"""主窗口。"""
import os, sys, winsound
from PyQt5.QtCore import QLockFile, QStandardPaths, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                             QLabel, QMainWindow, QMessageBox, QPushButton,
                             QShortcut, QStackedWidget, QVBoxLayout, QWidget)
from . import APP_NAME
from .database import Database
from .pages.cardhistory import CardHistoryPage
from .pages.live import LivePage
from .pages.people import PeoplePage
from .pages.records import RecordsPage
from .pages.register import RegisterPage
from .pages.settings import SettingsPage
from .reader_worker import ReaderWorker
from .styles import QSS
from .voice import get_engine

# 导航菜单顺序（key 同时作为 self._pages 的键）
NAV_ITEMS = [
    ("📡", "实时考勤", "live"),
    ("📝", "学生注册", "register"),
    ("👥", "人员管理", "people"),
    ("📇", "卡片记录", "cards"),
    ("📊", "考勤记录", "records"),
    ("⚙",  "设置",     "settings"),
]

PAGE_CLASSES = {
    "live":     LivePage,
    "register": RegisterPage,
    "people":   PeoplePage,
    "records":  RecordsPage,
    "cards":    CardHistoryPage,
    "settings": SettingsPage,
}


class NavButton(QPushButton):
    def __init__(self, icon, text, parent=None):
        super().__init__(f"  {icon}    {text}", parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setObjectName("NavButton")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.setMinimumSize(1100, 720)
        self._db = Database()
        self._worker = None
        self._conn_port = None       # 当前线程使用的串口（None = 自动扫描）
        self._build_ui()
        self._apply_voice_settings()
        self._try_autoconnect()

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar(), 0)
        root.addWidget(self._build_content(), 1)

    def _build_sidebar(self):
        sb = QFrame(); sb.setObjectName("sidebar"); sb.setFixedWidth(220)
        v = QVBoxLayout(sb); v.setContentsMargins(0,0,0,0); v.setSpacing(0)
        title = QLabel("RFID 考勤"); title.setObjectName("sidebarTitle")
        sub = QLabel("UHFReader18 智能管理"); sub.setObjectName("sidebarSubtitle")
        v.addWidget(title); v.addWidget(sub)
        self._nav = QButtonGroup(sb); self._nav.setExclusive(True)
        for icon, text, key in NAV_ITEMS:
            btn = NavButton(icon, text, sb)
            btn.setProperty("pageKey", key)
            self._nav.addButton(btn)
            v.addWidget(btn)
            btn.clicked.connect(lambda _, k=key: self._show_page(k))
        v.addStretch(1)
        self._btn_conn = QPushButton("连  接", sb)
        self._btn_conn.setCursor(Qt.PointingHandCursor)
        self._btn_conn.clicked.connect(self._toggle_connection)
        v.addWidget(self._btn_conn)
        footer = QLabel("v1.0  ·  ©2026"); footer.setObjectName("sidebarFooter")
        v.addWidget(footer)
        self._first_btn = self._nav.buttons()[0]
        self._first_btn.setChecked(True)
        return sb

    def _build_content(self):
        wrap = QFrame()
        v = QVBoxLayout(wrap); v.setContentsMargins(0,0,0,0); v.setSpacing(0)
        self._stack = QStackedWidget(wrap)
        v.addWidget(self._stack, 1)
        self._pages = {key: cls(self._db) for key, cls in PAGE_CLASSES.items()}
        for w in self._pages.values():
            self._stack.addWidget(w)
        self._stack.setCurrentWidget(self._pages["live"])

        # 「学生注册」页注册成功后 → 刷新人员管理列表
        reg = self._pages["register"]
        reg.studentRegistered.connect(self._on_student_registered)
        reg.gotoPeopleRequested.connect(lambda: self._show_page("people"))
        # 「学生注册」页点「写入卡号」（发卡）→ 交给读写线程写卡
        reg.writeEpcRequested.connect(self._on_write_epc_request)

        # 「设置」页保存后立刻让功率/串口生效
        self._pages["settings"].settingsSaved.connect(self._on_settings_saved)

        # 「卡片记录」页点「去注册这张卡」→ 带着 EPC 跳到注册页
        self._pages["cards"].registerCardRequested.connect(self._on_register_card)
        # 「实时考勤」页刷到未注册卡时点「去注册这张卡」→ 同样跳到注册页
        self._pages["live"].registerCardRequested.connect(self._on_register_card)

        self.statusBar().showMessage("就绪")
        return wrap

    def _show_page(self, key: str) -> None:
        """切换页面（并同步侧边栏选中态）。"""
        page = self._pages[key]
        self._stack.setCurrentWidget(page)
        for btn in self._nav.buttons():
            btn.setChecked(btn.property("pageKey") == key)
        reload_fn = getattr(page, "reload", None)
        if callable(reload_fn):
            reload_fn()

    def _on_student_registered(self, name: str) -> None:
        self._pages["people"].reload()
        self.statusBar().showMessage(f"已注册学生：{name}")

    def _toggle_connection(self):
        # 已经有线程（正在连 / 正在重试 / 已连上）→ 这次点击就是「断开」
        if self._worker is not None:
            self._shutdown_worker()
            self._btn_conn.setText("连  接")
            self._btn_conn.setEnabled(True)
            self._show_state("未连接", "off")
            return
        self._start_worker(int(self._db.get_setting("com_port", "3")))

    def _start_worker(self, port: int):
        """port=0 表示自动扫描所有串口。"""
        power = int(self._db.get_setting("power_dbm", "10"))
        self._conn_port = None if port == 0 else port
        self._btn_conn.setText("连接中…")
        self._btn_conn.setEnabled(False)
        self._worker = ReaderWorker(self._conn_port, power=power)
        self._worker.tagRead.connect(self._on_tag)
        self._worker.readerStateChanged.connect(self._on_state)
        self._worker.inventoryChanged.connect(self._on_inventory)
        self._worker.epcWritten.connect(self._on_epc_written)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _apply_voice_settings(self) -> None:
        """把设置里的语音开关/语速/音色应用到播报引擎。"""
        try:
            voice = get_engine()
            voice.set_enabled(self._db.get_setting("voice_enabled", "1") != "0")
            try:
                voice.set_rate(float(self._db.get_setting("voice_rate", "0")))
            except ValueError:
                voice.set_rate(0.0)
            voice.set_voice_name(self._db.get_setting("voice_name", ""))
        except Exception:
            pass

    def _on_settings_saved(self):
        """设置页保存后：功率立刻下发；串口号变了就重连。"""
        self._apply_voice_settings()
        self._pages["live"].apply_settings()
        port = int(self._db.get_setting("com_port", "3"))
        power = int(self._db.get_setting("power_dbm", "10"))
        if self._worker is None:
            return
        if port == 0 or self._conn_port is None or port == self._conn_port:
            self._worker.set_power(power)          # 串口没变，只改功率
            self.statusBar().showMessage(f"已下发发射功率：{power} dBm")
        else:
            self._shutdown_worker()
            self._start_worker(port)

    def _shutdown_worker(self):
        """停掉读写线程。线程若还没退出，等它自己结束后再释放，
        避免 QThread 在运行中被销毁导致程序崩溃。"""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        worker.stop()
        if worker.isRunning():
            worker.finished.connect(worker.deleteLater)
        else:
            worker.deleteLater()

    def _on_worker_finished(self):
        worker = self.sender()
        if worker is not None and worker is not self._worker:
            return
        self._worker = None
        worker.deleteLater()
        self._btn_conn.setText("连  接")
        self._btn_conn.setEnabled(True)

    def _show_state(self, text, state):
        for page in self._pages.values():
            fn = getattr(page, "set_reader_state", None)
            if callable(fn):
                fn(state, text)

    def _on_state(self, text, state):
        # 忽略上一轮残留线程发来的状态，只认当前这个线程的
        worker = self.sender()
        if worker is not None and worker is not self._worker:
            return
        self._show_state(text, state)
        self.statusBar().showMessage(text)
        # 侧边栏按钮跟随真实状态：只有真正连上才显示「断开」
        self._btn_conn.setEnabled(True)
        self._btn_conn.setText("断  开" if state in ("ok", "warn") else "连  接")

    def _on_inventory(self, epcs) -> None:
        """天线内当前所有标签：转发给页面做实时显示（排查刷卡问题用）。"""
        for page in self._pages.values():
            fn = getattr(page, "set_inventory", None)
            if callable(fn):
                fn(epcs)

    def _on_tag(self, epc):
        # 「学生注册」页处于读取状态时优先消费这次刷卡（不记考勤）
        if self._pages["register"].on_tag_read(epc):
            try: winsound.Beep(1500, 90)
            except Exception: pass
            self.statusBar().showMessage(f"已读取卡片：{epc}")
            self._pages["cards"].on_tag_read(epc)
            return
        self._pages["live"].on_tag_read(epc)
        # 卡片记录页紧跟着刷新：这条记录已入库，表里能看到最新一条
        self._pages["cards"].on_tag_read(epc)
        try: winsound.Beep(1200, 120)
        except Exception: pass

    def _on_write_epc_request(self, epc: str) -> None:
        """「学生注册」页请求发卡（写 EPC）：转给读写线程执行。"""
        if self._worker is None:
            QMessageBox.warning(self, "读写器未连接",
                                "请先在左下角点「连接」连上读写器，再写入卡号。")
            return
        self.statusBar().showMessage(f"正在写入卡号 {epc} …")
        self._worker.request_write_epc(epc)

    def _on_epc_written(self, ok: bool, epc: str, msg: str) -> None:
        """读写线程写卡完成：结果转给「学生注册」页。"""
        worker = self.sender()
        if worker is not None and worker is not self._worker:
            return
        self._pages["register"].on_epc_written(ok, epc, msg)
        self.statusBar().showMessage(
            ("✓ " if ok else "✗ ") + (msg or ("写卡完成" if ok else "写卡失败")))

    def _on_register_card(self, epc: str) -> None:
        """「卡片记录」/「实时考勤」页请求注册某张卡：带上 EPC 切到注册页。"""
        self._pages["register"].set_epc(epc)
        self._show_page("register")
        self.statusBar().showMessage(f"已带入卡片 {epc}，填写学号和姓名后保存即可")

    def _try_autoconnect(self):
        self._toggle_connection()

    def closeEvent(self, e):
        self._shutdown_worker()
        e.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)

    # 单实例保护：同一台电脑只允许开一个窗口。
    # 两个窗口会抢同一个串口，后开的那个必然连不上读写器（表现就是右上角一直「未连接」）。
    lock_path = os.path.join(
        QStandardPaths.writableLocation(QStandardPaths.TempLocation),
        "rfid_attendance.lock",
    )
    lock = QLockFile(lock_path)
    # 0 = 锁永不过期。QLockFile 自己会判断持锁进程是否还活着，进程没了锁自动作废；
    # 不设 0 的话锁超时后会被下一个实例强行抢占，结果就是同一台电脑上
    # 能同时开出两个窗口抢同一个串口读写器（一个能读卡、一个显示未连接，刷卡全乱）。
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        msg = QMessageBox(
            QMessageBox.Warning, APP_NAME,
            "考勤系统已经在运行了，不能再开第二个窗口。\n\n"
            "两个窗口会抢同一个串口读写器：\n"
            "· 后开的这个永远显示「未连接 / 连接失败」；\n"
            "· 先开的那个也会因为串口被抢而刷卡错乱（认错人、漏记）。\n\n"
            "请到任务栏切换到已经打开的考勤系统窗口使用它。\n"
            "如果确实找不到窗口，用任务管理器结束「启动考勤系统.exe」再启动。",
        )
        msg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        msg.show()
        msg.raise_()
        msg.activateWindow()
        msg.exec_()
        return 1

    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
