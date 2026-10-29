"""RFID 智能考勤系统 —— 唯一程序入口。

用法：
    py -3.12-32 run.py        # 开发模式直接运行
    py -3.12-32 build.py      # 打包成 dist/启动考勤系统.exe

注意：必须通过本文件启动，不要直接运行 ``app/main.py``。
``app`` 是包，内部使用相对导入（``from . import ...``），
若被当作顶层脚本执行会抛 ImportError。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.main import main

if __name__ == "__main__":
    main()
