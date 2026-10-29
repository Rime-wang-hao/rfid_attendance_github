# -*- coding: utf-8 -*-
"""「考勤记录」页面。"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from typing import List, Optional

from PyQt5.QtCore import QDate, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QDateEdit, QFileDialog,
                             QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..database import AttendanceEvent, Database
from ..widgets import Card


class RecordsPage(QWidget):
    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        title = QLabel("考勤记录")
        title.setObjectName("h1")
        root.addWidget(title)

        # 工具栏
        bar = QHBoxLayout()
        bar.addWidget(QLabel("开始日期："))
        self._d_from = QDateEdit(QDate.currentDate())
        self._d_from.setCalendarPopup(True)
        self._d_from.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self._d_from)
        bar.addSpacing(12)
        bar.addWidget(QLabel("结束日期："))
        self._d_to = QDateEdit(QDate.currentDate())
        self._d_to.setCalendarPopup(True)
        self._d_to.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self._d_to)

        btn_query = QPushButton("查询")
        btn_query.clicked.connect(self._reload)
        bar.addSpacing(8)
        bar.addWidget(btn_query)

        btn_today = QPushButton("只看今天")
        btn_today.setProperty("variant", "secondary")
        btn_today.clicked.connect(self._on_today)
        bar.addWidget(btn_today)

        bar.addStretch(1)
        self._btn_export = QPushButton("导出 CSV")
        self._btn_export.setProperty("variant", "secondary")
        self._btn_export.clicked.connect(self._on_export)
        bar.addWidget(self._btn_export)

        btn_clear = QPushButton("清空今日")
        btn_clear.setProperty("variant", "danger")
        btn_clear.clicked.connect(self._on_clear_today)
        bar.addWidget(btn_clear)
        root.addLayout(bar)

        # 表格
        self._tbl = QTableWidget(0, 5)
        self._tbl.setHorizontalHeaderLabels(["时间", "学号", "姓名", "类型", "EPC"])
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tbl.setShowGrid(False)
        self._tbl.setAlternatingRowColors(True)
        h = self._tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        root.addWidget(self._tbl, 1)

        self._lbl_summary = QLabel("")
        self._lbl_summary.setObjectName("muted")
        root.addWidget(self._lbl_summary)

    def _on_today(self) -> None:
        self._d_from.setDate(QDate.currentDate())
        self._d_to.setDate(QDate.currentDate())
        self._reload()

    def _on_clear_today(self) -> None:
        ans = QMessageBox.question(
            self, "确认", "清空今天的全部考勤记录？此操作不可恢复。",
        )
        if ans == QMessageBox.Yes:
            n = self._db.clear_today_events()
            QMessageBox.information(self, "完成", f"已删除 {n} 条今日记录。")
            self._reload()

    def _on_export(self) -> None:
        events = self._current_events
        if not events:
            QMessageBox.information(self, "提示", "当前查询结果为空，没有可导出的数据。")
            return
        default = f"考勤记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", default, "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["时间", "学号", "姓名", "类型", "EPC"])
                for e in events:
                    w.writerow([
                        e.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        e.sid, e.name,
                        {"in": "签到", "out": "签退"}.get(e.event_type, "未知"),
                        e.epc,
                    ])
            QMessageBox.information(self, "完成", f"已导出到：\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "失败", f"导出失败：{e}")

    def _reload(self) -> None:
        d_from = self._d_from.date().toPyDate()
        d_to = self._d_to.date().toPyDate()
        events = self._db.query_events(d_from=d_from, d_to=d_to, limit=5000)
        events.sort(key=lambda e: e.ts, reverse=True)
        self._current_events = events
        self._fill(events)
        self._lbl_summary.setText(
            f"共 {len(events)} 条记录"
            + (f"  ·  已签到 {sum(1 for e in events if e.event_type=='in')} 次"
               f"  ·  已签退 {sum(1 for e in events if e.event_type=='out')} 次"
               if events else "")
        )

    def _fill(self, events: List[AttendanceEvent]) -> None:
        self._tbl.setRowCount(len(events))
        color_map = {"in": QColor("#D1FAE5"), "out": QColor("#FEF3C7"),
                     "unknown": QColor("#F1F5F9")}
        label_map = {"in": "签到", "out": "签退", "unknown": "未知"}
        for r, e in enumerate(events):
            ts = QTableWidgetItem(e.ts.strftime("%Y-%m-%d %H:%M:%S"))
            sid = QTableWidgetItem(e.sid or "—")
            name = QTableWidgetItem(e.name or "—")
            tp = QTableWidgetItem(label_map.get(e.event_type, "—"))
            epc = QTableWidgetItem(e.epc or "—")
            for it in (ts, sid, name, tp, epc):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            tp.setBackground(color_map.get(e.event_type, QColor("#FFFFFF")))
            self._tbl.setItem(r, 0, ts)
            self._tbl.setItem(r, 1, sid)
            self._tbl.setItem(r, 2, name)
            self._tbl.setItem(r, 3, tp)
            self._tbl.setItem(r, 4, epc)
