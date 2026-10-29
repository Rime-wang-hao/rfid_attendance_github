# -*- coding: utf-8 -*-
"""刷卡语音播报。

两条音源，按优先级取用：

  1. 音色包   —— voice/ 目录下的 wav。这些文件是用参考人声做音色克隆后
     离线预生成的，音色与参考人声一致（见 tools/build_voice_pack.py）。
  2. 系统语音 —— PyQt5.QtTextToSpeech，Windows 上走 SAPI
     （这里是 Microsoft Huihui，中文女声），任意文本实时合成。

音色包是**可选的**：voice/ 里没有对应音频时自动回退到系统语音。
所以一条音频都不生成，播报功能照样能用，只是音色是系统默认的。

打包时注意：QtTextToSpeech 需要 Qt 的 texttospeech 插件，
启动考勤系统.spec 里的 collect_all('PyQt5') 已经把它一起收集了。
"""
from __future__ import annotations

import hashlib
import os
import sys
import unicodedata
from typing import List, Optional

from PyQt5.QtCore import QLocale, QObject

from . import app_data_dir

#: 刷到未登记的卡时说的话（与界面上的「学生注册」保持一致）
UNKNOWN_TEXT = "未注册卡，请到学生注册登记"

#: 签到后还没到间隔就刷卡时说的话
TOO_SOON_TEXT = "未到签退时间"

#: 卡已过有效期时说的话
EXPIRED_TEXT = "这张卡已过期，请联系管理员"


# ---------------------------------------------------------------- 音色包路径
def voice_dir() -> str:
    """用户音色包目录：exe 同级目录（开发模式为项目根目录）下的 voice/。

    打包后这里是可写的，用户随时可以往里丢自己的 wav 来换音色。
    """
    return os.path.join(app_data_dir(), "voice")


def _candidate_dirs() -> List[str]:
    """音色包查找顺序：用户目录优先，其次是打包内置的。"""
    dirs = [voice_dir()]
    if getattr(sys, "frozen", False):
        # PyInstaller 单文件模式会把 datas 解压到 _MEIPASS
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            dirs.append(os.path.join(meipass, "voice"))
    return dirs


def pack_name(text: str) -> str:
    """文本 → 音色包文件名。

    用哈希而不是原文做文件名：中文文件名在 PyInstaller 解包目录里容易出幺蛾子，
    而且原文里可能有 / : * ? 之类的非法字符。
    """
    norm = unicodedata.normalize("NFC", (text or "").strip())
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16] + ".wav"


def pack_path(text: str) -> str:
    """这段文本对应的音色包路径。

    返回第一个**真实存在**的文件；都不存在时返回用户目录下的目标路径
    （也就是将来外部工具该把 wav 写到哪）。
    """
    name = pack_name(text)
    dirs = _candidate_dirs()
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return os.path.join(dirs[0], name)


def has_pack(text: str) -> bool:
    """这段文本有没有现成的音色包音频。"""
    try:
        name = pack_name(text)
        return any(os.path.isfile(os.path.join(d, name)) for d in _candidate_dirs())
    except Exception:
        return False


