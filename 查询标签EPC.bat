@echo off
chcp 65001 >nul
title 查询标签EPC（登记学生用）
cd /d "%~dp0"
echo 提示：请先关闭官方演示软件 UHFReader18demomain.exe，再按任意键开始...
pause >nul
"D:\Python312-32\python.exe" attendance_system.py --query
pause
