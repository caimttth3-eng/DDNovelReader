# -*- coding: utf-8 -*-
"""真机验证逐词高亮：真开书 → 真 Edge 朗读 → 连续截图 + 打印 tag 范围。

用法： .venv\\Scripts\\python.exe tools\\verify_word_hl.py
输出： 项目根 _verify_word_hl.txt + _verify_word_hl_1/2/3.png
"""
import io
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE) if os.path.basename(HERE) == "tools" else HERE
sys.path.insert(0, ROOT_DIR)

TMP = os.path.join(ROOT_DIR, "_tmp_word_verify")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
os.environ["DOUBAO_NOVEL_DATA"] = TMP

import ctypes                                                 # noqa: E402
import tkinter as tk                                          # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

from PIL import ImageGrab                                     # noqa: E402

from novelreader.storage import Storage as _SeedStorage       # noqa: E402

_SeedStorage(path=os.path.join(TMP, "library.json")).set_setting("ui_lang", "zh")

from novelreader.gui import NovelReaderApp                    # noqa: E402

LINES = []


def log(msg):
    LINES.append(str(msg))
    print(msg)


def main():
    sample = os.path.join(ROOT_DIR, "sample", "星火.txt")
    if not os.path.exists(sample):
        log("找不到样书：%s" % sample)
        return 1
    root = tk.Tk()
    root.geometry("1280x800+60+40")
    app = NovelReaderApp(root)
    # 等启动流程跑完（启动期有 after 链会重置阅读区，抢在它前面渲染会被覆盖）
    t_wait = time.time()
    while time.time() - t_wait < 4.0:
        root.update()
        time.sleep(0.05)
    log("启动完成，开始开书")

    app._load_book(sample)
    content = app._load_book(sample)
    if content is None:
        log("解析样书失败")
        return 1
    app.book = content
    app.chapter_idx = 0
    app.char_offset = 0
    try:
        app._render_chapter()
    except Exception as e:                                     # noqa: BLE001
        log("渲染失败：%s" % e)
    root.update()
    log("已打开书：%s 章节数=%d" % (getattr(content, "title", "?"),
                                    len(getattr(content, "chapters", []) or [])))
    # 用 Edge 神经语音（逐词高亮只对它生效）
    try:
        app.settings["word_highlight"] = True
        app.tts.set_voice("zh-CN-XiaoxiaoNeural")
        app.settings["voice"] = "zh-CN-XiaoxiaoNeural"
        app._start_tts(app.book, 0, 0)
    except Exception as e:                                     # noqa: BLE001
        log("启动朗读失败：%s" % e)
        return 1

    shots = 0
    seen = []
    t0 = time.time()
    tick = 0
    while time.time() - t0 < 14 and shots < 3:
        root.update()
        time.sleep(0.15)
        tick += 1
        if tick % 4 == 0:
            try:
                playing = app.tts.is_playing()
                with app.tts._cv:
                    nwords = len(app.tts._cur_words or [])
                pos = app.tts.playback_position_ms()
                cur = app.tts.current_word()
                wr = app.text.tag_ranges("tts_word")
                ctx = getattr(app, "_word_ctx", None)
                info = ""
                if ctx:
                    mp = app._word_map() or []
                    w = cur
                    info = "ctx_ci=%s ch=%s clean_len=%s disp_len=%s map_len=%s" % (
                        ctx.get("ci"), app.chapter_idx, len(ctx.get("clean") or ""),
                        len(ctx.get("display") or ""), len(mp))
                    if w is not None and mp:
                        s_i = w.text_start
                        e_i = min(w.text_end, len(mp))
                        info += " map[s]=%s map[e-1]=%s" % (
                            mp[s_i] if 0 <= s_i < len(mp) else "OOR",
                            mp[e_i - 1] if 0 < e_i <= len(mp) else "OOR")
                log("t=%.1fs playing=%s 词表=%d 位置=%s 当前词=%s 词层tag=%s %s" % (
                    time.time() - t0, playing, nwords, pos,
                    getattr(cur, "text", None), bool(wr), info))
                if ctx and tick <= 12:
                    try:
                        a = ctx["start"]
                        raw = app.text.get(a, "%s+60c" % a)
                        log("  锚点=%s 窗口前60字=%r" % (a, raw))
                        log("  句子清洗文本=%r" % (ctx.get("clean") or "")[:60])
                    except Exception as e:                     # noqa: BLE001
                        log("  窗口取样失败：%s" % e)
            except Exception as e:                             # noqa: BLE001
                log("诊断异常：%s" % e)
        try:
            wr = app.text.tag_ranges("tts_word")
            sr = app.text.tag_ranges("tts")
        except Exception:
            wr, sr = (), ()
        if wr:
            word = app.text.get(wr[0], wr[1])
            sverb = app.text.get(sr[0], sr[1])[:24] if sr else ""
            if not seen or seen[-1] != word:
                seen.append(word)
                log("词层命中：%r（句子层：%r…）" % (word, sverb))
            if len(seen) >= 3:
                shots += 1
                try:
                    root.attributes("-topmost", True)
                    root.lift()
                    root.focus_force()
                    root.update_idletasks()
                    root.update()
                    time.sleep(0.4)
                except Exception:
                    pass
                x, y = app.text.winfo_rootx(), app.text.winfo_rooty()
                w, h = app.text.winfo_width(), app.text.winfo_height()
                img = ImageGrab.grab(bbox=(x, y, x + w, min(y + h, y + 380)))
                out = os.path.join(ROOT_DIR, "_verify_word_hl_%d.png" % shots)
                img.save(out)
                log("截图：%s（词=%r）" % (os.path.basename(out), word))
                try:
                    root.attributes("-topmost", False)
                except Exception:
                    pass
                seen.clear()
    try:
        app.tts.stop()
    except Exception:
        pass
    log("连续命中词数：%d" % len(seen))
    ok = True
    if not LINES or not any("词层命中" in ln for ln in LINES):
        log("RESULT FAIL：没有观察到词层高亮")
        ok = False
    else:
        log("RESULT PASS")
    io.open(os.path.join(ROOT_DIR, "_verify_word_hl.txt"), "w", encoding="utf-8").write("\n".join(LINES))
    try:
        app._on_close()
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
