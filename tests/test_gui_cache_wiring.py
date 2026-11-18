# -*- coding: utf-8 -*-
"""GUI 接线冒烟：真实 App 打开书后，缓存键/目录/按书大小都要对。

窗口全程 withdraw（不可见），不联网、不出声。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))
    else:
        print("[ OK ] %s" % name)


tmp_data = tempfile.mkdtemp(prefix="ddgui_data_")
os.environ["DOUBAO_NOVEL_DATA"] = tmp_data

import tkinter as tk
from novelreader.gui import NovelReaderApp
from novelreader.content_key import content_key_of_chapters, rate_dir_key
from novelreader.book_loader import parse_book

sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample", "星火.txt")

root = tk.Tk()
root.withdraw()
app = NovelReaderApp(root)
root.update_idletasks()

content = parse_book(sample)
bid = app.storage.book_id(sample)
ckey = content_key_of_chapters(content.title, content.chapters)
app.storage.add_book({
    "id": bid, "content_key": ckey, "title": content.title, "author": content.author,
    "format": content.format, "path": sample, "added_at": 0, "last_read_at": 0,
    "total_chars": content.total_chars,
    "chapter_titles": [c.title for c in content.chapters],
    "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
})
app.storage.write_cache(bid, content)

# 造旧布局（模拟升级前的缓存）
voice = "zh-CN-XiaoxiaoNeural"
tts_root = app._effective_tts_cache_root()
import json
old_dir = os.path.join(tts_root, bid, voice, "200")
os.makedirs(old_dir, exist_ok=True)
with open(os.path.join(old_dir, "0000_00000000.mp3"), "wb") as f:
    f.write(b"ID3" + b"\0" * 4000)
with open(os.path.join(old_dir, "progress.json"), "w", encoding="utf-8") as f:
    json.dump({"book_id": bid, "voice": voice, "rate": 200, "total": 1, "done": 1,
               "completed": [0], "state": "done"}, f)

app.open_book(bid)
root.update_idletasks()

check("open_book 后 tts 缓存键 = content_key", app.tts._cache_key == ckey, app.tts._cache_key)
check("meta 回写 content_key", app.storage.get_book(bid).get("content_key") == ckey)
check("旧目录已懒迁移到 pct 目录",
      os.path.isdir(os.path.join(tts_root, ckey, voice, rate_dir_key(200))))
check("旧 bid 目录已消失", not os.path.exists(os.path.join(tts_root, bid)))
check("书架按书大小 > 0", app._audio_cache_size(bid) > 0, str(app._audio_cache_size(bid)))
check("全局大小 >= 该书大小", app.tts.tts_cache_size() >= app.tts.tts_cache_size(bid))
check("读取缓存不受影响：书仍可打开", app.book is not None and app.current_bid == bid)

# 二次打开（幂等，不应再动目录）
app.open_book(bid)
root.update_idletasks()
check("二次打开仍命中同一缓存键", app.tts._cache_key == ckey)

root.destroy()
print("\n==== %d FAIL ====" % len(FAIL))
for f in FAIL:
    print(f)
sys.exit(1 if FAIL else 0)
