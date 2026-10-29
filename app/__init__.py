"""RFID 考勤系统 GUI 版本。"""
import os
import sys

__version__ = "1.0.0"
APP_NAME = "RFID 智能考勤系统"


def app_data_dir() -> str:
    """持久数据目录：exe 所在目录，开发模式为项目根目录。

    数据库、语音包这类需要跟着程序走、又要在打包后仍然可写的东西都放这里。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
