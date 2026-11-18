# -*- coding: utf-8 -*-
"""逐词高亮端到端自测（v2.3.0）。

覆盖：
1) 真实 Edge 合成返回词表（有网时）；词表时间单调、字符区间落在句子里
2) SpeechController.current_word() 按播放位置返回当前词
3) GUI：句子高亮 + 词层上色 + 设置开关（关掉即清除词层）

跑法： .venv\\Scripts\\python.exe tests\\test_word_highlight.py
"""
import io
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "_tmp_word_hl")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
os.environ["DOUBAO_NOVEL_DATA"] = TMP

from novelreader.storage import Storage as _SeedStorage            # noqa: E402

_SeedStorage(path=os.path.join(TMP, "library.json")).set_setting("ui_lang", "zh")

import tkinter as tk                                               # noqa: E402

from novelreader.speech_word import SpeechWord, word_at            # noqa: E402
from novelreader.tts_engine import SpeechController, synth_audio    # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def main():
    # ---------- 1) 真实 Edge 合成 → 词表 ----------
    sent = "今天的天气很好，我们一起去公园散步吧。"
    words = []
    try:
        audio, words = synth_audio(sent, "zh-CN-XiaoxiaoNeural", 200)
        ok_audio = bool(audio) and len(audio) > 1000
    except Exception as e:                                        # noqa: BLE001
        ok_audio = False
        print("（跳过真实合成：%s）" % e)
    if ok_audio:
        check("真实合成返回音频", True)
        check("真实合成返回词表（Edge WordBoundary）", len(words) >= 5, len(words))
        if words:
            check("词表时间单调不减",
                  all(words[i].start_ms <= words[i + 1].start_ms for i in range(len(words) - 1)),
                  [(w.text, w.start_ms) for w in words[:6]])
            check("词的字符区间落在句子内",
                  all(0 <= w.text_start < w.text_end <= len(sent) for w in words),
                  [(w.text, w.text_start, w.text_end) for w in words[:4]])
            check("词文本能在句子里对上",
                  all(sent[w.text_start:w.text_end] == w.text for w in words),
                  [(w.text, sent[w.text_start:w.text_end]) for w in words[:4]])
            check("词时长都为正", all(w.duration_ms > 0 for w in words))
    else:
        print("[SKIP] 无网络/合成失败，跳过真实合成相关断言")

    # ---------- 2) 控制器按位置取词 ----------
    ctl = SpeechController()
    fake = [SpeechWord(0, 200, 0, 2, "今天"), SpeechWord(200, 200, 2, 4, "天气"),
            SpeechWord(400, 200, 4, 6, "很好")]
    with ctl._cv:
        ctl._cur_words = list(fake)
    positions = {}

    def _fake_pos():
        return positions.get("ms", -1)

    ctl.playback_position_ms = _fake_pos
    positions["ms"] = 100
    check("位置 100ms → 第 1 个词", getattr(ctl.current_word(), "text", None) == "今天")
    positions["ms"] = 450
    check("位置 450ms → 第 3 个词", getattr(ctl.current_word(), "text", None) == "很好")
    positions["ms"] = -1
    check("位置未知 → None", ctl.current_word() is None)
    with ctl._cv:
        ctl._cur_words = []
    positions["ms"] = 100
    check("没有词表 → None（退回句子级）", ctl.current_word() is None)

    # ---------- 3) GUI 上色 + 开关 ----------
    app_root = tk.Tk()
    app_root.withdraw()
    from novelreader.gui import NovelReaderApp
    app = NovelReaderApp(app_root)
    app_root.update_idletasks()
    # 模拟"正在朗读"：词层刷新要求 book 存在且引擎在播放
    app.book = object()
    app.tts.is_playing = lambda: True

    app.text.configure(state="normal")
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "今天的天气很好，我们一起去公园散步吧。")
    app.text.configure(state="disabled")

    # 句子级高亮 + 词层上下文（模拟 sentence_start 后的状态）
    app.text.tag_add("tts", "1.0", "1.end")
    app.settings["word_highlight"] = True
    app.chapter_idx = 0
    app._word_ctx = {"ci": 0, "start": "1.0",
                     "clean": "今天的天气很好我们一起去公园散步吧",
                     "display": "今天的天气很好，我们一起去公园散步吧。", "map": None}

    with app.tts._cv:
        app.tts._cur_words = [SpeechWord(0, 300, 0, 2, "今天"),
                              SpeechWord(300, 300, 3, 5, "天气")]
    app.tts.playback_position_ms = lambda: 50
    app.root.after_cancel(app._word_after) if getattr(app, "_word_after", None) else None
    app._word_after = None
    app._word_tick()
    ranges = app.text.tag_ranges("tts_word")
    got = app.text.get(ranges[0], ranges[1]) if ranges else ""
    check("词层上色命中「今天」", got == "今天", got)
    check("句子层仍在（两色共存）", bool(app.text.tag_ranges("tts")))

    # 位置移到第二个词
    app.tts.playback_position_ms = lambda: 350
    app._word_tick()
    ranges = app.text.tag_ranges("tts_word")
    got2 = app.text.get(ranges[0], ranges[1]) if ranges else ""
    check("位置推进后词层移到「天气」", got2 == "天气", got2)

    # 关掉开关 → 立即清除词层，句子层保留
    app._toggle_word_highlight()
    app_root.update_idletasks()
    check("关闭后设置已落盘", app.settings.get("word_highlight") is False)
    check("关闭后词层已清除", not app.text.tag_ranges("tts_word"))
    check("关闭后句子层仍在", bool(app.text.tag_ranges("tts")))
    app._toggle_word_highlight()
    check("再次开启生效", app.settings.get("word_highlight") is True)

    try:
        app._on_close()
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)

    print("\n==== %d FAIL ====" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
