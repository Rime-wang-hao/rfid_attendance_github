# -*- coding: utf-8 -*-
"""「学生注册」页面：刷卡读取 EPC → 填写学号/姓名 → 绑定并入库。

流程：
    1. 点「开始读取卡片」，把标签靠近天线，程序抓到 EPC 后自动停止读取
       （单次抓取，避免卡片一直放着被反复读取）；也可以手动输入 EPC。
    2. 填写学号、姓名、备注。
    3. 点「保存并注册」——若该卡或该学号已存在，会弹窗确认是否覆盖/改绑。
    4. 注册成功立即出现在「人员管理」中。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QAbstractItemView, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..database import Database, EpcConflictError
from ..widgets import Card, StatusDot, reader_state_display

_EPC_BOX_BASE = (
    "QLineEdit{{background:{bg};border:1px dashed {border};border-radius:12px;"
    "font-family:'Cascadia Mono','Consolas',monospace;font-size:19px;"
    "font-weight:700;letter-spacing:1px;padding:16px;color:{fg};}}"
)
_EPC_BOX_IDLE = _EPC_BOX_BASE.format(bg="#F1F5F9", border="#CBD5E1", fg="#0F172A")
_EPC_BOX_WAIT = _EPC_BOX_BASE.format(bg="#FEF3C7", border="#F59E0B", fg="#B45309")
_EPC_BOX_DONE = _EPC_BOX_BASE.format(bg="#ECFDF5", border="#10B981", fg="#047857")


def _fmt_epc(epc: str) -> str:
    """每 2 个十六进制字符分隔，便于人眼核对。"""
    e = "".join(ch for ch in epc if ch.strip().upper() in "0123456789ABCDEF")
    return " ".join(e[i:i + 2] for i in range(0, len(e), 2))


class RegisterPage(QWidget):
    """学生注册页。"""

    studentRegistered = pyqtSignal(str)    # 注册成功（参数：姓名）
    gotoPeopleRequested = pyqtSignal()     # 请求切到「人员管理」
    #: 请求给天线上的卡写入新卡号（发卡）。参数：新 EPC
    writeEpcRequested = pyqtSignal(str)

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._capturing = False
        self._inventory: list[str] = []   # 读写器此刻读到的所有卡
        self._build_ui()
        self._reload_recent()
        self._refresh_epc_style()
        self._regen_epc()

        # 读卡超时保护：忘了点「停止读取」也最多等 2 分钟，
        # 否则这个页面会一直吞掉刷卡，实时考勤页就再也收不到卡。
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(120000)
        self._capture_timer.timeout.connect(self._on_capture_timeout)

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        # 标题 + 读写器状态
        header = QHBoxLayout()
        title = QLabel("学生注册")
        title.setObjectName("h1")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("读写器："))
        self._dot = StatusDot()
        header.addWidget(self._dot)
        self._reader_text = QLabel("未连接")
        self._reader_text.setObjectName("muted")
        header.addWidget(self._reader_text)
        self._dot.setState("off")
        root.addLayout(header)

        # 上：读卡（左） + 填表（右）
        top = QHBoxLayout()
        top.setSpacing(16)

        # ---- 左：读卡 ----
        scan_card = Card()
        st = QLabel("① 读取卡片")
        st.setObjectName("cardTitle")
        scan_card.addWidget(st)

        self._epc_edit = QLineEdit()
        self._epc_edit.setAlignment(Qt.AlignCenter)
        self._epc_edit.setMinimumHeight(76)
        self._epc_edit.setPlaceholderText("等待刷卡……")
        self._epc_edit.setToolTip("刷卡后自动填入，也可以手动输入 / 粘贴 EPC")
        self._epc_edit.textChanged.connect(self._on_epc_changed)
        scan_card.addWidget(self._epc_edit)

        self._scan_hint = QLabel("把标签靠近天线 5～15 cm 后点下面的按钮开始读取。")
        self._scan_hint.setObjectName("muted")
        self._scan_hint.setWordWrap(True)
        scan_card.addWidget(self._scan_hint)

        btns = QHBoxLayout()
        self._btn_scan = QPushButton("开始读取卡片")
        self._btn_scan.clicked.connect(self._toggle_capture)
        self._btn_clear = QPushButton("清空")
        self._btn_clear.setProperty("variant", "secondary")
        self._btn_clear.clicked.connect(self._reset)
        btns.addWidget(self._btn_scan)
        btns.addWidget(self._btn_clear)
        btns.addStretch(1)
        scan_card.addLayout(btns)

        self._bind_hint = QLabel("")
        self._bind_hint.setWordWrap(True)
        self._bind_hint.setVisible(False)
        scan_card.addWidget(self._bind_hint)
        scan_card.addStretch(1)

        # ---- 右：填表 ----
        form_card = Card()
        ft = QLabel("② 填写学生信息")
        ft.setObjectName("cardTitle")
        form_card.addWidget(ft)

        form = QFormLayout()
        form.setSpacing(12)
        self._sid = QLineEdit()
        self._sid.setPlaceholderText("如 20250101（必填）")
        self._name = QLineEdit()
        self._name.setPlaceholderText("如 张三（必填）")
        self._note = QLineEdit()
        self._note.setPlaceholderText("选填，如 高一(3)班")
        form.addRow("学号 / 工号", self._sid)
        self._sid.textChanged.connect(self._on_sid_changed)
        form.addRow("姓名", self._name)
        form.addRow("备注", self._note)
        form_card.addLayout(form)

        save_bar = QHBoxLayout()
        self._btn_save = QPushButton("保存并注册")
        self._btn_save.clicked.connect(self._on_save)
        btn_people = QPushButton("查看人员管理")
        btn_people.setProperty("variant", "secondary")
        btn_people.clicked.connect(self.gotoPeopleRequested.emit)
        save_bar.addWidget(self._btn_save)
        save_bar.addWidget(btn_people)
        save_bar.addStretch(1)
        form_card.addLayout(save_bar)

        self._msg = QLabel("")
        self._msg.setWordWrap(True)
        self._msg.setVisible(False)
        form_card.addWidget(self._msg)
        form_card.addStretch(1)

        # ---- 左列：读卡 + 发卡 ----
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(scan_card, 3)

        # ---- 发卡：给空白卡写入一个独一无二的卡号 ----
        issue_card = Card()
        it = QLabel("🆔 发卡：给空白卡写入专属卡号")
        it.setObjectName("cardTitle")
        issue_card.addWidget(it)

        issue_tip = QLabel(
            "空白 UHF 卡出厂卡号往往完全相同，多张卡刷出来一模一样，"
            "于是谁刷都显示成同一个人。\n"
            "发卡 = 给这张卡烧一个独一无二的卡号，从此一卡一人。"
        )
        issue_tip.setObjectName("muted")
        issue_tip.setWordWrap(True)
        issue_card.addWidget(issue_tip)

        self._issue_inv = QLabel("感应区：等待读写器…")
        self._issue_inv.setObjectName("muted")
        self._issue_inv.setWordWrap(True)
        issue_card.addWidget(self._issue_inv)

        epc_row = QHBoxLayout()
        self._new_epc_edit = QLineEdit()
        self._new_epc_edit.setAlignment(Qt.AlignCenter)
        self._new_epc_edit.setPlaceholderText("新卡号（自动生成，可修改）")
        btn_gen = QPushButton("🎲 重新生成")
        btn_gen.setProperty("variant", "secondary")
        btn_gen.clicked.connect(self._regen_epc)
        epc_row.addWidget(self._new_epc_edit, 1)
        epc_row.addWidget(btn_gen)
        issue_card.addLayout(epc_row)

        self._btn_issue = QPushButton("✍ 写入卡号")
        self._btn_issue.setToolTip(
            "把天线上这张卡的卡号改写为上面那个新卡号（不可撤销）")
        self._btn_issue.clicked.connect(self._on_issue)
        issue_card.addWidget(self._btn_issue)

        self._issue_msg = QLabel("")
        self._issue_msg.setWordWrap(True)
        self._issue_msg.setVisible(False)
        issue_card.addWidget(self._issue_msg)
        issue_card.addStretch(1)

        left.addWidget(issue_card, 2)
        top.addLayout(left, 5)
        top.addWidget(form_card, 4)
        root.addLayout(top)

        # 下：最近注册
        recent_card = Card()
        rt = QLabel("最近注册")
        rt.setObjectName("cardTitle")
        recent_card.addWidget(rt)
        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["学号 / 工号", "姓名", "EPC 标签", "注册 / 更新时间"])
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl.setShowGrid(False)
        self._tbl.setAlternatingRowColors(True)
        h = self._tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        recent_card.addWidget(self._tbl, 1)
        root.addWidget(recent_card, 1)

        # 回车即保存
        for w in (self._sid, self._name, self._note):
            w.returnPressed.connect(self._on_save)

    # ---------------------------------------------------------- 读卡逻辑
    def _toggle_capture(self) -> None:
        if self._capturing:
            self._stop_capture("")
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        self._capturing = True
        self._btn_scan.setText("停止读取")
        self._scan_hint.setText("正在读取…… 请把标签靠近天线。读到后会自动停止。")
        self._refresh_epc_style()
        self._capture_timer.start()

    def _stop_capture(self, hint: str = "") -> None:
        self._capturing = False
        self._capture_timer.stop()
        self._btn_scan.setText("开始读取卡片")
        self._scan_hint.setText(hint or "把标签靠近天线 5～15 cm 后点下面的按钮开始读取。")
        self._refresh_epc_style()

    def _on_capture_timeout(self) -> None:
        """2 分钟没读到卡自动停：避免「正在读取」挂着不放，一直吞刷卡。"""
        if self._capturing:
            self._stop_capture(
                "已等待 2 分钟仍未读到卡片，已自动停止读取。\n"
                "请确认读写器已连接、天线已接好，再点「开始读取卡片」。"
            )

    def stop_capture(self) -> None:
        """供外部调用（切换页面 / 关闭窗口）：立刻停止读卡。

        不停的话，本页会继续消费每一次刷卡，实时考勤页完全收不到卡 ——
        表现就是刷卡没反应、也不提示「该卡未注册」。
        """
        if self._capturing:
            self._stop_capture("已停止读取（离开了注册页）。")

    def hideEvent(self, event) -> None:
        """页面被切走（QStackedWidget 会 hide 非当前页）时自动停止读卡。"""
        self.stop_capture()
        super().hideEvent(event)

    def on_tag_read(self, epc: str) -> bool:
        """主窗口读到标签时调用。返回 True 表示本次刷卡已被本页消费（用于注册）。"""
        if not self._capturing:
            return False
        raw = "".join(ch for ch in epc.upper() if ch in "0123456789ABCDEF")
        self._epc_edit.setText(raw or epc.strip().upper())
        self._stop_capture("✓ 已读到卡片，核对下方信息后保存。")
        self._name.setFocus()
        return True

    def set_reader_state(self, state: str, text: str) -> None:
        self._dot.setState(state)
        short, tip = reader_state_display(state, text)
        self._reader_text.setText(short)
        self._reader_text.setToolTip(tip)

    def set_epc(self, epc: str) -> None:
        """外部直接带入一张卡（如「卡片记录」页的「去注册这张卡」）。"""
        raw = "".join(ch for ch in (epc or "").upper() if ch in "0123456789ABCDEF")
        self._epc_edit.setText(raw)
        self._stop_capture("")
        self._sid.setFocus()

    def _current_epc(self) -> str:
        return "".join(
            ch for ch in self._epc_edit.text().upper()
            if ch in "0123456789ABCDEF"
        )

    def _on_epc_changed(self) -> None:
        self._refresh_epc_style()
        self._refresh_hints()

    def _on_sid_changed(self) -> None:
        self._refresh_hints()

    def _refresh_epc_style(self) -> None:
        if self._capturing:
            self._epc_edit.setStyleSheet(_EPC_BOX_WAIT)
        elif self._current_epc():
            self._epc_edit.setStyleSheet(_EPC_BOX_DONE)
        else:
            self._epc_edit.setStyleSheet(_EPC_BOX_IDLE)

    def _refresh_hints(self) -> None:
        """同时检查「卡号」和「学号」两处冲突，在点保存之前就提示出来。

        之前只检查卡号：学号被占用时屏幕上没有任何提示，直到点保存才弹窗，
        一不注意点「是」，新卡就把旧卡顶掉了 —— 表现出来就是"所有新卡都叫同一个人"。
        """
        epc = self._current_epc()
        sid = self._sid.text().strip()
        by_epc = self._db.get_student_by_epc(epc) if epc else None
        by_sid = self._db.get_student_by_sid(sid) if sid else None

        warn = False
        lines = []
        if by_epc:
            warn = True
            lines.append(f"⚠ 这张卡已经绑定给「{by_epc.sid} {by_epc.name}」，"
                         f"保存将覆盖原绑定信息。")
        elif epc:
            lines.append(f"新卡片 · {_fmt_epc(epc)}")

        # 学号被别的卡占用 —— 这是"新卡变旧人"的真正入口
        if by_sid and not (by_epc and by_epc.id == by_sid.id):
            warn = True
            old = _fmt_epc(by_sid.epc or "") or "（未绑卡）"
            lines.append(
                f"⚠ 学号「{sid}」已被「{by_sid.name}」占用，"
                f"它绑的卡是 {old}。\n"
                f"　　要给新同学发卡，请换一个没被用过的学号；"
                f"沿用旧学号会把这张旧卡顶掉。"
            )
        if not lines:
            self._hide_hint()
            return
        if warn:
            self._show_hint("\n".join(lines), "#B45309", "#FEF3C7")
        else:
            self._show_hint("\n".join(lines), "#047857", "#ECFDF5")

    def _show_hint(self, text: str, fg: str, bg: str) -> None:
        self._bind_hint.setText(text)
        self._bind_hint.setStyleSheet(
            f"color:{fg};background:{bg};border-radius:8px;padding:8px 12px;font-size:12px;"
        )
        self._bind_hint.setVisible(True)

    def _hide_hint(self) -> None:
        self._bind_hint.setVisible(False)

    def _flash(self, text: str, ok: bool = True) -> None:
        self._msg.setText(text)
        self._msg.setStyleSheet(
            ("color:#047857;background:#ECFDF5;" if ok else "color:#B91C1C;background:#FEF2F2;")
            + "border-radius:8px;padding:10px 12px;font-weight:600;"
        )
        self._msg.setVisible(True)

    # ---------------------------------------------------------- 发卡逻辑
    def _regen_epc(self) -> None:
        """生成一个库里没有的全新卡号。"""
        try:
            self._new_epc_edit.setText(self._db.generate_unique_epc())
        except Exception:
            self._new_epc_edit.setText("")

    def set_inventory(self, epcs) -> None:
        """主窗口转发：读写器此刻读到的所有卡。写卡必须确认只有一张卡。"""
        self._inventory = [str(e).upper() for e in (epcs or [])]
        n = len(self._inventory)
        if n == 0:
            self._issue_inv.setText("感应区：没有卡")
            self._issue_inv.setStyleSheet("color:#94A3B8;")
        elif n == 1:
            self._issue_inv.setText(f"感应区：1 张卡　{_fmt_epc(self._inventory[0])}")
            self._issue_inv.setStyleSheet("color:#047857;")
        else:
            self._issue_inv.setText(
                f"⚠ 感应区有 {n} 张卡 —— 写卡只对一张生效，请先移走多余的卡")
            self._issue_inv.setStyleSheet("color:#B91C1C;")

    def _issue_flash(self, text: str, ok: bool = True) -> None:
        self._issue_msg.setText(text)
        self._issue_msg.setStyleSheet(
            ("color:#047857;background:#ECFDF5;" if ok else "color:#B91C1C;background:#FEF2F2;")
            + "border-radius:8px;padding:10px 12px;font-weight:600;"
        )
        self._issue_msg.setVisible(True)

    def _on_issue(self) -> None:
        """发卡：把天线上的这张卡改写成上面的新卡号。"""
        new_epc = "".join(
            ch for ch in self._new_epc_edit.text().upper()
            if ch in "0123456789ABCDEF"
        )
        if not new_epc or len(new_epc) % 2:
            self._issue_flash("新卡号必须是偶数位十六进制！", ok=False)
            return
        if not (8 <= len(new_epc) <= 48):
            self._issue_flash("新卡号长度需在 4~24 字节之间。", ok=False)
            return
        if self._db.get_student_by_epc(new_epc):
            self._issue_flash("这个新卡号库里已经有人用了，请点「重新生成」。", ok=False)
            return

        old_epc = self._current_epc()
        ans = QMessageBox.question(
            self, "确认发卡",
            f"将把天线上的卡片改写为新卡号：\n\n"
            f"　　原卡号：{_fmt_epc(old_epc) if old_epc else '（未读取到）'}\n"
            f"　　新卡号：{_fmt_epc(new_epc)}\n\n"
            f"⚠ 写卡后原卡号立即失效，不可撤销。\n"
            f"⚠ 感应区必须只有一张卡，否则可能写错卡。\n\n"
            f"确定写入吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._btn_issue.setEnabled(False)
        self._btn_issue.setText("写入中…")
        self.writeEpcRequested.emit(new_epc)

    def on_epc_written(self, ok: bool, epc: str, msg: str) -> None:
        """主窗口转发：写卡结果。"""
        self._btn_issue.setEnabled(True)
        self._btn_issue.setText("✍ 写入卡号")
        if ok:
            # 新卡号直接填进上面的卡号框，接着填学号姓名保存即可
            self.set_epc(epc)
            self._issue_flash(
                f"✓ {msg}\n现在填写学号和姓名，点「保存并注册」完成发卡登记。", ok=True)
            self._name.setFocus()
        else:
            self._issue_flash(f"✗ 写入失败：{msg}", ok=False)

    # ---------------------------------------------------------- 保存
    def _on_save(self) -> None:
        epc = self._current_epc()
        sid = self._sid.text().strip()
        name = self._name.text().strip()
        note = self._note.text().strip()

        if not epc:
            self._flash("请先点「开始读取卡片」刷一次卡，或手动填写 EPC。", ok=False)
            return
        if not sid:
            self._flash("学号 / 工号不能为空。", ok=False)
            self._sid.setFocus()
            return
        if not name:
            self._flash("姓名不能为空。", ok=False)
            self._name.setFocus()
            return

        by_epc = self._db.get_student_by_epc(epc)
        by_sid = self._db.get_student_by_sid(sid)
        target_id: Optional[int] = None
        allow_rebind = False

        if by_epc and by_sid and by_epc.id != by_sid.id:
            # 卡属于 A，学号属于 B —— 需要合并
            ans = QMessageBox.question(
                self, "信息冲突",
                f"这张卡已绑定给：{by_epc.sid} {by_epc.name}\n"
                f"学号 {sid} 已属于：{by_sid.sid} {by_sid.name}\n\n"
                f"是否把卡片改绑到「{sid} {name}」？\n"
                f"⚠ 原卡主将失去这张卡（{by_epc.epc} 会变成未注册）。",
            )
            if ans != QMessageBox.Yes:
                return
            target_id = by_sid.id
            allow_rebind = True
        elif by_epc:
            if by_epc.sid == sid and by_epc.name == name:
                self._flash(f"该卡已经注册过了：{sid} {name}，无需重复注册。", ok=False)
                return
            ans = QMessageBox.question(
                self, "确认覆盖",
                f"这张卡当前绑定的是：{by_epc.sid}　{by_epc.name}\n"
                f"确定改成「{sid}　{name}」？\n"
                f"⚠ 改绑后，{by_epc.name} 就失去这张卡，这张卡对他失效，"
                f"不能再刷考勤。\n"
                f"　 如果 {by_epc.name} 还要用这张卡，请点「否」。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,      # ★ 默认否
            )
            if ans != QMessageBox.Yes:
                return
            target_id = by_epc.id
            allow_rebind = True
        elif by_sid:
            old_epc = _fmt_epc(by_sid.epc or "") or "（未绑卡）"
            ans = QMessageBox.question(
                self, "学号已存在",
                f"学号「{sid}」已属于：{by_sid.sid}　{by_sid.name}\n"
                f"该学号当前绑定的卡片：{old_epc}\n\n"
                f"确定把这张新卡绑给「{sid}　{name}」吗？\n"
                f"⚠ 绑定后上面那张旧卡会失效，不能再刷考勤。\n"
                f"⚠ 如果是另一位同学，请点「否」，换一个学号再来 ——\n"
                f"　　沿用旧学号会把旧卡顶掉，新卡就会顶着别人的名字。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,      # ★ 默认否：别一不小心就把别人的卡抢了
            )
            if ans != QMessageBox.Yes:
                return
            target_id = by_sid.id
            allow_rebind = True

        try:
            self._db.upsert_student(sid=sid, name=name, epc=epc, note=note,
                                    student_id=target_id, allow_rebind=allow_rebind)
        except EpcConflictError as e:
            self._flash(
                f"卡号 {_fmt_epc(e.epc)} 已经绑定给 {e.owner_display}，"
                f"没有覆盖对方。请换一张卡或先处理原绑定。", ok=False)
            return
        except Exception as e:
            self._flash(f"保存失败：{e}", ok=False)
            return

        self._flash(f"✓ 注册成功：{sid}  {name}   EPC {_fmt_epc(epc)}")
        self.studentRegistered.emit(name)
        self._reset(keep_msg=True)
        self._reload_recent()

    def _reset(self, keep_msg: bool = False) -> None:
        self._stop_capture("")
        self._epc_edit.clear()
        self._sid.clear()
        self._name.clear()
        self._note.clear()
        self._hide_hint()
        if not keep_msg:
            self._msg.setVisible(False)
        self._sid.setFocus()

    # ---------------------------------------------------------- 最近注册
    def reload(self) -> None:
        self._reload_recent()

    def _reload_recent(self) -> None:
        rows = self._db.list_recent_students(12)
        self._tbl.setRowCount(len(rows))
        for r, s in enumerate(rows):
            cells = [
                QTableWidgetItem(s.get("sid") or "—"),
                QTableWidgetItem(s.get("name") or "—"),
                QTableWidgetItem(s.get("epc") or ""),
                QTableWidgetItem((s.get("updated_at") or s.get("created_at") or "")[:19]),
            ]
            for it in cells:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            cells[2].setToolTip(s.get("epc") or "")
            for c, it in enumerate(cells):
                self._tbl.setItem(r, c, it)
