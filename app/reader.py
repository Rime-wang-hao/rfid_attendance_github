# -*- coding: utf-8 -*-
"""
UHF 读写器 ctypes 封装。

功能：
    * 自动定位 UHFReader18.dll（PyInstaller 单文件模式下从 _MEIPASS 抽取目录加载）
    * 打开/关闭串口（自动尝试多种波特率）
    * 读取读写器信息
    * 盘点 EPC 标签

说明：
    UHFReader18.dll 是 32 位原生库，因此本模块必须配合 32 位 Python 使用。
    打包后的 .exe 也必须是 32 位。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import List, Optional


# DLL 波特率代码（与 C# 演示源码完全一致）
# 顺序按「读写器出厂默认 57600」优先，能更快连上，失败再往下试
BAUD_CODES = [
    (5, "57600"),
    (6, "115200"),
    (2, "38400"),
    (1, "19200"),
    (0, "9600"),
]

# 返回码
RET_OK = 0x00
RET_COMM_ERR = 0x30
RET_PORT_BUSY = 0x35
RET_NO_TAG = 0xFB

DEFAULT_COM_ADDR = 0xFF


def _dll_path() -> str:
    """定位 UHFReader18.dll。开发模式与 PyInstaller 模式通用。"""
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile 模式下，DLL 在临时解压目录 _MEIPASS
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "UHFReader18.dll")


class PortBusyError(RuntimeError):
    """串口不存在，或者已被其它程序占用。"""


class NoReaderError(RuntimeError):
    """串口能打开，但读写器没有应答（波特率/线路/供电问题）。"""


# --------------------------- 串口可用性检查 ---------------------------
# 用 Win32 API 独占打开一次 COM 口：这样就能把「端口不存在 / 被占用」
# 和「端口开了但读写器不应答」两种情况区分开，不再一律报成通讯错误。
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_GENERIC_RW = 0x80000000 | 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = -1


def _open_port_handle(port: int) -> int:
    if port <= 0:
        return _INVALID_HANDLE
    _kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    _kernel32.CreateFileW.restype = ctypes.c_ssize_t
    _kernel32.CloseHandle.argtypes = [ctypes.c_ssize_t]
    _kernel32.CloseHandle.restype = ctypes.c_int
    return _kernel32.CreateFileW(
        f"\\\\.\\COM{port}", _GENERIC_RW, 0, None, _OPEN_EXISTING, 0, None,
    )


def is_port_available(port: int) -> bool:
    """COM 口存在且未被其它程序占用时返回 True。"""
    h = _open_port_handle(port)
    if h == _INVALID_HANDLE or h == 0:
        return False
    _kernel32.CloseHandle(h)
    return True


def list_serial_ports() -> List[int]:
    """列出系统当前存在的串口号（注册表 DEVICEMAP\\SERIALCOMM，失败则暴力探测）。"""
    ports: List[int] = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            i = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                name = str(value).upper()
                if name.startswith("COM"):
                    try:
                        ports.append(int(name[3:]))
                    except ValueError:
                        pass
    except Exception:
        ports = []
    if not ports:
        ports = [p for p in range(1, 33) if is_port_available(p)]
    return sorted(set(ports))


def _parse_epc_buffer(raw: bytes, cardnum: int) -> List[str]:
    """解析 Inventory_G2 返回的缓冲区。

    缓冲区格式：每条记录 = [长度字节 L] + [L 字节 EPC 数据]
    EPC 既可能是 ASCII 十六进制（直接解出），也可能是二进制（需 hex 编码）。
    """
    epcs: List[str] = []
    pos = 0
    for _ in range(cardnum):
        if pos >= len(raw):
            break
        length = raw[pos]
        rec = raw[pos + 1: pos + 1 + length]
        pos += length + 1
        if len(rec) != length or length == 0:
            continue
        # 优先尝试 ASCII 十六进制
        try:
            text = rec.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        if text and all(c in "0123456789abcdefABCDEF" for c in text):
            epcs.append(text.upper())
        else:
            epcs.append(rec.hex().upper())
    return epcs


@dataclass
class ReaderInfo:
    version: str
    address: int
    reader_type: int
    power_dbm: int = 0      # 当前发射功率（dBm）


class UHFReader:
    """对 UHFReader18.dll 的高层封装。所有方法线程安全（内部有锁）。"""

    def __init__(self, dll_path: Optional[str] = None) -> None:
        path = dll_path or _dll_path()
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"找不到 {os.path.basename(path)}，请确认它与本程序在同一目录下。\n"
                f"期望路径：{path}"
            )
        try:
            self._dll = ctypes.WinDLL(path)
        except OSError as e:
            raise OSError(
                f"加载 {os.path.basename(path)} 失败：{e}\n"
                "最常见原因：当前 Python 是 64 位而 DLL 是 32 位。"
                "请确认使用 32 位 Python 运行 / 打包为 32 位 exe。"
            ) from e
        self._lock = threading.RLock()
        self._handle = ctypes.c_long(-1)
        self._setup_prototypes()

    def _setup_prototypes(self) -> None:
        d = self._dll
        d.OpenComPort.argtypes = [
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_long),
        ]
        d.OpenComPort.restype = ctypes.c_long
        d.CloseComPort.argtypes = []
        d.CloseComPort.restype = ctypes.c_long
        # long SetPowerDbm(byte* ComAdr, byte PowerDbm, long frmComPortindex)
        # 官方 C# 演示源码：SetPowerDbm(ref fComAdr, powerDbm, frmcomportindex)
        # powerDbm 取值 0~30（固件版本 >= 2.30 时上限 30，老固件上限 18）
        d.SetPowerDbm.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_ubyte, ctypes.c_long,
        ]
        d.SetPowerDbm.restype = ctypes.c_long
        d.GetReaderInformation.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_long,
        ]
        d.GetReaderInformation.restype = ctypes.c_long
        d.Inventory_G2.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
            ctypes.c_long,
        ]
        d.Inventory_G2.restype = ctypes.c_long
        # long WriteEPC_G2(byte* ComAdr, byte* Password, byte* WriteEPC,
        #                 byte WriteEPClen, byte* errorcode, long FrmHandle)
        # 官方手册签名：写 EPC 区（发卡）。注意它**不指定源 EPC**，
        # 写的是天线上的卡 —— 所以写卡时感应区必须只有一张卡。
        d.WriteEPC_G2.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_long,
        ]
        d.WriteEPC_G2.restype = ctypes.c_long

    def open(self, port: int) -> str:
        """打开指定串口（1~255），自动尝试 5 种波特率。返回实际使用的波特率名称。"""
        with self._lock:
            self._close_locked()
            if not is_port_available(port):
                raise PortBusyError(
                    f"COM{port} 打不开：端口不存在，或已被其它程序占用。\n"
                    "请先关闭官方演示软件 UHFReader18demomain.exe、串口调试助手，"
                    "以及多余的考勤系统窗口。"
                )
            for code, name in BAUD_CODES:
                # 每次都要用一个全新的 0xFF：DLL 会把读写器实际地址写回这块内存，
                # 复用同一个变量会让后面的尝试带着错误地址去寻址，怎么试都连不上。
                com_adr = ctypes.c_ubyte(DEFAULT_COM_ADDR)
                handle = ctypes.c_long(-1)
                ret = self._dll.OpenComPort(
                    port, ctypes.byref(com_adr), code, ctypes.byref(handle)
                )
                if ret == RET_PORT_BUSY:
                    self._release_locked(handle)
                    raise PortBusyError(
                        f"COM{port} 已被其他程序占用！\n"
                        "请先关闭官方演示软件 UHFReader18demomain.exe 等串口程序。"
                    )
                if ret == RET_OK:
                    self._handle = handle
                    return name
                # 失败的尝试也可能已经开了句柄，必须关掉，否则会一直占着串口
                self._release_locked(handle)
            raise NoReaderError(
                f"COM{port} 能打开，但读写器没有应答：\n"
                "请确认读写器已上电、串口线插在 RS232 口（不是 485/韦根）、天线已接好。"
            )

    def open_auto(self, ports: Optional[List[int]] = None) -> tuple:
        """自动扫描串口：返回 (串口号, 波特率)。ports 为 None 时扫描系统全部串口。"""
        candidates = ports or list_serial_ports()
        if not candidates:
            candidates = [p for p in range(1, 33)]
        last_err: Optional[Exception] = None
        for p in candidates:
            try:
                return p, self.open(p)
            except (PortBusyError, NoReaderError) as e:
                last_err = e
                continue
        raise NoReaderError(
            "自动扫描完毕，没有找到读写器。\n"
            f"已扫描串口：{', '.join('COM' + str(p) for p in candidates)}\n"
            f"最后一次错误：{last_err}"
        )

    def _release_locked(self, handle) -> None:
        """关掉一次尝试中可能已经打开的句柄（防句柄泄漏把串口占死）。"""
        if handle is not None and handle.value != -1:
            self._handle = handle
            self._close_locked()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._handle.value != -1:
            try:
                self._dll.CloseComPort()
            except OSError:
                pass
            self._handle = ctypes.c_long(-1)

    @property
    def is_open(self) -> bool:
        return self._handle.value != -1

    def get_info(self) -> Optional[ReaderInfo]:
        with self._lock:
            if self._handle.value == -1:
                return None
            com_adr = ctypes.c_ubyte(DEFAULT_COM_ADDR)
            version = ctypes.create_string_buffer(2)
            reader_type = ctypes.c_ubyte(0)
            tr_type = ctypes.create_string_buffer(2)
            dmax = ctypes.c_ubyte(0)
            dmin = ctypes.c_ubyte(0)
            power = ctypes.c_ubyte(0)
            scan = ctypes.c_ubyte(0)
            ret = self._dll.GetReaderInformation(
                ctypes.byref(com_adr), version, ctypes.byref(reader_type), tr_type,
                ctypes.byref(dmax), ctypes.byref(dmin), ctypes.byref(power),
                ctypes.byref(scan), self._handle,
            )
            if ret != RET_OK:
                return None
            ver_hex = version.raw[:2].hex().upper()
            return ReaderInfo(
                version=ver_hex, address=com_adr.value,
                reader_type=reader_type.value, power_dbm=power.value,
            )

    def set_power(self, dbm: int) -> bool:
        """设置发射功率（0~30 dBm）。功率越小 → 读卡距离越近。成功返回 True。"""
        dbm = max(0, min(30, int(dbm)))
        with self._lock:
            if self._handle.value == -1:
                return False
            com_adr = ctypes.c_ubyte(DEFAULT_COM_ADDR)
            ret = self._dll.SetPowerDbm(
                ctypes.byref(com_adr), ctypes.c_ubyte(dbm), self._handle,
            )
            return ret == RET_OK

    def get_power(self) -> Optional[int]:
        """读回当前发射功率（dBm），读不到返回 None。"""
        info = self.get_info()
        return None if info is None else info.power_dbm

    def inventory(self) -> List[str]:
        """盘点一次，返回当前天线范围内所有 EPC 字符串。无标签时返回空列表。"""
        with self._lock:
            if self._handle.value == -1:
                return []
            com_adr = ctypes.c_ubyte(DEFAULT_COM_ADDR)
            buf = ctypes.create_string_buffer(60000)
            totallen = ctypes.c_long(0)
            cardnum = ctypes.c_long(0)
            ret = self._dll.Inventory_G2(
                ctypes.byref(com_adr), 0, 0, 0, buf,
                ctypes.byref(totallen), ctypes.byref(cardnum), self._handle,
            )
            if ret not in (0x01, 0x02, 0x03, 0x04):
                return []
            return _parse_epc_buffer(buf.raw[: totallen.value], cardnum.value)

    def write_epc(self, epc_hex: str, password: Optional[bytes] = None
                  ) -> tuple[bool, str]:
        """给天线上的卡片写入新的 EPC（发卡）。

        返回 (成功?, 说明文字)。

        ⚠ 写卡不可逆：原卡号立即失效。
        ⚠ SDK 的 WriteEPC_G2 不带源 EPC 参数 —— 写的是**天线上的那张卡**。
           因此调用方必须先确认感应区里只有一张卡，否则会写错卡。

        空密码默认 6 字节全 0（出厂默认）。
        """
        epc_hex = (epc_hex or "").strip().upper()
        if not epc_hex or len(epc_hex) % 2:
            return False, "EPC 必须是偶数位十六进制字符串"
        try:
            data = bytes.fromhex(epc_hex)
        except ValueError:
            return False, "EPC 含非十六进制字符"
        if not (4 <= len(data) <= 24):
            return False, "EPC 长度需在 4~24 字节之间"

        with self._lock:
            if self._handle.value == -1:
                return False, "读写器未连接"

            # 只读机型的 SDK 没有写卡接口 —— 直接拦下，别去调不存在的函数
            if not hasattr(self._dll, "WriteEPC_G2"):
                return False, "当前读写器 SDK 不支持写卡（没有 WriteEPC_G2 接口）"

            # 写卡前确认：感应区必须只有一张卡
            try:
                current = self.inventory()
            except Exception as e:
                return False, f"读卡失败，无法确认感应区状态：{e}"
            if len(current) != 1:
                if not current:
                    return False, "感应区没有卡，请把要发卡的卡片放到天线上"
                return False, (f"感应区有 {len(current)} 张卡"
                               f"（{'、'.join(current)}），写卡只对一张卡生效，"
                               f"请只保留一张")

            com_adr = ctypes.c_ubyte(DEFAULT_COM_ADDR)
            pwd_buf = ctypes.create_string_buffer(password or b"\x00" * 6, 6)
            epc_buf = (ctypes.c_ubyte * len(data))(*data)
            err = ctypes.c_ubyte(0)
            try:
                ret = self._dll.WriteEPC_G2(
                    ctypes.byref(com_adr), pwd_buf, epc_buf, len(data),
                    ctypes.byref(err), self._handle,
                )
            except Exception as e:
                return False, f"调用写卡接口出错：{e}"

            if ret != RET_OK or err.value != 0:
                return False, (f"写卡失败（返回码 {ret:#04x}，"
                               f"错误码 {err.value}）")

            # ★ 写完立刻回读验证：确认新卡号真的生效了
            ok, msg = self._verify_epc(epc_hex)
            if not ok:
                return False, msg
            return True, f"写入成功，卡号已变为 {epc_hex}"

    def _verify_epc(self, epc_hex: str, tries: int = 6) -> tuple[bool, str]:
        """回读几次，确认天线上的卡现在是指定卡号。"""
        want = epc_hex.upper()
        for i in range(tries):
            try:
                cur = self.inventory()
            except Exception:
                cur = []
            if want in [e.upper() for e in cur]:
                return True, ""
            if i < tries - 1:
                time.sleep(0.35)
        return False, ("写卡接口返回成功，但回读到的卡号不是新卡号，"
                       "请检查卡片是否支持改写 EPC 区")
