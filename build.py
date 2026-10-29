"""PyInstaller 打包：py -3.12-32 build.py
产出：dist/启动考勤系统.exe （单文件、32 位、无控制台）

入口必须是根目录的 run.py，不能直接用 app/main.py
（app 是包，内部相对导入在顶层脚本模式下会失败）。
"""
import os
import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = [
        os.path.join(HERE, "run.py"),
        f"--paths={HERE}",
        "--name=启动考勤系统",
        "--onefile",
        "--windowed",
        f"--add-binary={os.path.join(HERE, 'UHFReader18.dll')};.",
        "--hidden-import=app",
        "--hidden-import=app.main",
        "--hidden-import=app.database",
        "--hidden-import=app.reader",
        "--hidden-import=app.reader_worker",
        "--hidden-import=app.styles",
        "--hidden-import=app.widgets",
        "--hidden-import=app.voice",
        "--hidden-import=app.pages",
        "--hidden-import=app.pages.live",
        "--hidden-import=app.pages.people",
        "--hidden-import=app.pages.records",
        "--hidden-import=app.pages.settings",
        "--collect-all=PyQt5",
        "--noconfirm",
        "--clean",
    ]

    # 语音包（voice/*.wav）作为内置资源一起打包。
    # 运行时 exe 同级若也有 voice/ 目录，优先用那一边的，方便不重新打包就换音色。
    voice_dir = os.path.join(HERE, "voice")
    if os.path.isdir(voice_dir):
        args.append(f"--add-data={voice_dir}{os.pathsep}voice")

    PyInstaller.__main__.run(args)


if __name__ == "__main__":
    main()
