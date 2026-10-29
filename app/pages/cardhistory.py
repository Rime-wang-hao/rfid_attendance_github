# -*- coding: utf-8 -*-
"""「卡片记录」页面：刷一张卡，立刻看到这张卡绑的是谁、以及它的全部签到/签退记录。

刷卡由主窗口转发进来（见 MainWindow._on_tag），也可以手动输入 EPC 查询。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QSizePolicy, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..database import Database
from ..widgets import Card, StatCard, StatusDot, reader_state_display

_TYPE_TEXT = {"in": "签到", "out": "签退", "unknown": "未注册 / 失效"}
_TYPE_COLOR = {"in": QColor("#D1FAE5"), "out": QColor("#FEF3C7"),
               "unknown": QColor("#F1F5F9")}


def _fmt_epc(epc: str) -> str:
    """每 2 个十六进制字符分隔，便于人眼核对。"""
    e = "".join(ch for ch in (epc or "").upper() if ch in "0123456789ABCDEF")
    return " ".join(e[i:i + 2] for i in range(0, len(e), 2))


class CardHistoryPage(QWidget):
    """卡片记录页。"""

    registerCardRequested = pyqtSignal(str)     # 请求去注册这张卡（参数：EPC）

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._epc = ""
        self._build_ui()
        self._show_empty()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("卡片记录")
        title.setObjectName("h1")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("读写器："))
        self._dot = StatusDot()
        header.addWidget(self._dot)
        self._reader_text = QLabel("未连接")
        self._reader_text.setObjectName("muted")
        header.addWidget(self._reader_text)
        self._inv_text = QLabel("· 感应区 0 张")
        self._inv_text.setObjectName("muted")
        header.addWidget(self._inv_text)
        self._dot.setState("off")
        root.addLayout(header)

        # ---- 当前卡片 ----
        info = Card()
        t = QLabel("当前卡片")
        t.setObjectName("cardTitle")
        info.addWidget(t)

        self._who = QLabel("👋  等待刷卡……")
        self._who.setStyleSheet("font-size:24px;font-weight:800;color:#94A3B8;")
        info.addWidget(self._who)

        self._meta = QLabel("")
        self._meta.setObjectName("muted")
        self._meta.setWordWrap(True)
        info.addWidget(self._meta)

        self._state = QLabel("")
        self._state.setWordWrap(True)
        self._state.setVisible(False)
        info.addWidget(self._state)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._epc_edit = QLineEdit()
        self._epc_edit.setPlaceholderText("也可以手动输入 EPC 后回车查询")
        self._epc_edit.returnPressed.connect(self._on_query)
        row.addWidget(self._epc_edit, 1)
        btn_q = QPushButton("查询")
        btn_q.setProperty("variant", "secondary")
        btn_q.clicked.connect(self._on_query)
        row.addWidget(btn_q)
        self._btn_reg = QPushButton("去注册这张卡")
        self._btn_reg.setProperty("variant", "secondary")
        self._btn_reg.clicked.connect(lambda: self.registerCardRequested.emit(self._epc))
        self._btn_reg.setVisible(False)
        row.addWidget(self._btn_reg)
        row.addStretch(1)
        info.addLayout(row)
        root.addWidget(info)

        # ---- 天线实时状态：读写器此刻到底读到了哪几张卡 ----
        inv_card = Card()
        it = QLabel("天线实时状态")
        it.setObjectName("cardTitle")
        inv_card.addWidget(it)
        self._inv_hint = QLabel("等待读写器……")
        self._inv_hint.setObjectName("muted")
        self._inv_hint.setWordWrap(True)
        inv_card.addWidget(self._inv_hint)
        self._inv_tbl = QTableWidget(0, 3)
        self._inv_tbl.setHorizontalHeaderLabels(["卡号 (EPC)", "状态", "绑定的人"])
        self._inv_tbl.verticalHeader().setVisible(False)
        self._inv_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._inv_tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._inv_tbl.setShowGrid(False)
        self._inv_tbl.setMaximumHeight(160)
        ih = self._inv_tbl.horizontalHeader()
        ih.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(2, QHeaderView.Stretch)
        inv_card.addWidget(self._inv_tbl)
        root.addWidget(inv_card)

        # ---- 累计统计 ----
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._st_total = StatCard("累计刷卡")
        self._st_in = StatCard("累计签到")
        self._st_out = StatCard("累计签退")
        for w in (self._st_total, self._st_in, self._st_out):
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            w.setMinimumHeight(96)
            stats.addWidget(w)
        root.addLayout(stats)

        # ---- 明细 ----
        list_card = Card()
        lt = QLabel("这张卡的全部记录")
        lt.setObjectName("cardTitle")
        list_card.addWidget(lt)
        self._tbl = QTableWidget(0, 5)
        self._tbl.setHorizontalHeaderLabels(["日期", "时间", "类型", "姓名", "学号"])
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl.setShowGrid(False)
        self._tbl.setAlternatingRowColors(True)
        h = self._tbl.horizontalHeader()
        for i in (0, 1, 2, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        list_card.addWidget(self._tbl, 1)
        root.addWidget(list_card, 1)

    # ------------------------------------------------------------ 公共 API
    def set_reader_state(self, state: str, text: str) -> None:
        self._dot.setState(state)
        short, tip = reader_state_display(state, text)
        self._reader_text.setText(short)
        self._reader_text.setToolTip(tip)

    def on_tag_read(self, epc: str) -> None:
        """主窗口读到一张卡就转到这里：立刻展示这张卡的档案和全部记录。"""
        self._load(epc)

    def reload(self) -> None:
        """切到本页时刷新（比如刚在别处注册了这张卡）。"""
        if self._epc:
            self._load(self._epc)

    def set_inventory(self, epcs) -> None:
        """读写器此刻读到的所有卡（实时）。

        这一栏是排查"刷卡没反应 / 认错人"的关键：
        它列出的是读写器**真实读到**的卡号，刷卡的判定完全以这里为准。
        """
        eps = [str(e) for e in (epcs or [])]
        self._inv_text.setText(f"· 感应区 {len(eps)} 张")
        if not eps:
            self._inv_hint.setText(
                "感应区里没有卡。把卡贴到读写器天线下方 5～15 cm，"
                "这里会立刻列出它真实读到的卡号。")
            self._inv_tbl.setRowCount(0)
            return
        self._inv_hint.setText(
            f"读写器此刻读到 {len(eps)} 张卡（刷卡只认下面这些卡号）：")
        self._inv_tbl.setRowCount(len(eps))
        for r, epc in enumerate(eps):
            stu = self._db.get_student_by_epc(epc)
            cells = [
                QTableWidgetItem(_fmt_epc(epc)),
                QTableWidgetItem("已注册" if stu else "未注册"),
                QTableWidgetItem(f"{stu.sid}　{stu.name}" if stu
                                 else "— 请去「学生注册」登记 —"),
            ]
            for it in cells:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            cells[1].setBackground(QColor("#D1FAE5") if stu else QColor("#FEE2E2"))
            for c, it in enumerate(cells):
                self._inv_tbl.setItem(r, c, it)

    # ------------------------------------------------------------ 内部
    def _on_query(self) -> None:
        epc = "".join(
            ch for ch in self._epc_edit.text().upper() if ch in "0123456789ABCDEF"
        )
        if not epc:
            return
        self._load(epc)

    def _show_empty(self) -> None:
        self._epc = ""
        self._who.setText("👋  等待刷卡……")
        self._who.setStyleSheet("font-size:24px;font-weight:800;color:#94A3B8;")
        self._meta.setText("刷任意一张卡即可查看它的全部签到 / 签退记录。")
        self._hide_state()
        self._btn_reg.setVisible(False)
        self._st_total.setValue(0)
        self._st_in.setValue(0)
        self._st_out.setValue(0)
        self._tbl.setRowCount(0)

    def _load(self, epc: str) -> None:
        epc = (epc or "").upper().strip()
        if not epc:
            return
        self._epc = epc
        student = self._db.get_student_by_epc(epc)
        stat = self._db.count_events_by_epc(epc)
        events = self._db.query_events_by_epc(epc, limit=1000)

        # 卡片档案
        if student:
            self._who.setText(f"{student.name}　{student.sid}".strip())
            self._who.setStyleSheet("font-size:24px;font-weight:800;color:#0F172A;")
            meta = [f"EPC：{_fmt_epc(epc)}"]
            if student.created_at:
                meta.append(f"登记时间：{student.created_at}")
            if student.note:
                meta.append(f"备注：{student.note}")
            if stat["last_ts"]:
                meta.append(f"最近刷卡：{stat['last_ts']}")
            self._meta.setText("　·　".join(meta))
            self._btn_reg.setVisible(False)
        else:
            self._who.setText("未注册卡片")
            self._who.setStyleSheet("font-size:24px;font-weight:800;color:#94A3B8;")
            self._meta.setText(
                f"EPC：{_fmt_epc(epc)}　·　这张卡还没有绑定任何人，"
                f"刷卡只会记为「未注册」，不会记考勤。"
            )
            self._btn_reg.setVisible(True)

        # 有效期
        ok, why = self._db.judge_card_validity(student) if student else (True, "")
        if student and not ok:
            self._show_state(f"⛔ {why}　—— 这张卡当前不能用于签到 / 签退。",
                             "#B91C1C", "#FEF2F2")
        elif student:
            f, t = self._db.get_validity_range()
            if f or t:
                self._show_state(
                    f"✓ 在有效期内（{f.isoformat() if f else '不限'} ~ "
                    f"{t.isoformat() if t else '不限'}）",
                    "#047857", "#ECFDF5",
                )
            else:
                self._hide_state()
        else:
            self._hide_state()

        # 统计
        self._st_total.setValue(stat["total"])
        self._st_in.setValue(stat["in"])
        self._st_out.setValue(stat["out"])

        # 明细（最新在上）
        self._tbl.setRowCount(len(events))
        for r, ev in enumerate(events):
            cells = [
                QTableWidgetItem(ev.ts.strftime("%Y-%m-%d")),
                QTableWidgetItem(ev.ts.strftime("%H:%M:%S")),
                QTableWidgetItem(_TYPE_TEXT.get(ev.event_type, ev.event_type)),
                QTableWidgetItem(ev.name or "—"),
                QTableWidgetItem(ev.sid or "—"),
            ]
            for it in cells:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            cells[2].setBackground(_TYPE_COLOR.get(ev.event_type, QColor("#FFFFFF")))
            for c, it in enumerate(cells):
                self._tbl.setItem(r, c, it)
        self._epc_edit.setText(epc)

    def _show_state(self, text: str, fg: str, bg: str) -> None:
        self._state.setText(text)
        self._state.setStyleSheet(
            f"color:{fg};background:{bg};border-radius:8px;padding:8px 12px;font-size:12px;"
            "font-weight:600;"
        )
        self._state.setVisible(True)

    def _hide_state(self) -> None:
        self._state.setVisible(False)
