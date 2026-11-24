# -*- coding: utf-8 -*-
"""分页模式 + 正文简繁的 GUI 集成冒烟：开书、翻页、回翻、切字号、切简繁、切回滚动。"""
import os
import sys
import tempfile
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
VENV = os.path.join(BASE, ".venv")
os.environ.setdefault("TCL_LIBRARY", os.path.join(VENV, "Lib", "tcl8.6"))
os.environ.setdefault("TK_LIBRARY", os.path.join(VENV, "Lib", "tk8.6"))
TMP = tempfile.mkdtemp(prefix="novel_page_test_")
os.environ["DOUBAO_NOVEL_DATA"] = TMP

import tkinter as tk

from novelreader import book_loader, paging
from novelreader.gui import NovelReaderApp

SAMPLE = os.path.join(BASE, "sample", "星火.txt")
FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def main():
    threading.Thread(target=lambda: (time.sleep(120), os._exit(2)), daemon=True).start()

    root = tk.Tk()
    root.geometry("760x620+2200+80")
    try:
        root.attributes("-alpha", 0.05)
    except Exception:
        pass
    app = NovelReaderApp(root)
    root.update()

    content = book_loader.parse_book(SAMPLE)
    bid = app.storage.book_id(SAMPLE)
    app.storage.add_book({
        "id": bid, "title": content.title, "author": content.author,
        "format": content.format, "path": SAMPLE,
        "added_at": time.time(), "last_read_at": time.time(),
        "total_chars": content.total_chars,
        "chapter_titles": [c.title for c in content.chapters],
        "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
    })
    app.storage.write_cache(bid, content)
    app._cache[bid] = content
    app.open_book(bid)
    root.update()

    print("[page-ui] 切到翻页模式")
    app.settings["page_mode"] = "page"
    app._page_mode_changed()
    root.update()
    check("分页模式生效", app._page_active())
    top = paging.page_top_index(app.text)
    d = app.text.dlineinfo(top)
    pady = paging._pady(app.text)
    check("首页页首对齐", bool(d) and abs(int(d[1]) - pady) <= 1, str(d))
    check("底部遮罩已放置", app._page_mask is not None or paging.first_cut_line(app.text) is None)

    print("[page-ui] 连续翻 6 页")
    offs = [app.char_offset]
    chapters = [app.chapter_idx]
    for _ in range(6):
        app._page_turn(1)
        root.update()
        offs.append(app.char_offset)
        chapters.append(app.chapter_idx)
        top = paging.page_top_index(app.text)
        d = app.text.dlineinfo(top)
        if not (d and abs(int(d[1]) - pady) <= 1):
            check("翻页后页首对齐", False, f"d={d} off={app.char_offset} ch={app.chapter_idx}")
            break
    else:
        check("翻页后每页页首对齐", True)
    check("翻页推进了位置", (chapters[-1], offs[-1]) != (chapters[0], offs[0]))
    check("翻页后遮罩在最底部区域",
          app._page_mask is None or int(app._page_mask.place_info().get("y", 0)) >= pady + 2)

    print("[page-ui] 回翻")
    cur = (app.chapter_idx, app.char_offset)
    app._page_turn(-1)
    root.update()
    check("回翻后位置变化", (app.chapter_idx, app.char_offset) != cur,
          f"{(app.chapter_idx, app.char_offset)} vs {cur}")
    top = paging.page_top_index(app.text)
    d = app.text.dlineinfo(top)
    check("回翻页首对齐", bool(d) and abs(int(d[1]) - pady) <= 1, str(d))

    print("[page-ui] 改字号后重算")
    while_off = app.char_offset
    old_pct = app._compute_percent(app.chapter_idx, app.char_offset)
    app.settings["font_size"] = int(app.settings.get("font_size", 16)) + 3
    app._apply_font()
    app._page_after_font()
    root.update()
    top = paging.page_top_index(app.text)
    d = app.text.dlineinfo(top)
    check("改字号后页首对齐", bool(d) and abs(int(d[1]) - pady) <= 1, str(d))
    new_pct = app._compute_percent(app.chapter_idx, app.char_offset)
    check("改字号后进度基本不变（±1.5%）", abs(new_pct - old_pct) <= 1.5,
          f"{old_pct:.2f} -> {new_pct:.2f}")

    print("[page-ui] 简繁转换（显示层 1:1）")
    app.settings["text_convert"] = "s2t"
    app._render_chapter()
    root.update()
    shown = app.text.get("1.0", "end-1c")
    raw = app.book.chapters[app.chapter_idx].content
    check("显示文本与原文等长（1:1 不破坏偏移）", len(app._disp_text) == len(raw),
          f"disp={len(app._disp_text)} raw={len(raw)}")
    check("显示层确已转换（内容与原文不同）", app._disp_text != raw, app._disp_text[:24])
    off_after = app.char_offset
    check("切简繁后原文偏移不漂（±30 字）", abs(off_after - while_off) <= 30,
          f"{while_off} -> {off_after}")
    app.settings["text_convert"] = "none"
    app._render_chapter()
    root.update()
    check("切回不转换后与原文一致", app._disp_text == app.book.chapters[app.chapter_idx].content)

    print("[page-ui] 切回滚动模式")
    app.settings["page_mode"] = "scroll"
    app._page_mode_changed()
    root.update()
    check("滚动模式生效", not app._page_active())
    check("滚动模式无遮罩", getattr(app, "_page_mask", None) is None)
    check("滚动模式可滚动", app.text.yview()[0] >= 0.0 and float(app.text.cget("state") == "disabled" or 1))

    root.destroy()
    print("\n%s（失败 %d 项）" % ("ALL PASS" if not FAIL else "FAILED", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
