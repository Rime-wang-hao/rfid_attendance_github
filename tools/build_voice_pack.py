# -*- coding: utf-8 -*-
"""生成刷卡语音包。

程序播报时会先去 voice/ 目录找现成的 wav，找不到才回退到系统语音。
这个脚本就是用来**批量产出那些 wav** 的。

用法：
    # 1) 只导出台词清单，不合成（拿去别的工具里做音色克隆）
    python tools/build_voice_pack.py --dump-only

    # 2) 用系统自带中文语音合成（离线、零依赖，音色 = Microsoft Huihui）
    python tools/build_voice_pack.py

    # 3) 指定发音人 / 语速，覆盖已有文件
    python tools/build_voice_pack.py --voice "Microsoft Huihui Desktop" --rate 0.2 --force

台词清单来自人员管理：每个人两句（签到 / 签退），外加一句未注册卡提示。
清单会写进 voice/manifest.csv，格式是「文件名,台词」。

想换成指定人声（例如某段录音的音色）：
    用任意支持音色克隆的工具（GPT-SoVITS / CosyVoice / 在线克隆服务，
    甚至自己录一遍）照着 manifest.csv 逐条生成 wav，
    按「文件名」这一列命名后放回 voice/ 目录即可，程序会自动优先使用，
    不需要改任何代码。
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.database import Database                          # noqa: E402
from app.voice import UNKNOWN_TEXT, pack_name, voice_dir   # noqa: E402

# 走 PowerShell 调 .NET 的 System.Speech 合成 wav——不需要装任何 Python 包。
_PS_TEMPLATE = r"""
param(
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [string]$VoiceName = "",
    [double]$Rate = 0.0
)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($VoiceName) {
        $found = $false
        foreach ($v in $synth.GetInstalledVoices()) {
            if ($v.VoiceInfo.Name -eq $VoiceName) { $found = $true; break }
        }
        if ($found) { $synth.SelectVoice($VoiceName) }
        else { Write-Host "WARN: voice not found: $VoiceName" }
    }
    # SAPI 的 Rate 是 -10 ~ 10，Qt 的是 -1.0 ~ 1.0
    $synth.Rate = [int][Math]::Round($Rate * 10)
    $synth.Volume = 100

    $lines = [System.IO.File]::ReadAllLines($ManifestPath, [System.Text.Encoding]::UTF8)
    $ok = 0
    foreach ($line in $lines) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split "`t", 2
        if ($parts.Count -lt 2) { continue }
        $name = $parts[0]
        $text = $parts[1]
        $out = Join-Path $OutDir $name
        $synth.SetOutputToWaveFile($out)
        $synth.Speak($text)
        $ok++
    }
    Write-Host "SYNTHESIZED=$ok"
}
finally {
    $synth.Dispose()
}
"""


def build_texts(db: Database) -> list:
    """按人员管理名单生成台词清单。"""
    texts = []
    for stu in db.list_students():
        name = (stu.name or "").strip()
        if not name:
            continue
        texts.append(f"{name}，签到成功")
        texts.append(f"{name}，签退成功")
    texts.append(UNKNOWN_TEXT)
    return texts


def write_manifest(out_dir: str, texts: list) -> str:
    """写出 voice/manifest.csv（文件名 ↔ 台词），方便人工/外部工具替换。"""
    path = os.path.join(out_dir, "manifest.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["文件名", "台词"])
        for t in texts:
            w.writerow([pack_name(t), t])
    return path


def synthesize(texts: list, out_dir: str, voice: str = "", rate: float = 0.0,
               force: bool = False) -> int:
    """调用系统语音合成 wav。返回实际合成条数。"""
    pending = [t for t in texts
               if force or not os.path.isfile(os.path.join(out_dir, pack_name(t)))]
    if not pending:
        return 0

    tmp_manifest = os.path.join(tempfile.gettempdir(), "rfid_voice_manifest.tsv")
    with open(tmp_manifest, "w", encoding="utf-8", newline="") as f:
        for t in pending:
            f.write(f"{pack_name(t)}\t{t}\n")

    tmp_ps = os.path.join(tempfile.gettempdir(), "rfid_voice_synth.ps1")
    with open(tmp_ps, "w", encoding="utf-8") as f:
        f.write(_PS_TEMPLATE)

    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp_ps,
        "-ManifestPath", tmp_manifest, "-OutDir", out_dir,
    ]
    if voice:
        cmd += ["-VoiceName", voice]
    cmd += ["-Rate", str(rate)]

    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        print("!! 合成失败：", proc.stderr or proc.stdout, file=sys.stderr)
        return 0
    print(proc.stdout.strip())

    for f in (tmp_manifest, tmp_ps):
        try:
            os.remove(f)
        except OSError:
            pass

    return sum(1 for t in pending if os.path.isfile(os.path.join(out_dir, pack_name(t))))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成刷卡语音包")
    ap.add_argument("--voice", default="", help="发音人名字，留空=系统默认中文")
    ap.add_argument("--rate", type=float, default=0.0, help="语速 -1.0 ~ 1.0")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的音频")
    ap.add_argument("--dump-only", action="store_true", help="只导出清单不合成")
    args = ap.parse_args()

    db = Database()
    texts = build_texts(db)
    if not texts:
        print("人员管理里还没有登记任何人，先去登记再来生成。")
        return 1

    out_dir = voice_dir()
    os.makedirs(out_dir, exist_ok=True)
    manifest = write_manifest(out_dir, texts)

    print(f"台词 {len(texts)} 条 | 输出目录：{out_dir}")
    print(f"清单：{manifest}")

    if args.dump_only:
        print("\n（--dump-only：未合成，可拿 manifest.csv 去外部工具做音色克隆）")
        return 0

    n = synthesize(texts, out_dir, voice=args.voice, rate=args.rate, force=args.force)
    print(f"\n完成：新增 {n} 条，语音包共覆盖 {len(texts)} 条台词。")
    if args.voice == "":
        print("说明：当前用的是系统自带中文语音（Microsoft Huihui）。\n"
              "      要换成别的音色，见本文件开头注释里的替换方法。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
