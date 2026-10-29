# -*- coding: utf-8 -*-
"""
学生签到签退考勤系统（UHFReader18 超高频读写器）
================================================
功能说明：
    1. 学生第一次刷卡 -> 签到（记录时间）
    2. 学生第二次刷卡 -> 签退（自动计算上课时长 = 两次刷卡时间差）
    3. 学生第三次刷卡 -> 视为新一次签到（开始下一节课），以此类推
    4. EPC 与学生的对应关系在 students.txt 中维护（记事本即可编辑）
    5. 考勤结果自动追加保存到 考勤记录.csv（可用 Excel / WPS 直接打开）

使用方法：
    python attendance_system.py           启动考勤系统（默认 COM3 串口）
    python attendance_system.py COM5      指定其他串口
    python attendance_system.py --query   查询模式：只显示读到的标签 EPC（用于登记学生）

注意：本程序必须使用 32 位 Python 运行（因为 UHFReader18.dll 是 32 位库），
     且运行时必须先关闭官方演示软件 UHFReader18demomain.exe，否则串口会被占用。
"""

import ctypes
import csv
import os
import sys
import time
from datetime import datetime

try:
    import winsound  # Windows 蜂鸣提示音
except ImportError:
    winsound = None

# ==================== 配置区（可按需修改） ====================
DLL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "UHFReader18.dll")
STUDENTS_FILE = "students.txt"    # 学生名单文件：每行格式  EPC,学号,姓名
RECORD_FILE = "考勤记录.csv"      # 考勤结果输出文件
COOLDOWN = 10                     # 同一张卡两次刷卡的最小间隔（秒）。
                                  # 卡片放在天线上会被连续读到，间隔小于该值不算新的一次刷卡
POLL_INTERVAL = 0.3               # 轮询读卡间隔（秒）
COM_ADDR = 0xFF                   # 读写器地址，0xFF 为广播地址（默认值，不用改）
# =============================================================

# DLL 波特率代码表（对应关系来自官方演示软件源码，代码含义：0=9600 1=19200 2=38400 5=57600 6=115200）
BAUD_TABLE = [
    (6, "115200"),
    (5, "57600"),
    (2, "38400"),
    (1, "19200"),
    (0, "9600"),
]

# DLL 返回码含义（节选）
RET_MSG = {
    0x00: "成功",
    0x01: "成功(返回标签较少)",
    0x02: "成功(返回标签较多)",
    0x03: "成功(返回字节数较多)",
    0x04: "成功(返回字节数较少)",
    0x30: "串口通讯错误",
    0x35: "串口已被占用",
    0xFB: "无标签",
}


# ==================== 读写器 DLL 封装 ====================
class UHFReader:
    """封装 UHFReader18.dll 的基本操作"""

    def __init__(self, dll_path):
        if not os.path.exists(dll_path):
            raise FileNotFoundError("找不到 UHFReader18.dll，请确认它与本脚本在同一目录！")
        try:
            self.dll = ctypes.WinDLL(dll_path)  # stdcall 调用约定
        except OSError as e:
            raise OSError(
                "加载 UHFReader18.dll 失败：%s\n"
                "最常见原因：当前 Python 是 64 位，而 DLL 是 32 位。请安装 32 位 Python 后再运行。" % e
            )
        self.handle = ctypes.c_long(-1)  # 打开串口后得到的句柄
        self._setup_prototypes()

    def _setup_prototypes(self):
        d = self.dll
        # long OpenComPort(long Port, byte* ComAdr, byte Baud, long* frmComPortindex)
        d.OpenComPort.argtypes = [ctypes.c_long, ctypes.POINTER(ctypes.c_ubyte),
                                  ctypes.c_ubyte, ctypes.POINTER(ctypes.c_long)]
        d.OpenComPort.restype = ctypes.c_long
        # long CloseComPort()
        d.CloseComPort.argtypes = []
        d.CloseComPort.restype = ctypes.c_long
        # long GetReaderInformation(byte* ComAdr, char* VersionInfo, byte* ReaderType,
        #                           char* TrType, byte* dmaxfre, byte* dminfre,
        #                           byte* powerdBm, byte* ScanTime, long frmComPortindex)
        d.GetReaderInformation.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_char_p,
                                           ctypes.POINTER(ctypes.c_ubyte), ctypes.c_char_p,
                                           ctypes.POINTER(ctypes.c_ubyte),
                                           ctypes.POINTER(ctypes.c_ubyte),
                                           ctypes.POINTER(ctypes.c_ubyte),
                                           ctypes.POINTER(ctypes.c_ubyte), ctypes.c_long]
        d.GetReaderInformation.restype = ctypes.c_long
        # long Inventory_G2(byte* ComAdr, byte AdrTID, byte LenTID, byte TIDFlag,
        #                   char* EPClenandEPC, long* Totallen, long* CardNum, long frmComPortindex)
        d.Inventory_G2.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_ubyte,
                                   ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_char_p,
                                   ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
                                   ctypes.c_long]
        d.Inventory_G2.restype = ctypes.c_long

    def open(self, port):
        """打开指定串口，自动尝试各种波特率。成功返回 True。"""
        com_adr = ctypes.c_ubyte(COM_ADDR)
        for code, name in BAUD_TABLE:
            self.handle = ctypes.c_long(-1)
            ret = self.dll.OpenComPort(port, ctypes.byref(com_adr), code,
                                       ctypes.byref(self.handle))
            if ret == 0:
                print("串口 COM%d 已连接（波特率 %s）" % (port, name))
                return True
            if ret == 0x35:
                raise RuntimeError("COM%d 已被其他程序占用！请先关闭官方演示软件等串口程序。" % port)
        return False

    def close(self):
        if self.handle.value != -1:
            self.dll.CloseComPort()
            self.handle = ctypes.c_long(-1)

    def get_info(self):
        """读取读写器信息，用于确认连接正常。"""
        com_adr = ctypes.c_ubyte(COM_ADDR)
        version = ctypes.create_string_buffer(2)
        reader_type = ctypes.c_ubyte(0)
        tr_type = ctypes.create_string_buffer(2)
        dmax, dmin, power, scan = (ctypes.c_ubyte(0) for _ in range(4))
        ret = self.dll.GetReaderInformation(
            ctypes.byref(com_adr), version, ctypes.byref(reader_type), tr_type,
            ctypes.byref(dmax), ctypes.byref(dmin), ctypes.byref(power),
            ctypes.byref(scan), self.handle)
        if ret != 0:
            return None
        return {
            "版本": version.raw[:2].hex().upper(),
            "读写器地址": "%02X" % com_adr.value,
        }

    def inventory(self):
        """盘点一次天线范围内的标签，返回 EPC 字符串列表（读不到返回空列表）。"""
        com_adr = ctypes.c_ubyte(COM_ADDR)
        buf = ctypes.create_string_buffer(60000)
        totallen = ctypes.c_long(0)
        cardnum = ctypes.c_long(0)
        ret = self.dll.Inventory_G2(
            ctypes.byref(com_adr), 0, 0, 0, buf,
            ctypes.byref(totallen), ctypes.byref(cardnum), self.handle)
        if ret not in (1, 2, 3, 4):  # 0xFB 表示无标签，其他为通讯错误
            return []
        return parse_epc_buffer(buf.raw[:totallen.value], cardnum.value)


