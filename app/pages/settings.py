# -*- coding: utf-8 -*-
"""「设置」页面。"""
from __future__ import annotations

import os
from typing import Optional

from PyQt5.QtCore import QDate, Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox,
                             QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QScrollArea,
                             QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..database import Database
from ..reader import UHFReader, is_port_available, list_serial_ports
from ..voice import get_engine
from ..widgets import Card


def distance_hint(dbm: int) -> str:
    """功率 → 大致读卡距离（经验值，仅供参考，需现场实测）。"""
    if dbm >= 27:
        return "约 3~6 米（出厂默认，最远）"
    if dbm >= 22:
        return "约 1.5~3 米"
    if dbm >= 18:
        return "约 0.8~1.5 米"
    if dbm >= 14:
        return "约 30~50 厘米"
    if dbm >= 10:
        return "约 15~30 厘米"
    if dbm >= 6:
        return "约 10~15 厘米"
    return "约 5~10 厘米（功率过低可能完全读不到卡）"


class SettingsPage(QWidget):
    settingsSaved = pyqtSignal()      # 保存后通知主窗口立即生效

    def __init__(self, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 内容放进滚动区：窗口拉小、或以后再加卡片，也不会被裁掉
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(28, 24, 28, 16)
        root.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("h1")
        root.addWidget(title)

        # 串口
        port_card = Card()
        t1 = QLabel("读写器")
        t1.setObjectName("cardTitle")
        port_card.addWidget(t1)
        h1 = QLabel("使用 USB 转串口线（CH340/CP2102/PL2303 等）连接读写器与电脑的 RS232 口。\n"
                    "不知道串口号就选「自动扫描」，程序会依次尝试所有串口和波特率。")
        h1.setObjectName("cardHint")
        h1.setWordWrap(True)
        port_card.addWidget(h1)
        form1 = QFormLayout()
        form1.setSpacing(10)
        self._com = QSpinBox()
        self._com.setRange(0, 30)
        self._com.setSpecialValueText("自动扫描")
        self._com.setValue(int(self._db.get_setting("com_port", "3")))
        self._com.setFixedWidth(140)
        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_row.addWidget(self._com)
        btn_detect = QPushButton("检测串口")
        btn_detect.setProperty("variant", "secondary")
        btn_detect.clicked.connect(self._on_detect)
        port_row.addWidget(btn_detect)
        port_row.addStretch(1)
        form1.addRow("串口 COM：", port_row)
        port_card.addLayout(form1)
        root.addWidget(port_card)

        # 读卡距离（发射功率）
        dist_card = Card()
        t5 = QLabel("读卡距离")
        t5.setObjectName("cardTitle")
        dist_card.addWidget(t5)
        h5 = QLabel("不想隔老远也能刷上卡 → 把发射功率调小。\n"
                    "保存后若读写器已连接会立即生效；估算距离仅供参考，请现场用卡实测。")
        h5.setObjectName("cardHint")
        h5.setWordWrap(True)
        dist_card.addWidget(h5)
        self._power = QSpinBox()
        self._power.setRange(0, 30)
        self._power.setValue(int(self._db.get_setting("power_dbm", "10")))
        self._power.setSuffix(" dBm")
        self._power.setFixedWidth(120)
        self._power.valueChanged.connect(self._on_power_changed)

        # 滑块和数值框双向联动（值没变时 Qt 不会重复发信号，不会递归）
        self._power_slider = QSlider(Qt.Horizontal)
        self._power_slider.setRange(0, 30)
        self._power_slider.setValue(self._power.value())
        self._power_slider.setFixedWidth(280)
        self._power_slider.valueChanged.connect(self._power.setValue)
        self._power.valueChanged.connect(self._power_slider.setValue)

        self._power_hint = QLabel(distance_hint(self._power.value()))
        self._power_hint.setObjectName("muted")

        lbl_power = QLabel("发射功率：")
        lbl_power.setFixedWidth(80)
        power_row = QHBoxLayout()
        power_row.setSpacing(12)
        power_row.addWidget(lbl_power)
        power_row.addWidget(self._power_slider)
        power_row.addWidget(self._power)
        power_row.addStretch(1)

        # 提示文字缩进到与滑块同一列（80 标签宽 + 12 间距）
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(92, 0, 0, 0)
        hint_row.addWidget(self._power_hint)
        hint_row.addStretch(1)

        dist_card.addLayout(power_row)
        dist_card.addLayout(hint_row)
        root.addWidget(dist_card)

        # 考勤规则
        rule_card = Card()
        t2 = QLabel("考勤规则")
        t2.setObjectName("cardTitle")
        rule_card.addWidget(t2)
        h2 = QLabel("刷卡冷却：同一张卡两次刷卡的最小间隔（秒）。"
                    "卡片长时间放在天线上会被连续读到，小于该值视为同一次刷卡。\n"
                    "签退间隔：签到后至少经过这么久才允许签退。没到时间刷卡会显示并播报"
                    "「未到签退时间」，且不会记入考勤记录。")
        h2.setObjectName("cardHint")
        h2.setWordWrap(True)
        rule_card.addWidget(h2)
        form2 = QFormLayout()
        form2.setSpacing(10)
        self._cooldown = QSpinBox()
        self._cooldown.setRange(1, 600)
        self._cooldown.setValue(int(self._db.get_setting("cooldown", "5")))
        self._cooldown.setSuffix(" 秒")
        self._cooldown.setFixedWidth(120)
        form2.addRow("刷卡冷却：", self._cooldown)

        self._gap = QSpinBox()
        self._gap.setRange(0, 600)
        self._gap.setSingleStep(1)
        self._gap.setSuffix(" 分钟")
        self._gap.setSpecialValueText("不限制（签完就能退）")
        try:
            self._gap.setValue(int(float(self._db.get_setting("min_gap_min", "0"))))
        except (TypeError, ValueError):
            self._gap.setValue(0)
        self._gap.setFixedWidth(190)
        form2.addRow("签退间隔：", self._gap)
        rule_card.addLayout(form2)
        root.addWidget(rule_card)

        # 卡有效期
        valid_card = Card()
        t7 = QLabel("卡有效期")
        t7.setObjectName("cardTitle")
        valid_card.addWidget(t7)
        h7 = QLabel("给卡片设定一个可用时间段：只有在这段时间内登记的卡才能刷卡考勤；"
                    "一旦当前日期超过截止日，这批卡就整批失效——刷卡会提示「已失效」，"
                    "且不计入考勤。\n不勾选 = 不限制，所有已登记的卡一直能用。")
        h7.setObjectName("cardHint")
        h7.setWordWrap(True)
        valid_card.addWidget(h7)

        self._valid_enabled = QCheckBox("启用卡有效期")
        valid_card.addWidget(self._valid_enabled)

        form7 = QFormLayout()
        form7.setSpacing(10)
        self._valid_from = QDateEdit()
        self._valid_from.setCalendarPopup(True)      # 点一下弹出日历，直接选年月日
        self._valid_from.setDisplayFormat("yyyy-MM-dd")
        self._valid_from.setFixedWidth(170)
        self._valid_until = QDateEdit()
        self._valid_until.setCalendarPopup(True)
        self._valid_until.setDisplayFormat("yyyy-MM-dd")
        self._valid_until.setFixedWidth(170)
        form7.addRow("启用日期：", self._valid_from)
        form7.addRow("截止日期：", self._valid_until)
        valid_card.addLayout(form7)

        self._valid_hint = QLabel("")
        self._valid_hint.setObjectName("muted")
        self._valid_hint.setWordWrap(True)
        valid_card.addWidget(self._valid_hint)

        # 连线必须等控件都建好：setChecked 会立刻触发 toggled，
        # 那时 _valid_from 还没创建，回调里访问它会直接报错
        self._valid_from.dateChanged.connect(self._refresh_valid_hint)
        self._valid_until.dateChanged.connect(self._refresh_valid_hint)
        self._valid_enabled.toggled.connect(self._on_valid_toggled)
        self._valid_enabled.setChecked(self._db.get_setting("valid_enabled", "0") != "0")
        root.addWidget(valid_card)

        # 语音播报
        voice_card = Card()
        t6 = QLabel("语音播报")
        t6.setObjectName("cardTitle")
        voice_card.addWidget(t6)
        h6 = QLabel("刷卡后自动念出结果：签到 / 签退念「姓名 + 签到成功 / 签退成功」；"
                    "刷到没登记的卡则提示去「人员管理」注册。")
        h6.setObjectName("cardHint")
        h6.setWordWrap(True)
        voice_card.addWidget(h6)

        self._voice_enabled = QCheckBox("启用刷卡语音播报")
        self._voice_enabled.setChecked(self._db.get_setting("voice_enabled", "1") != "0")
        voice_card.addWidget(self._voice_enabled)

        form6 = QFormLayout()
        form6.setSpacing(10)

        self._voice_name = QComboBox()
        self._voice_name.addItem("自动（系统默认中文）", "")
        try:
            for n in get_engine().list_voices():
                self._voice_name.addItem(n, n)
        except Exception:
            pass
        idx = self._voice_name.findData(self._db.get_setting("voice_name", ""))
        if idx >= 0:
            self._voice_name.setCurrentIndex(idx)
        self._voice_name.setFixedWidth(320)
        form6.addRow("发音人：", self._voice_name)

        self._voice_rate = QDoubleSpinBox()
        self._voice_rate.setRange(-1.0, 1.0)
        self._voice_rate.setSingleStep(0.1)
        self._voice_rate.setDecimals(1)
        try:
            self._voice_rate.setValue(float(self._db.get_setting("voice_rate", "0")))
        except ValueError:
            self._voice_rate.setValue(0.0)
        self._voice_rate.setFixedWidth(120)
        form6.addRow("语速：", self._voice_rate)
        voice_card.addLayout(form6)

        hint6 = QLabel("提示：想换成指定人声（比如某段录音的音色），把对应的 wav 放进 "
                       "程序目录下的 voice 文件夹即可，无需改代码。")
        hint6.setObjectName("cardHint")
        hint6.setWordWrap(True)
        voice_card.addWidget(hint6)

        tests = QHBoxLayout()
        tests.setSpacing(8)
        btn_in = QPushButton("试听：签到")
        btn_in.setProperty("variant", "secondary")
        btn_in.clicked.connect(lambda: self._test_voice("in"))
        btn_out = QPushButton("试听：签退")
        btn_out.setProperty("variant", "secondary")
        btn_out.clicked.connect(lambda: self._test_voice("out"))
        btn_unknown = QPushButton("试听：未注册卡")
        btn_unknown.setProperty("variant", "secondary")
        btn_unknown.clicked.connect(lambda: self._test_voice("unknown"))
        btn_soon = QPushButton("试听：未到签退时间")
        btn_soon.setProperty("variant", "secondary")
        btn_soon.clicked.connect(lambda: self._test_voice("soon"))
        for b in (btn_in, btn_out, btn_unknown, btn_soon):
            tests.addWidget(b)
        tests.addStretch(1)
        voice_card.addLayout(tests)
        root.addWidget(voice_card)

        # 站点信息
        site_card = Card()
        t3 = QLabel("站点信息")
        t3.setObjectName("cardTitle")
        site_card.addWidget(t3)
        form3 = QFormLayout()
        form3.setSpacing(10)
        self._school = QLineEdit()
        form3.addRow("学校 / 单位：", self._school)
        self._class_ = QLineEdit()
        form3.addRow("班级 / 部门：", self._class_)
        site_card.addLayout(form3)
        root.addWidget(site_card)

        # 数据
        data_card = Card()
        t4 = QLabel("数据")
        t4.setObjectName("cardTitle")
        data_card.addWidget(t4)
        h4 = QLabel(f"数据库文件位置：\n{self._db.path}")
        h4.setObjectName("muted")
        h4.setWordWrap(True)
        data_card.addWidget(h4)
        btns = QHBoxLayout()
        btn_import = QPushButton("从 students.txt 导入")
        btn_import.setProperty("variant", "secondary")
        btn_import.clicked.connect(self._on_import)
        btn_clear_att = QPushButton("清空今日考勤")
        btn_clear_att.setProperty("variant", "danger")
        btn_clear_att.clicked.connect(self._on_clear_today)
        btns.addWidget(btn_import)
        btns.addWidget(btn_clear_att)
        btns.addStretch(1)
        data_card.addLayout(btns)
        root.addWidget(data_card)

        root.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # 保存栏固定在底部：不用滚到底才能点到
        footer = QWidget()
        footer.setObjectName("pageFooter")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        bar = QHBoxLayout(footer)
        bar.setContentsMargins(28, 12, 28, 16)
        bar.addStretch(1)
        btn_save = QPushButton("保存设置")
        btn_save.clicked.connect(self._on_save)
        bar.addWidget(btn_save)
        outer.addWidget(footer)

    def _on_detect(self) -> None:
        ports = list_serial_ports()
        if not ports:
            QMessageBox.warning(
                self, "检测串口",
                "没有检测到任何串口。\n请确认 USB 转串口线已插好、驱动已安装、读写器已上电。",
            )
            return
        lines = [
            f"COM{p}" + ("（当前被占用）" if not is_port_available(p) else "（可用）")
            for p in ports
        ]
        QMessageBox.information(
            self, "检测串口",
            "检测到以下串口：\n" + "\n".join(lines)
            + "\n\n如果看不到你的读写器，换一个 USB 口或重新插拔后再点「检测串口」。",
        )

    def _load(self) -> None:
        self._school.setText(self._db.get_setting("school", ""))
        self._class_.setText(self._db.get_setting("class_name", ""))
        today = QDate.currentDate()
        self._valid_from.setDate(self._date_setting("valid_from", today))
        self._valid_until.setDate(self._date_setting("valid_until", today.addDays(120)))

    def _on_power_changed(self) -> None:
        self._power_hint.setText(distance_hint(self._power.value()))

    def _date_setting(self, key: str, default: QDate) -> QDate:
        """读取日期型设置；没存过或格式不对就用默认值。"""
        d = QDate.fromString(self._db.get_setting(key, "").strip(), "yyyy-MM-dd")
        return d if d.isValid() else default

    def _on_valid_toggled(self, checked: bool) -> None:
        self._valid_from.setEnabled(checked)
        self._valid_until.setEnabled(checked)
        self._refresh_valid_hint()

    def _refresh_valid_hint(self) -> None:
        if not self._valid_enabled.isChecked():
            self._valid_hint.setText("当前：不限制，所有已登记的卡都能刷卡考勤。")
            return
        f = self._valid_from.date().toString("yyyy-MM-dd")
        t = self._valid_until.date().toString("yyyy-MM-dd")
        self._valid_hint.setText(
            f"当前：{f} ~ {t}　→　只有这段时间内登记的卡能用，"
            f"过了 {t} 这批卡整批失效。"
        )

    def _sample_name(self) -> str:
        """试听用的名字：取人员名单第一个，没有登记过就用占位名。"""
        try:
            people = self._db.list_students()
            if people:
                return people[0].name
        except Exception:
            pass
        return "张三"

    def _test_voice(self, kind: str) -> None:
        """试听三个场景的播报（按当前下拉框/语速设置，但不落库）。"""
        try:
            voice = get_engine()
            voice.set_enabled(True)      # 试听不看开关，否则没法试
            voice.set_voice_name(self._voice_name.currentData() or "")
            voice.set_rate(float(self._voice_rate.value()))
            if kind == "out":
                voice.announce_check_out(self._sample_name())
            elif kind == "unknown":
                voice.announce_unknown_card()
            elif kind == "soon":
                voice.announce_too_soon(self._sample_name())
            else:
                voice.announce_check_in(self._sample_name())
        except Exception:
            QMessageBox.warning(
                self, "试听失败",
                "当前系统没有可用的中文语音引擎。\n"
                "请在 Windows「设置 → 时间和语言 → 语音」里安装中文语音包后重试。",
            )

    def _on_save(self) -> None:
        self._db.set_setting("com_port", str(self._com.value()))
        self._db.set_setting("cooldown", str(self._cooldown.value()))
        self._db.set_setting("min_gap_min", str(self._gap.value()))
        self._db.set_setting("power_dbm", str(self._power.value()))
        self._db.set_setting("school", self._school.text().strip())
        self._db.set_setting("class_name", self._class_.text().strip())
        self._db.set_setting("voice_enabled", "1" if self._voice_enabled.isChecked() else "0")
        self._db.set_setting("voice_name", self._voice_name.currentData() or "")
        self._db.set_setting("voice_rate", str(round(self._voice_rate.value(), 2)))

        # 卡有效期：没启用就把区间清空（判定逻辑只看区间，留着旧值会继续生效）
        if self._valid_enabled.isChecked():
            f, t = self._valid_from.date(), self._valid_until.date()
            if f > t:
                QMessageBox.warning(
                    self, "日期有误",
                    f"启用日期（{f.toString('yyyy-MM-dd')}）不能晚于"
                    f"截止日期（{t.toString('yyyy-MM-dd')}）。",
                )
                return
            self._db.set_setting("valid_enabled", "1")
            self._db.set_setting("valid_from", f.toString("yyyy-MM-dd"))
            self._db.set_setting("valid_until", t.toString("yyyy-MM-dd"))
        else:
            self._db.set_setting("valid_enabled", "0")
            self._db.set_setting("valid_from", "")
            self._db.set_setting("valid_until", "")

        self.settingsSaved.emit()
        QMessageBox.information(self, "完成", "设置已保存。")

    def _on_import(self) -> None:
        path = os.path.join(
            os.path.dirname(self._db.path), "students.txt",
        )
        if not os.path.exists(path):
            QMessageBox.information(
                self, "提示",
                f"未在以下位置找到 students.txt：\n{path}\n"
                "请把旧版本的学生名单文件放到与 .exe 同一目录后再试。",
            )
            return
        n = self._db.import_students_txt(path)
        QMessageBox.information(self, "完成", f"已导入 {n} 条学生信息。")

    def _on_clear_today(self) -> None:
        ans = QMessageBox.question(self, "确认", "清空今天的全部考勤记录？此操作不可恢复。")
        if ans == QMessageBox.Yes:
            n = self._db.clear_today_events()
            QMessageBox.information(self, "完成", f"已删除 {n} 条今日记录。")
