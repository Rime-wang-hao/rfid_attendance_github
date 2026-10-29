# -*- coding: utf-8 -*-
"""「人员管理」页面。"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..database import Database, EpcConflictError, Student
from ..widgets import Card


def _fmt_epc(epc: str) -> str:
    """每 2 个十六进制字符分隔，便于人眼核对。"""
    e = "".join(ch for ch in (epc or "").upper() if ch in "0123456789ABCDEF")
    return " ".join(e[i:i + 2] for i in range(0, len(e), 2))


class StudentDialog(QDialog):
    """新增 / 编辑 人员对话框。"""

    def __init__(self, db: Database, student: Optional[Student] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._student = student
        self.setWindowTitle("编辑人员" if student else "新增人员")
        self.setMinimumWidth(420)
        self.setStyleSheet("QDialog { background: #F8FAFC; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("编辑人员信息" if student else "新增人员")
        title.setObjectName("h2")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        self._sid = QLineEdit(student.sid if student else "")
        self._sid.setPlaceholderText("如 20250101")
        self._name = QLineEdit(student.name if student else "")
        self._name.setPlaceholderText("如 张三")
        self._epc = QLineEdit(student.epc if student else "")
        self._epc.setPlaceholderText("可留空，由「刷卡绑定」自动填入")
        self._note = QLineEdit(student.note if student else "")
        form.addRow("学号 / 工号", self._sid)
        form.addRow("姓名", self._name)
        form.addRow("EPC 标签", self._epc)
        form.addRow("备注", self._note)
        layout.addLayout(form)

        hint = QLabel("说明：给新学生发卡请用「学生注册」页刷卡录入；此处适合批量修改或补录已有的学号 / 姓名。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("保存")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_save(self) -> None:
        sid = self._sid.text().strip()
        name = self._name.text().strip()
        if not sid or not name:
            QMessageBox.warning(self, "提示", "学号和姓名不能为空！")
            return
        epc = self._epc.text().strip() or None
        student_id = self._student.id if self._student else None
        allow_rebind = False

        # ★ 这张卡是不是已经绑给别人了？必须问清楚，不能默默顶掉。
        #   默默改绑正是"新卡一刷就显示成别人"的老毛病。
        if epc:
            owner = self._db.get_student_by_epc(epc)
            if owner and owner.id != student_id:
                ans = QMessageBox.question(
                    self, "这张卡已经有主人了",
                    f"卡号 {_fmt_epc(epc)} 已经绑定给：\n"
                    f"　　{owner.sid}　{owner.name}\n\n"
                    f"确定要把这张卡改绑给「{sid}　{name}」吗？\n"
                    f"⚠ 改绑后，{owner.name} 就失去这张卡，不能再刷考勤。\n"
                    f"　 如果这是另一位同学，请点「否」。",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,      # ★ 默认否
                )
                if ans != QMessageBox.Yes:
                    return
                allow_rebind = True

        try:
            self._db.upsert_student(
                sid=sid, name=name, epc=epc, note=self._note.text().strip(),
                student_id=student_id, allow_rebind=allow_rebind,
            )
        except EpcConflictError as e:
            QMessageBox.warning(
                self, "卡号冲突",
                f"卡号 {_fmt_epc(e.epc)} 已经绑定给 {e.owner_display}，\n"
                f"没有覆盖对方。请换一个卡号，或先到「学生注册」处理。")
            return
        except Exception as e:
            QMessageBox.critical(self, "失败", f"保存失败：{e}")
            return
        self.accept()


class PeoplePage(QWidget):
    """人员管理页——表格 CRUD + 搜索。"""

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        # 标题
        title = QLabel("人员管理")
        title.setObjectName("h1")
        root.addWidget(title)

        # 工具栏
        bar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索 学号 / 姓名 / EPC")
        self._search.setMaximumWidth(360)
        self._search.textChanged.connect(self._reload)
        bar.addWidget(self._search)
        bar.addStretch(1)

        self._btn_add = QPushButton("＋  新增")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit = QPushButton("编辑")
        self._btn_edit.setProperty("variant", "secondary")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del = QPushButton("删除")
        self._btn_del.setProperty("variant", "danger")
        self._btn_del.clicked.connect(self._on_delete)
        self._btn_clear = QPushButton("清空 EPC")
        self._btn_clear.setProperty("variant", "secondary")
        self._btn_clear.clicked.connect(self._on_clear_epc)
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_del)
        bar.addWidget(self._btn_clear)
        root.addLayout(bar)

        # 表格
        self._tbl = QTableWidget(0, 5)
        self._tbl.setHorizontalHeaderLabels(["学号 / 工号", "姓名", "EPC 标签", "备注", "更新时间"])
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl.setShowGrid(False)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.doubleClicked.connect(self._on_edit)
        h = self._tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        root.addWidget(self._tbl, 1)

    def reload(self) -> None:
        """外部（如「学生注册」页注册成功后）调用，刷新列表。"""
        self._reload()

    def _reload(self) -> None:
        q = self._search.text().strip().lower()
        students = self._db.list_students()
        if q:
            students = [s for s in students
                        if q in s.sid.lower() or q in s.name.lower()
                        or (s.epc and q in s.epc.lower())]
        self._fill(students)

    def _fill(self, students: List[Student]) -> None:
        self._tbl.setRowCount(len(students))
        for r, s in enumerate(students):
            sid = QTableWidgetItem(s.sid or "—")
            name = QTableWidgetItem(s.name or "—")
            epc = QTableWidgetItem(s.epc or "")
            epc.setToolTip(s.epc or "")
            note = QTableWidgetItem(s.note or "")
            ts = QTableWidgetItem(s.updated_at or "")
            for it in (sid, name, epc, note, ts):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setData(Qt.UserRole, s.id)
            self._tbl.setItem(r, 0, sid)
            self._tbl.setItem(r, 1, name)
            self._tbl.setItem(r, 2, epc)
            self._tbl.setItem(r, 3, note)
            self._tbl.setItem(r, 4, ts)

    def _selected_student(self) -> Optional[Student]:
        rows = self._tbl.selectionModel().selectedRows()
        if not rows:
            return None
        r = rows[0].row()
        sid_item = self._tbl.item(r, 0)
        if not sid_item:
            return None
        student_id = sid_item.data(Qt.UserRole)
        return self._db.get_student_by_id(student_id)

    def _on_add(self) -> None:
        dlg = StudentDialog(self._db, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self._reload()

    def _on_edit(self) -> None:
        s = self._selected_student()
        if not s:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        dlg = StudentDialog(self._db, student=s, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self._reload()

    def _on_delete(self) -> None:
        s = self._selected_student()
        if not s:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        ans = QMessageBox.question(
            self, "确认删除",
            f"确定删除「{s.display}」？\n该人员的考勤记录仍会保留。",
        )
        if ans == QMessageBox.Yes:
            self._db.delete_student(s.id)
            self._reload()

    def _on_clear_epc(self) -> None:
        s = self._selected_student()
        if not s:
            QMessageBox.information(self, "提示", "请先选中一行")
            return
        if not s.epc:
            QMessageBox.information(self, "提示", "该人员没有绑定 EPC")
            return
        ans = QMessageBox.question(
            self, "确认", f"清空「{s.display}」的 EPC 绑定？",
        )
        if ans == QMessageBox.Yes:
            self._db.upsert_student(sid=s.sid, name=s.name, epc=None,
                                     note=s.note, student_id=s.id)
            self._reload()
