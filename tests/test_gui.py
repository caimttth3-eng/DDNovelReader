# -*- coding: utf-8 -*-
"""GUI 冒烟测试：实例化应用、打开书籍、翻章、字体/进度/设置、TTS 事件。"""
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

TMP = tempfile.mkdtemp(prefix="novel_gui_test_")
os.environ["DOUBAO_NOVEL_DATA"] = TMP

import tkinter as tk

from novelreader import book_loader
from novelreader.gui import NovelReaderApp
from novelreader.storage import Storage

SAMPLE = os.path.join(BASE, "sample", "星火.txt")

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def main():
    import threading

    def _watchdog():
        time.sleep(60)
        os._exit(2)  # 卡死保护：60 秒未结束强制退出

    threading.Thread(target=_watchdog, daemon=True).start()

    root = tk.Tk()
    root.withdraw()
    app = NovelReaderApp(root)
    root.update_idletasks()

    # 注入样本书
    content = book_loader.parse_book(SAMPLE)
    bid = app.storage.book_id(SAMPLE)
    meta = {
        "id": bid,
        "title": content.title,
        "author": content.author,
        "format": content.format,
        "path": SAMPLE,
        "added_at": time.time(),
        "last_read_at": time.time(),
        "total_chars": content.total_chars,
        "chapter_titles": [c.title for c in content.chapters],
        "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
    }
    app.storage.add_book(meta)
    app.storage.write_cache(bid, content)
    app._cache[bid] = content
    app._refresh_bookshelf()

    check("书架刷新", len(app.shelf_tree.get_children()) >= 1)

    app.open_book(bid)
    check("打开书籍", app.book is not None and app.book.title == "星火")
    check("章节数正确", len(app.book.chapters) >= 6, f"n={len(app.book.chapters)}")
    check("渲染后文本非空", app.text.get("1.0", "end").strip() != "")
    check("状态栏章节", "第 1 章" in app.pos_label.cget("text"))
    check("标题标签", app.title_label.cget("text") == "星火")

    # 进度计算
    pct = app._compute_percent(0, 10)
    check("进度计算", 0 < pct < 100, f"{pct}")

    # 翻章
    app._goto_chapter(2)
    check("翻到第 3 章", app.chapter_idx == 2 and app.chapter_cb.get() == app.book.chapters[2].title)

    # 进度保存
    app.char_offset = 50
    app.chapter_idx = 1
    app._save_now()
    b = app.storage.get_book(bid)
    check("进度已保存", b["progress"]["chapter_idx"] == 1 and b["progress"]["char_offset"] == 50)

    # 自动恢复：重新实例化（模拟下次打开）
    root2 = tk.Tk()
    root2.withdraw()
    app2 = NovelReaderApp(root2)
    app2.storage.data["books"] = app.storage.data["books"]
    app2.storage.set_setting("last_book", bid)
    app2.open_book(bid)
    check("自动恢复上次章节", app2.chapter_idx == 1 and app2.char_offset == 50)
    root2.destroy()

    # 字体设置
    app._change_font_size(2)
    check("字号调整", app.storage.get_setting("font_size") == 19)
    app._change_line_spacing(0.1)
    check("行距调整", abs(app.storage.get_setting("line_spacing") - 1.6) < 0.01)
    app._on_theme_change(type("E", (), {})())
    # 主题下拉未设置值 → 用默认护眼，仅验证不崩溃
    check("主题切换不崩溃", True)

    # 进度滑块跳转
    total = app.book.total_chars
    mid_chapter = 1  # 跳到第 2 章中部
    target = app.book.cum[mid_chapter] + 20
    app._seeking = True
    app._on_seek(target / total * 100.0)  # 模拟拖动到 50% 附近
    check("滑块跳转到目标章节", app.chapter_idx == mid_chapter, f"ci={app.chapter_idx}")
    check("滑块跳转无递归", True)  # 不抛异常即通过

    # TTS 事件处理（模拟朗读推进）
    app._handle_tts_event({"type": "sentence_start", "chapter_idx": 0, "char_offset": 5, "text": "测试句子"})
    check("高亮产生", "tts" in app.text.tag_names())

    # —— 高亮钉顶（长章节 + 真实可见窗口；隐藏窗口下滚动/几何不可靠，避免误报）——
    long_path = os.path.join(TMP, "long.txt")
    paras = [f"这是用于验证高亮钉顶的第{i}段文字，足够长以确保阅读区可以滚动。" for i in range(300)]
    with open(long_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(paras))
    lc = book_loader.parse_book(long_path)
    lbid = app.storage.book_id(long_path)
    app.storage.add_book({"id": lbid, "title": lc.title, "author": "", "format": "txt",
                          "path": long_path, "added_at": time.time(), "last_read_at": time.time(),
                          "total_chars": lc.total_chars, "chapter_titles": [c.title for c in lc.chapters],
                          "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0}})
    app.storage.write_cache(lbid, lc)
    app.open_book(lbid)
    root.deiconify()
    root.geometry("900x600")
    root.update_idletasks()
    root.update()
    mid = len(app.book.chapters[0].content) // 2
    app.chapter_idx = 0
    app.char_offset = mid
    app._render_chapter()
    app.root.update_idletasks()
    app._handle_tts_event({"type": "sentence_start", "chapter_idx": 0, "char_offset": mid, "text": "高亮测试"})
    app.root.update_idletasks()
    app.root.update()
    top_after = int(app.text.index("@0,0").split(".")[0])
    hl_line = int(app._highlight_index[1].split(".")[0])
    check("高亮钉到窗口第一行", top_after == hl_line, f"top {top_after} vs hl {hl_line}")

    _orig_is_playing = app.tts.is_playing
    app.tts.is_playing = lambda: True
    app.settings["font_size"] = 30
    app._apply_font()
    app.root.update_idletasks()
    app.root.update()
    check("字号放大高亮仍在顶部", int(app.text.index("@0,0").split(".")[0]) == hl_line)
    app.tts.is_playing = _orig_is_playing
    root.withdraw()

    app._handle_tts_event({"type": "sentence_done", "chapter_idx": 0, "char_offset": 9})
    check("朗读推进进度", app.char_offset == 9)

    app._on_close()
    check("正常关闭", True)

    print()
    if FAIL:
        print(f"共 {len(FAIL)} 项失败: {FAIL}")
        sys.exit(1)
    print("GUI 冒烟测试通过")
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