# ---------------------------------------------------------------- 播报引擎
class VoiceEngine(QObject):
    """刷卡语音播报引擎。

    QTextToSpeech 必须在 GUI 线程里创建和使用，所以这个对象只在主线程构造，
    并且惰性初始化（第一次播报时才真正创建引擎）。
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._enabled = True
        self._rate = 0.0          # -1.0 ~ 1.0
        self._pitch = 0.0         # -1.0 ~ 1.0
        self._volume = 1.0        #  0.0 ~ 1.0
        self._voice_name = ""     # 用户在设置里指定的语音名，空 = 自动挑中文
        self._tts = None
        self._tts_broken = False

    # ------------------------------------------------------------ 配置
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if not self._enabled:
            self.stop()

    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        self._rate = max(-1.0, min(1.0, float(rate)))
        if self._tts is not None:
            try:
                self._tts.setRate(self._rate)
            except Exception:
                pass

    def voice_name(self) -> str:
        return self._voice_name

    def set_voice_name(self, name: str) -> None:
        self._voice_name = (name or "").strip()
        if self._tts is not None:
            self._pick_voice(self._tts)

    # ------------------------------------------------------------ 播报
    def speak(self, text: str) -> None:
        """播报一段文本：音色包优先，没有就用系统语音。"""
        if not self._enabled:
            return
        text = (text or "").strip()
        if not text:
            return

        path = pack_path(text)
        if os.path.isfile(path) and self._play_file(path):
            return
        self._say(text)

    def announce_check_in(self, name: str) -> None:
        """场景一：刷卡签到成功 → 念「姓名 + 签到成功」。"""
        self.speak(f"{name}，签到成功")

    def announce_check_out(self, name: str) -> None:
        """场景二：刷卡签退成功 → 念「姓名 + 签退成功」。"""
        self.speak(f"{name}，签退成功")

    def announce_unknown_card(self) -> None:
        """场景三：刷的卡没登记 → 提示去人员管理注册。"""
        self.speak(UNKNOWN_TEXT)

    def announce_too_soon(self, name: str = "") -> None:
        """场景四：签到后没到间隔又刷卡 → 提示还没到签退时间。"""
        name = (name or "").strip()
        self.speak(f"{name}，{TOO_SOON_TEXT}" if name else TOO_SOON_TEXT)

    def announce_expired(self) -> None:
        """场景五：卡不在有效期内 → 提示已过期。"""
        self.speak(EXPIRED_TEXT)

    def stop(self) -> None:
        """立刻停掉当前播报（下一次刷卡会打断上一次）。"""
        self._purge()
        if self._tts is not None:
            try:
                self._tts.stop()
            except Exception:
                pass

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _purge() -> None:
        if sys.platform != "win32":
            return
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def _play_file(self, path: str) -> bool:
        """播放 wav 文件，成功返回 True。"""
        if sys.platform != "win32":
            return False
        try:
            import winsound
            self._purge()
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        except Exception:
            return False

    def _tts_engine(self):
        """惰性创建 QtTextToSpeech。"""
        if self._tts is not None or self._tts_broken:
            return self._tts
        try:
            from PyQt5.QtTextToSpeech import QTextToSpeech
            eng = QTextToSpeech(self)
            if int(eng.state()) == 3:          # QTextToSpeech.BackendError
                raise RuntimeError("QtTextToSpeech backend error")
            self._pick_voice(eng)
            eng.setRate(self._rate)
            eng.setPitch(self._pitch)
            eng.setVolume(self._volume)
            self._tts = eng
        except Exception:
            self._tts_broken = True
            self._tts = None
        return self._tts

    def _pick_voice(self, eng) -> None:
        """优先中文语音；用户指定过名字则以名字为准。"""
        try:
            locales = list(eng.availableLocales())
        except Exception:
            locales = []

        if self._voice_name:
            for loc in locales:
                eng.setLocale(loc)
                for v in eng.availableVoices():
                    if v.name() == self._voice_name:
                        eng.setVoice(v)
                        return

        for loc in locales:
            if loc.language() == QLocale.Chinese:
                eng.setLocale(loc)
                vs = eng.availableVoices()
                if vs:
                    eng.setVoice(vs[0])
                return

        if locales:
            eng.setLocale(locales[0])

    def _say(self, text: str) -> None:
        eng = self._tts_engine()
        if eng is None:
            return
        try:
            eng.stop()
            eng.say(text)
        except Exception:
            pass

    # ------------------------------------------------------------ 诊断
    def list_voices(self) -> List[str]:
        """当前引擎可用的语音名（供设置页下拉框），找不到就返回空列表。"""
        eng = self._tts_engine()
        if eng is None:
            return []
        out: List[str] = []
        seen = set()
        for loc in eng.availableLocales():
            eng.setLocale(loc)
            for v in eng.availableVoices():
                n = v.name()
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        self._pick_voice(eng)      # 复位回该用的语音
        return out

    def current_voice(self) -> str:
        eng = self._tts
        if eng is None:
            return self._voice_name
        try:
            return eng.voice().name()
        except Exception:
            return self._voice_name


# ---------------------------------------------------------------- 全局单例
_engine: Optional[VoiceEngine] = None


def get_engine() -> VoiceEngine:
    """全局唯一的播报引擎。

    惰性创建——第一次调用必须发生在 QApplication 之后（比如刷卡那一刻）。
    """
    global _engine
    if _engine is None:
        _engine = VoiceEngine()
    return _engine