def parse_epc_buffer(data, cardnum):
    """
    解析 Inventory_G2 返回的缓冲区。
    缓冲区格式：每条记录 = [长度字节 L] + [L 字节 EPC 数据]。
    兼容两种情况：EPC 以二进制存储（转成十六进制字符串）或以 ASCII 十六进制存储（直接使用）。
    """
    epcs = []
    m = 0
    for _ in range(cardnum):
        if m >= len(data):
            break
        length = data[m]
        rec = data[m + 1: m + 1 + length]
        m += length + 1
        if len(rec) != length:
            break
        try:
            text = rec.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        if text and all(c in "0123456789abcdefABCDEF" for c in text):
            epc = text.upper()      # ASCII 十六进制格式
        else:
            epc = rec.hex().upper()  # 二进制格式
        if epc:
            epcs.append(epc)
    return epcs


# ==================== 考勤业务逻辑 ====================
class Attendance:
    """签到签退逻辑：第一次刷卡=签到，第二次刷卡=签退并计算时长"""

    def __init__(self):
        self.students = load_students()      # epc -> (学号, 姓名)
        self.swipes = {}                     # epc -> [datetime, ...] 每次刷卡时间
        self.last_seen = {}                  # epc -> 上次读到的时间戳（用于防重复计数）
        self.pending = {}                    # epc -> 签到 datetime（等待签退的会话）
        self._writer = None                  # csv.writer，由 main() 通过 set_writer 注入
        self._file = None                    # 对应的文件句柄（用于 flush）

    def student_of(self, epc):
        info = self.students.get(epc)
        if info:
            return info[0], info[1]
        return "未登记", "未登记（请将EPC加入students.txt: %s）" % epc

    def beep(self):
        if winsound:
            try:
                winsound.Beep(1200, 150)
            except Exception:
                pass

    def on_tag_seen(self, epc):
        """每次盘点到某张卡时调用。超过冷却间隔才认定为一次新的刷卡。"""
        now = time.time()
        if now - self.last_seen.get(epc, 0) < COOLDOWN:
            self.last_seen[epc] = now       # 卡还在天线上，刷新时间，不算新刷卡
            return
        self.last_seen[epc] = now
        self.on_swipe(epc)

    def set_writer(self, writer, file_handle):
        """由 main() 调用，注入用于实时写 CSV 的 writer 和文件句柄。"""
        self._writer = writer
        self._file = file_handle

    def on_swipe(self, epc):
        now = datetime.now()
        sid, name = self.student_of(epc)
        times = self.swipes.setdefault(epc, [])
        times.append(now)
        self.beep()
        if len(times) % 2 == 1:
            # 奇数次刷卡 -> 签到
            self.pending[epc] = now
            print("[%s] 签到  %s %s  %s" % (now.strftime("%H:%M:%S"), sid, name, epc))
        else:
            # 偶数次刷卡 -> 签退，计算时长并保存
            start = self.pending.pop(epc, None)
            if start is None:
                start = times[-2] if len(times) >= 2 else now
            duration = now - start
            minutes = duration.total_seconds() / 60.0
            print("[%s] 签退  %s %s  上课时长：%.1f 分钟（%s ~ %s）"
                  % (now.strftime("%H:%M:%S"), sid, name, minutes,
                     start.strftime("%H:%M:%S"), now.strftime("%H:%M:%S")))
            save_record(self, sid, name, epc, start, now, minutes, "完成")

    def finish_unfinished(self):
        """程序退出时，把只签到未签退的记录也保存（状态=未签退）。"""
        for epc, start in self.pending.items():
            sid, name = self.student_of(epc)
            save_record(self, sid, name, epc, start, None, None, "未签退")
        self.pending.clear()


