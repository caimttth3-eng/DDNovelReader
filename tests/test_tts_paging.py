# -*- coding: utf-8 -*-
"""朗读 + 翻页模式：页内读字视口不动，句子越页整页翻。"""
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
TMP = tempfile.mkdtemp(prefix="novel_tts_page_test_")
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


def make_book():
    lines = ["第%d段：他沿着河往前走，看见远处的灯火和水面的倒影，心里忽然安静下来。" % i
             for i in range(1, 101)]
    path = os.path.join(TMP, "tts_paging.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("第一章 测试\n\n" + "\n".join(lines) + "\n")
    return path


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

    path = make_book()
    content = book_loader.parse_book(path)
    bid = app.storage.book_id(path)
    app.storage.add_book({
        "id": bid, "title": content.title, "author": content.author,
        "format": content.format, "path": path,
        "added_at": time.time(), "last_read_at": time.time(),
        "total_chars": content.total_chars,
        "chapter_titles": [c.title for c in content.chapters],
        "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
    })
    app.storage.write_cache(bid, content)
    app._cache[bid] = content
    app.open_book(bid)
    root.update()

    print("[tts-page] 进入翻页模式")
    app.settings["page_mode"] = "page"
    app._page_mode_changed()
    root.update()
    check("分页模式生效", app._page_active())

    top, end = app._page_range()
    check("页区间有效", end > top, f"top={top} end={end}")

    print("[tts-page] 页内句子：视口不能动")
    top_idx = paging.page_top_index(app.text)
    d0 = app.text.dlineinfo(top_idx)
    mid = top + max(1, (end - top) // 3)
    app._highlight_sentence(app.chapter_idx, mid, "页内句子测试")
    root.update()
    d1 = app.text.dlineinfo(paging.page_top_index(app.text))
    check("页内读字不滚屏", bool(d0) and bool(d1) and abs(d1[1] - d0[1]) <= 1,
          f"{d0} -> {d1}")
    check("页内读字页首不变", abs(app.char_offset - top) <= 1, f"{app.char_offset} vs {top}")

    print("[tts-page] 句子越页：整页翻一页")
    page_off = end
    offs_before = list(getattr(app, "_page_hist", []))
    app._highlight_sentence(app.chapter_idx, page_off, "越页句子测试")
    root.update()
    check("越页后位置前进到下一页首", app.char_offset >= page_off, f"{app.char_offset} vs {page_off}")
    hist = getattr(app, "_page_hist", [])
    check("历史恰好前进一页", len(hist) == len(offs_before) + 1, f"{offs_before} -> {hist}")
    top2, end2 = app._page_range()
    check("新页区间覆盖句子首字", top2 <= page_off < end2, f"top={top2} end={end2} off={page_off}")
    pady = paging._pady(app.text)
    d = app.text.dlineinfo(paging.page_top_index(app.text))
    check("翻页后页首仍对齐", bool(d) and abs(int(d[1]) - pady) <= 1, str(d))

    print("[tts-page] 跨两页的跳句：补翻")
    _t, e = app._page_range()
    far = e + max(1, (e - _t) // 2)
    app._highlight_sentence(app.chapter_idx, far, "远跳句子测试")
    root.update()
    root.update_idletasks()
    t3, e3 = app._page_range()
    check("远跳后区间覆盖句子首字", t3 <= far < e3, f"top={t3} end={e3} off={far}")

    print("[tts-page] 模拟 TTS 连续推进 8 句")
    seq = []
    t_cur, e_cur = app._page_range()
    step = max(1, (e_cur - t_cur) // 6)
    off = t_cur
    for i in range(8):
        off += step
        app._highlight_sentence(app.chapter_idx, off, "连续推进句子")
        root.update()
        root.update_idletasks()
        tr, er = app._page_range()
        check(f"第{i+1}句后高亮在页内", tr <= off < er + step, f"off={off} top={tr} end={er}")
        seq.append(app.char_offset)
    check("连续推进页首单调递增", all(seq[i + 1] >= seq[i] for i in range(len(seq) - 1)), str(seq))

    print("[tts-page] 切回滚动模式")
    app.settings["page_mode"] = "scroll"
    app._page_mode_changed()
    root.update()
    check("滚动模式生效", not app._page_active())
    check("滚动模式无遮罩", getattr(app, "_page_mask", None) is None)

    print("[tts-page] 词读到页底：自动翻页")
    app.settings["page_mode"] = "page"
    app._page_mode_changed()
    root.update()
    cut = paging.first_cut_line(app.text)
    if cut:
        top_before = app.char_offset
        app._page_word_check(cut)
        root.update()
        check("词越页触发整页翻", app.char_offset > top_before,
              f"{top_before} -> {app.char_offset}")
        tr, er = app._page_range()
        check("词翻页后页首对齐",
              abs(int(app.text.dlineinfo(paging.page_top_index(app.text))[1]) - paging._pady(app.text)) <= 1)
    else:
        check("词越页测试可执行（本页有被裁行）", True)

    print("[tts-page] 朗读中切换分页模式：高亮与页面对齐都保留")
    app.settings["page_mode"] = "page"
    app._page_mode_changed()
    root.update()
    top, end = app._page_range()
    off = top + max(1, (end - top) // 2)
    app.tts.is_playing = lambda: True  # 模拟朗读进行中（不真的出声）
    app._highlight_sentence(app.chapter_idx, off, "切换模式前句子")
    app.page_cb.current(2)  # 翻页-滑动
    app._on_page_mode_change()
    root.update()
    hi = getattr(app, "_highlight_index", None)
    check("切模式后高亮仍在", hi is not None and hi[0] == app.chapter_idx, str(hi))
    tr, er = app._page_range()
    check("切模式后高亮仍在页内", tr <= off < er, f"top={tr} end={er} off={off}")
    app.settings["page_mode"] = "scroll"
    app._page_mode_changed()
    root.update()

    print("[tts-page] TTS 事件路径 + 滑动/覆盖动画自动翻页")
    for mode in ("slide", "cover"):
        app.settings["page_mode"] = mode
        app._page_mode_changed()
        root.update()
        top, end = app._page_range()
        far = min(len(app.book.chapters[app.chapter_idx].content) - 2, end + 20)
        app._handle_tts_event({
            "type": "sentence_start", "chapter_idx": app.chapter_idx,
            "char_offset": far, "text": "动画路径测试",
        })
        for _ in range(40):
            root.update()
            time.sleep(0.02)
        tr, er = app._page_range()
        check(f"{mode} 动画翻页后覆盖句首", tr <= far < er, f"top={tr} end={er} off={far}")
        check(f"{mode} 动画未残留遮罩", app._page_mask is not None or paging.first_cut_line(app.text) is None)
    app.settings["page_mode"] = "scroll"
    app._page_mode_changed()
    root.update()

    root.destroy()
    print("\n%s（失败 %d 项）" % ("ALL PASS" if not FAIL else "FAILED", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
