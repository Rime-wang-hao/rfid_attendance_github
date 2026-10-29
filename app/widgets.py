from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QLabel,
                             QPushButton, QVBoxLayout, QWidget)


def _shadow(parent, blur=20, dy=2, alpha=25):
    sh = QGraphicsDropShadowEffect(parent)
    sh.setBlurRadius(blur); sh.setOffset(0, dy)
    sh.setColor(QColor(15, 23, 42, alpha))
    return sh


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;}")
        self.setGraphicsEffect(_shadow(self))
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 18, 20, 18)
        self._lay.setSpacing(10)
    def addWidget(self, w, *a): self._lay.addWidget(w, *a)
    def addLayout(self, l, *a): self._lay.addLayout(l, *a)
    def addStretch(self, n=0): self._lay.addStretch(n)


class StatCard(QFrame):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        # 必须用 ID 选择器：QLabel 继承自 QFrame，若写 QFrame{...}
        # 会给内部的标签也套上边框和背景。
        self.setObjectName("statCard")
        self.setStyleSheet(
            "QFrame#statCard{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;}"
        )
        self.setGraphicsEffect(_shadow(self, blur=16, alpha=20))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(4)
        self._label = QLabel(label)
        self._label.setStyleSheet("color:#64748B;font-size:12px;font-weight:500;")
        self._value = QLabel("0")
        self._value.setStyleSheet("color:#4F46E5;font-size:28px;font-weight:800;")
        lay.addWidget(self._label); lay.addWidget(self._value)

    def setValue(self, v): self._value.setText(str(v))


# 读写器状态 → (圆点状态色, 文案)
# 约定：连接成功=绿色「已连接」，未连接/连接失败=红色「未连接 / 连接失败」
READER_STATE_STYLE = {
    "ok":   ("ok",   "#10B981", "已连接"),
    "warn": ("warn", "#F59E0B", "已连接（不稳定）"),
    "err":  ("err",  "#EF4444", "连接失败"),
    "off":  ("off",  "#EF4444", "未连接"),
}


def reader_state_display(state: str, detail: str = "") -> tuple:
    """把 worker 上报的状态归一化成 (文案, 悬浮提示)。

    详情页/conner 只显示简短文案（已连接 / 未连接 / 连接失败），
    完整信息（如 "已连接 COM3（115200）"）放到 tooltip 与状态栏。
    """
    _, _, short = READER_STATE_STYLE.get(state, READER_STATE_STYLE["off"])
    return short, detail or short


class StatusDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self.setFixedSize(14, 14); self._state = "off"
    def setState(self, s):
        self._state = s; self.update()
    def paintEvent(self, e):
        from PyQt5.QtGui import QPainter, QColor
        from PyQt5.QtCore import Qt
        # off（未连接）与 err（连接失败）都画红色，只有 ok 才是绿色
        c = {"ok": "#10B981", "warn": "#F59E0B", "err": "#EF4444",
             "off": "#EF4444"}.get(self._state, "#EF4444")
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(c)); p.setPen(Qt.NoPen); p.drawEllipse(0, 0, 14, 14)


class HeroDisplay(Card):
    """刷卡后大字号显示姓名/事件。"""

    #: 刷到未注册的卡时，点它就能直接跳到「学生注册」并带上这张卡
    registerRequested = pyqtSignal(str)

    # 字号统一放在这里，方便整体调大调小
    EVENT_SIZE = 34
    NAME_SIZE = 76
    IDLE_SIZE = 56
    SUB_SIZE = 26
    EPC_SIZE = 19

    IDLE_TEXT = "👋  等待刷卡……"
    IDLE_STYLE = f"font-size:{IDLE_SIZE}px;font-weight:800;color:#94A3B8;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(300)
        lay = self._lay            # 复用 Card 已安装到自身的布局
        lay.setContentsMargins(24, 20, 24, 20); lay.setSpacing(10)
        self._epc_value = ""
        self._event = QLabel(""); self._event.setAlignment(Qt.AlignCenter)
        self._name = QLabel(self.IDLE_TEXT)
        self._name.setAlignment(Qt.AlignCenter)
        self._name.setWordWrap(True)
        self._name.setStyleSheet(self.IDLE_STYLE)
        self._sub = QLabel(""); self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet(f"font-size:{self.SUB_SIZE}px;color:#64748B;")
        self._epc = QLabel(""); self._epc.setAlignment(Qt.AlignCenter)
        self._epc.setStyleSheet(
            "font-family:Cascadia Mono,Consolas,monospace;"
            f"color:#94A3B8;font-size:{self.EPC_SIZE}px;"
        )
        # 未注册卡时出现的「去注册这张卡」：发卡流程的入口
        self._btn_reg = QPushButton("✍  去注册这张卡")
        self._btn_reg.setProperty("variant", "secondary")
        self._btn_reg.setCursor(Qt.PointingHandCursor)
        self._btn_reg.setMinimumHeight(38)
        self._btn_reg.setVisible(False)
        self._btn_reg.clicked.connect(self._on_register_clicked)
        lay.addStretch(1); lay.addWidget(self._event)
        lay.addWidget(self._name); lay.addWidget(self._sub); lay.addWidget(self._epc)
        lay.addWidget(self._btn_reg, 0, Qt.AlignCenter)
        lay.addStretch(1)

    def _on_register_clicked(self) -> None:
        if self._epc_value:
            self.registerRequested.emit(self._epc_value)

    def showEmpty(self):
        self._epc_value = ""
        self._event.setText(""); self._event.setStyleSheet("")
        self._name.setText(self.IDLE_TEXT)
        self._name.setStyleSheet(self.IDLE_STYLE)
        self._sub.setText(""); self._epc.setText("")
        self._btn_reg.setVisible(False)

    def showTag(self, *, name, event_type, epc, subtitle="", extra="",
                can_register=False):
        colors = {"in": ("#10B981", "✓  签到成功"),
                  "out": ("#F59E0B", "↩  签退成功"),
                  "unknown": ("#E11D48", "?  该卡未注册"),
                  "expired": ("#EF4444", "⛔  该卡已失效"),
                  "blocked": ("#EF4444", "⏳  未到签退时间")}
        color, text = colors.get(event_type, ("#94A3B8", ""))
        self._epc_value = (epc or "").strip()
        self._event.setText(text)
        self._event.setStyleSheet(
            f"font-size:{self.EVENT_SIZE}px;font-weight:700;color:{color};"
        )
        self._name.setText(name or "—")
        self._name.setStyleSheet(
            f"font-size:{self.NAME_SIZE}px;font-weight:800;color:#0F172A;"
        )
        self._sub.setText(subtitle or "")
        self._epc.setText(f"EPC: {epc}" + (f"　{extra}" if extra else ""))
        self._btn_reg.setVisible(bool(can_register))