def open_record_file():
    """打开考勤记录 CSV 并返回 writer。文件被 Excel 占用时给出明确提示。"""
    try:
        f = open(RECORD_FILE, "a", newline="", encoding="utf-8-sig")
    except PermissionError:
        raise PermissionError(
            "无法打开 %s：文件正在被其他程序（很可能是 Excel/WPS）占用，\n"
            "请先关闭后再启动考勤程序。" % RECORD_FILE
        )
    writer = csv.writer(f)
    if f.tell() == 0:  # 新文件，写入表头
        writer.writerow(["学号", "姓名", "标签EPC", "签到时间", "签退时间",
                         "上课时长(分钟)", "状态", "记录生成时间"])
        f.flush()
    return f, writer


def save_record(att, sid, name, epc, start, end, minutes, status):
    """通过 Attendance 持有的 writer 把一条考勤结果写入 CSV。"""
    if att._writer is None:
        return
    att._writer.writerow([sid, name, epc,
                          start.strftime("%Y-%m-%d %H:%M:%S") if start else "",
                          end.strftime("%Y-%m-%d %H:%M:%S") if end else "",
                          "%.1f" % minutes if minutes is not None else "",
                          status,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    if att._file:
        att._file.flush()  # 每条立即落盘，意外中断也不会丢数据


def load_students():
    """读取学生名单。文件格式：每行  EPC,学号,姓名（# 开头为注释行）"""
    students = {}
    if not os.path.exists(STUDENTS_FILE):
        return students
    with open(STUDENTS_FILE, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                students[parts[0].upper()] = (parts[1], parts[2])
    return students


# ==================== 主程序 ====================
def query_mode(reader):
    """查询模式：持续显示读到的标签 EPC，用于登记学生。Ctrl+C 退出。"""
    print("\n===== 查询模式：请将标签放到读写器天线上 =====")
    print("把下面显示的 EPC 复制到 students.txt 中即可完成学生登记\n")
    shown = {}
    try:
        while True:
            for epc in reader.inventory():
                if time.time() - shown.get(epc, 0) > 3:  # 同一张卡 3 秒内只提示一次
                    shown[epc] = time.time()
                    print("[%s] 读到标签 EPC: %s" % (datetime.now().strftime("%H:%M:%S"), epc))
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n已退出查询模式。")


def main():
    # 处理命令行参数
    args = [a for a in sys.argv[1:] if a != "--query"]
    query_only = "--query" in sys.argv
    port = int(args[0].upper().replace("COM", "")) if args else 3  # 默认 COM3

    print("=" * 56)
    print("        UHFReader18 学生签到签退考勤系统")
    print("=" * 56)

    reader = UHFReader(DLL_FILE)
    if not reader.open(port):
        print("连接 COM%d 失败！请检查：1) 读写器 USB 线是否插好；"
              "2) 串口号是否正确；3) 是否已关闭官方演示软件。" % port)
        return
    rec_file = None
    try:
        info = reader.get_info()
        if info:
            print("读写器连接成功！固件版本: %(版本)s，地址: %(读写器地址)s" % info)
        else:
            print("警告：无法读取读写器信息，请检查设备连接。")

        if query_only:
            query_mode(reader)
            return

        # 打开考勤记录文件（一次打开，全程复用，避免 Excel 占用期间反复报错）
        rec_file, rec_writer = open_record_file()
        print("考勤结果将实时写入：%s" % os.path.abspath(RECORD_FILE))

        att = Attendance()
        att.set_writer(rec_writer, rec_file)
        if not att.students:
            print("提示：students.txt 中还没有登记学生，请先运行 "
                  "`python attendance_system.py --query` 获取 EPC 并登记。")
        print("已登记学生 %d 人。考勤进行中，按 Ctrl+C 结束并保存……\n" % len(att.students))
        print("-" * 56)
        try:
            while True:
                for epc in reader.inventory():
                    att.on_tag_seen(epc)
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n" + "-" * 56)
            print("考勤结束，正在保存未签退的记录……")
            att.finish_unfinished()
            print("考勤结果已保存到：%s" % os.path.abspath(RECORD_FILE))
    finally:
        reader.close()
        if rec_file is not None:
            rec_file.close()
        print("串口已关闭。")


if __name__ == "__main__":
    main()
