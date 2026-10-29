# -*- coding: utf-8 -*-
"""「实时考勤」页面。"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import QDateTime, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                             QSizePolicy, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..database import AttendanceEvent, Database
from ..voice import get_engine
from ..widgets import Card, HeroDisplay, StatCard, StatusDot, reader_state_display


def _fmt_epc(epc: str) -> str:
    """每 2 个十六进制字符分隔，便于人眼核对。"""
    e = "".join(ch for ch in (epc or "").upper() if ch in "0123456789ABCDEF")
    return " ".join(e[i:i + 2] for i in range(0, len(e), 2))


class LivePage(QWidget):
    """实时考勤页——显示当前刷卡人、今日统计、今日记录。"""

    #: 刷到未注册的卡时，用户点「去注册这张卡」→ 主窗口跳到「学生注册」页
    registerCardRequested = pyqtSignal(str)

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._cooldowns: dict[str, float] = {}
        self._cooldown_sec = 5    # 同一张卡 N 秒内只触发一次（防多读）
        self._min_gap_sec = 0.0   # 签到 → 签退的最小间隔（秒），0 = 不限制
        self._reader_state = "off"   # 读写器连接状态，供时钟刷新时拼接文案
        self._reader_detail = ""     # 完整状态信息（放 tooltip）
        self._inventory: List[str] = []   # 天线内当前所有标签（实时）
        self._build_ui()
        self._apply_settings()
        self._reload_today()

        # 每 5 秒自动刷新今日统计
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._reload_today)
        self._refresh_timer.start()

        # 实时显示时钟
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        # 顶部：标题 + 状态点
        header = QHBoxLayout()
        title = QLabel("实时考勤")
        title.setObjectName("h1")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("读写器："))
        self._status_dot = StatusDot()
        header.addWidget(self._status_dot)
        self._status_text = QLabel("未连接")
        self._status_text.setObjectName("muted")
        header.addWidget(self._status_text)
        self._inv_text = QLabel("· 感应区 0 张")
        self._inv_text.setObjectName("muted")
        header.addWidget(self._inv_text)
        root.addLayout(header)

        # 统计三连
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._stat_total = StatCard("已登记人员")
        self._stat_in = StatCard("今日已签到")
        self._stat_out = StatCard("今日已签退")
        for w in (self._stat_total, self._stat_in, self._stat_out):
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            w.setMinimumHeight(96)
            stats.addWidget(w)
        root.addLayout(stats)

        # 英雄区 + 今日记录
        middle = QHBoxLayout()
        middle.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(16)
        self._hero = HeroDisplay()
        self._hero.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._hero.registerRequested.connect(self.registerCardRequested.emit)
        left.addWidget(self._hero, 1)

        # ---- 感应区实时状态 ----
        # 「新卡一刷就变成别人」时，第一时间要能看出读写器到底读到了几张卡、
        # 每张卡绑的是谁 —— 否则根本无从判断是"卡读串了"还是"绑定录错了"。
        inv_card = Card()
        it = QLabel("感应区实时状态")
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
        self._inv_tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self._inv_tbl.setShowGrid(False)
        self._inv_tbl.setMaximumHeight(132)
        ih = self._inv_tbl.horizontalHeader()
        ih.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ih.setSectionResizeMode(2, QHeaderView.Stretch)
        inv_card.addWidget(self._inv_tbl)
        left.addWidget(inv_card)
        middle.addLayout(left, 4)

        events_card = Card()
        events_title = QLabel("今日记录")
        events_title.setObjectName("cardTitle")
        events_card.addWidget(events_title)
        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["时间", "类型", "学号", "姓名"])
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
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        events_card.addWidget(self._tbl)
        events_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        middle.addWidget(events_card, 5)
        root.addLayout(middle, 1)

    def _tick_clock(self) -> None:
        # 读写器状态 + 实时时钟（状态文案随连接状态变化，不再写死）
        now = QDateTime.currentDateTime()
        short, _ = reader_state_display(self._reader_state, self._reader_detail)
        self._status_text.setText(f"{short}   ·   {now.toString('yyyy-MM-dd HH:mm:ss')}")

    # ------------------------------------------------------------ 公共 API
    def set_reader_state(self, state: str, text: str) -> None:
        """state: ok / err / off / warn；text: worker 上报的完整信息"""
        self._reader_state = state
        self._reader_detail = text
        self._status_dot.setState(state)
        self._tick_clock()  # 立刻重绘一次，之后由 1s 定时器继续刷新
        short, tip = reader_state_display(state, text)
        self._status_text.setToolTip(tip)

    def set_inventory(self, epcs) -> None:
        """天线内当前标签（实时）。

        有了它就能一眼分清两种情况：
          · 感应区 0 张 → 读写器根本没读到卡（卡没贴好/功率太低/卡叠在一起）
          · 感应区 N 张但刷出来是别人 → 读到了卡，是绑定关系的问题
        """
        self._inventory = [str(e) for e in (epcs or [])]
        n = len(self._inventory)
        self._inv_text.setText(f"· 感应区 {n} 张")
        tip = "读写器此刻读到的卡：\n" + (
            "\n".join(self._inventory) if self._inventory else "（空 —— 没读到卡）")
        self._inv_text.setToolTip(tip)
        self._reload_inventory_table()

    def _reload_inventory_table(self) -> None:
        """把感应区里每张卡和它的主人列出来 —— 认错人时第一眼就能定位。"""
        eps = list(self._inventory)
        if not eps:
            self._inv_hint.setText(
                "感应区里没有卡。把卡贴到读写器天线下方 5～15 cm，"
                "这里会立刻列出它真实读到的卡号。")
            self._inv_tbl.setRowCount(0)
            return
        if len(eps) > 1:
            self._inv_hint.setText(
                f"⚠ 读写器此刻读到 {len(eps)} 张卡！刷卡只认下面这些卡号 —— "
                "如果卡没贴对位置，程序会把别的卡当成刷的这张。")
        else:
            self._inv_hint.setText("读写器此刻读到 1 张卡（刷卡只认下面这个卡号）：")
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

    def _extra_hint(self, epc: str) -> str:
        """刷卡时的附加提醒：感应区里还有别的卡 → 很可能是读串了。"""
        others = [e for e in self._inventory if e.upper() != (epc or "").upper()]
        if others:
            return f"⚠ 感应区还有 {len(others)} 张卡，可能读串了"
        return ""

    def on_tag_read(self, epc: str) -> Optional[dict]:
        """主循环读到一帧标签时调用。返回 None 表示被冷却丢弃。"""
        import time
        now = time.time()
        last = self._cooldowns.get(epc, 0)
        if now - last < self._cooldown_sec:
            return None
        self._cooldowns[epc] = now

        student = self._db.get_student_by_epc(epc)
        extra = self._extra_hint(epc)

        # ① 没登记过的卡：明确提示去注册，不参与签到/签退
        if student is None:
            self._db.add_event(epc, None, "unknown")
            self._hero.showTag(
                name="未注册卡片",
                event_type="unknown",
                epc=epc,
                subtitle="该卡尚未注册，请到「学生注册」登记此卡",
                extra=extra,
                can_register=True,      # ← 一键跳到注册页（发卡流程）
            )
            self._announce(None, "unknown")
            self._reload_today()
            return {"event": "unknown", "student": None, "epc": epc}

        # ② 卡有效期：不在有效期内的卡一律不可用（不参与签到/签退）
        ok, why = self._db.judge_card_validity(student)
        if not ok:
            self._db.add_event(epc, student, "unknown")
            self._hero.showTag(
                name=student.name,
                event_type="expired",
                epc=epc,
                subtitle=f"{why}（学号 {student.sid}），请联系管理员",
                extra=extra,
            )
            self._announce_expired()
            self._reload_today()
            return {"event": "expired", "student": student, "epc": epc, "reason": why}

        # ③ 正常签到 / 签退：只看当天
        #    昨天最后一条是「签到」的话，今天第一刷也应该是签到
        last_event = self._db.get_last_event_today(epc)
        if last_event and last_event.event_type == "in":
            event_type = "out"
        else:
            event_type = "in"

        # 签到后没到设定间隔又刷卡 → 不允许签退：只提示 + 播报，不记考勤
        if event_type == "out" and last_event and self._min_gap_sec > 0:
            elapsed = now - last_event.ts.timestamp()
            if elapsed < self._min_gap_sec:
                self._hero.showTag(
                    name=student.name,
                    event_type="blocked",
                    epc=epc,
                    subtitle=(f"还需 {self._fmt_remain(self._min_gap_sec - elapsed)} 才能签退"
                              f"（签到时间 {last_event.ts.strftime('%H:%M:%S')}）"),
                    extra=extra,
                )
                self._announce_too_soon(student.name)
                self._reload_today()
                return {"event": "blocked", "student": student, "epc": epc}

        self._db.add_event(epc, student, event_type)
        self._hero.showTag(
            name=student.name,
            event_type=event_type,
            epc=epc,
            subtitle=student.sid or "",
            extra=extra,
        )
        self._announce(student, event_type)
        self._reload_today()
        return {"event": event_type, "student": student, "epc": epc}

    def _announce(self, student, event_type: str) -> None:
        """刷卡语音播报：姓名 + 签到/签退成功，未登记则提示去注册。

        播报失败不能影响考勤本身，所以这里吞掉所有异常。
        """
        try:
            voice = get_engine()
            if student:
                if event_type == "out":
                    voice.announce_check_out(student.name)
                else:
                    voice.announce_check_in(student.name)
            else:
                voice.announce_unknown_card()
        except Exception:
            pass

    def _announce_too_soon(self, name: str) -> None:
        """未到签退时间的语音提示（失败不影响考勤）。"""
        try:
            get_engine().announce_too_soon(name)
        except Exception:
            pass

    def _announce_expired(self) -> None:
        """卡已过有效期的语音提示（失败不影响考勤）。"""
        try:
            get_engine().announce_expired()
        except Exception:
            pass

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _fmt_remain(seconds: float) -> str:
        """剩余秒数 → 「3 分 20 秒」这种人话。"""
        total = max(0, int(round(seconds)))
        m, s = divmod(total, 60)
        if m <= 0:
            return f"{s} 秒"
        return f"{m} 分 {s:02d} 秒"

    def apply_settings(self) -> None:
        """重新读取设置（设置页保存后会调用，不用重启程序）。"""
        self._apply_settings()

    def _apply_settings(self) -> None:
        try:
            self._cooldown_sec = max(1, int(self._db.get_setting("cooldown", "5")))
        except (TypeError, ValueError):
            self._cooldown_sec = 5
        try:
            self._min_gap_sec = max(0.0, float(self._db.get_setting("min_gap_min", "0"))) * 60.0
        except (TypeError, ValueError):
            self._min_gap_sec = 0.0

    # ------------------------------------------------------------ 内部
    def _reload_today(self) -> None:
        # 统计
        students = self._db.list_students()
        self._stat_total.setValue(len(students))
        today = self._db.get_today_events()
        in_sids = {e.sid for e in today if e.event_type == "in" and e.sid}
        out_sids = {e.sid for e in today if e.event_type == "out" and e.sid}
        self._stat_in.setValue(len(in_sids))
        self._stat_out.setValue(len(out_sids))

        # 表格（最新在最上）
        today_sorted = sorted(today, key=lambda e: e.ts, reverse=True)
        self._fill_table(today_sorted)
        # 绑定关系可能刚被改过，感应区列表跟着刷新
        self._reload_inventory_table()

    def _fill_table(self, events: List[AttendanceEvent]) -> None:
        self._tbl.setRowCount(len(events))
        color_map = {"in": QColor("#D1FAE5"), "out": QColor("#FEF3C7"),
                     "unknown": QColor("#F1F5F9")}
        text_map = {"in": "签到", "out": "签退", "unknown": "未知"}
        for r, ev in enumerate(events):
            time_item = QTableWidgetItem(ev.ts.strftime("%H:%M:%S"))
            type_item = QTableWidgetItem(text_map.get(ev.event_type, ""))
            sid_item = QTableWidgetItem(ev.sid or "—")
            name_item = QTableWidgetItem(ev.name or "—")
            for it in (time_item, type_item, sid_item, name_item):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            type_item.setBackground(color_map.get(ev.event_type, QColor("#FFFFFF")))
            self._tbl.setItem(r, 0, time_item)
            self._tbl.setItem(r, 1, type_item)
            self._tbl.setItem(r, 2, sid_item)
            self._tbl.setItem(r, 3, name_item)
