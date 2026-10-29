from PyQt5.QtCore import QThread, pyqtSignal
import queue

from .reader import UHFReader


class ReaderWorker(QThread):
    """后台读写线程。

    行为：
        * port 为空/0 时自动扫描系统所有串口，不再要求用户填对 COM 号；
        * 连接失败不会放弃，按 retry_interval 自动重试（关掉占用串口的程序后能自愈）；
        * 指定的 COM 号连不上时，后续重试自动改为全盘扫描；
        * 每次失败都会把可能已打开的串口句柄关掉，避免把串口占死。
    """

    tagRead = pyqtSignal(str)
    readerStateChanged = pyqtSignal(str, str)
    inventoryChanged = pyqtSignal(list)     # 天线内当前所有标签（排查用）
    epcWritten = pyqtSignal(bool, str, str)  # (成功?, 新卡号, 说明)

    def __init__(self, port=None, poll_interval=0.3, retry_interval=3.0,
                 power=None):
        super().__init__()
        self._port = port or None          # None = 自动扫描
        self._poll = poll_interval
        self._retry = retry_interval
        self._power = power                # 发射功率 dBm，None = 不改
        self._pending_power = None         # 运行中改功率时，下一轮盘点生效
        self._running = False
        self._reader = None
        self._last_epcs = set()
        self._write_q = queue.Queue()      # 发卡请求队列

    def set_power(self, dbm):
        """运行期间改功率：由设置页保存后调用，下一轮盘点自动下发到读写器。"""
        self._power = dbm
        self._pending_power = dbm

    def request_write_epc(self, epc_hex: str) -> None:
        """请求给天线上的卡写入新 EPC（发卡）。线程安全。"""
        self._write_q.put((epc_hex or "").strip().upper())

    def _drain_write_requests(self) -> None:
        """处理排队的写卡请求（写卡与盘点串行，避免抢串口）。"""
        while True:
            try:
                epc_hex = self._write_q.get_nowait()
            except queue.Empty:
                return
            if not epc_hex:
                continue
            try:
                ok, msg = self._reader.write_epc(epc_hex)
            except Exception as e:
                ok, msg = False, f"写卡出错：{e}"
            self.epcWritten.emit(ok, epc_hex, msg)
            # 写卡后立刻同步一次感应区状态（不重置 _last_epcs，
            # 否则所有卡都会被当成"新出现的"重复触发刷卡）
            try:
                self.inventoryChanged.emit(sorted(self._reader.inventory()))
            except Exception:
                pass

    def _apply_power(self) -> str:
        """把功率下发到读写器，返回要追加到状态文案里的提示（正常为空串）。"""
        dbm = self._pending_power if self._pending_power is not None else self._power
        self._pending_power = None
        if dbm is None:
            return ""
        try:
            if self._reader.set_power(dbm):
                return ""
            return f"（功率设置失败，仍为 {self._reader.get_power()} dBm）"
        except Exception:
            return ""

    def stop(self):
        self._running = False
        if self.isRunning():
            self.wait(10000)

    # 可被 stop() 打断的等待，返回是否还要继续跑
    def _sleep(self, seconds: float) -> bool:
        for _ in range(int(seconds * 10)):
            if not self._running:
                return False
            self.msleep(100)
        return self._running

    def run(self):
        self._running = True
        try:
            self._reader = UHFReader()
        except Exception as e:
            self.readerStateChanged.emit(f"连接失败：{e}", "err")
            return

        use_auto = self._port is None
        while self._running:
            # ---------------- 连接 ----------------
            try:
                if use_auto:
                    port, baud = self._reader.open_auto()
                else:
                    port, baud = self._port, self._reader.open(self._port)
            except Exception as e:
                msg = f"{e}"
                if self._running:
                    msg += f"（{int(self._retry)} 秒后自动重试…）"
                self.readerStateChanged.emit(f"连接失败：{msg}", "err")
                # 指定串口连不上 → 下次改成扫描全部串口
                use_auto = True
                if not self._sleep(self._retry):
                    break
                continue

            msg = f"已连接 COM{port}（{baud}）"
            tip = self._apply_power()          # 连上后立刻按设置调整发射功率
            self.readerStateChanged.emit(
                msg + tip, "warn" if tip else "ok",
            )
            self._last_epcs = set()

            # ---------------- 盘点 ----------------
            while self._running:
                if self._pending_power is not None:
                    self._apply_power()        # 运行中被改了功率，立即下发
                # 发卡（写卡）请求优先处理，写卡期间不盘点
                if not self._write_q.empty():
                    self._drain_write_requests()
                    continue
                try:
                    epcs = self._reader.inventory()
                except Exception:
                    break
                cur = set(epcs)
                for epc in cur - self._last_epcs:
                    self.tagRead.emit(epc)
                self._last_epcs = cur
                # 天线内当前所有标签：实时告诉界面"读写器此刻读到了几张卡"，
                # 排查"刷了没反应 / 认错人"时非常有用
                self.inventoryChanged.emit(sorted(cur))
                self.msleep(int(self._poll * 1000))

            try:
                self._reader.close()
            except Exception:
                pass
            if self._running:
                self.readerStateChanged.emit("连接已断开，正在重连…", "off")
                if not self._sleep(self._retry):
                    break

        self.readerStateChanged.emit("未连接", "off")
