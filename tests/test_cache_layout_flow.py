# -*- coding: utf-8 -*-
"""真实数据流演练：导入书 → 写 content_key → 旧缓存布局 → 打开书触发懒迁移 → 按书统计。

不弹窗、不联网、不打字，全部在临时数据目录里跑，跑完即弃。
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


tmp_data = tempfile.mkdtemp(prefix="ddflow_data_")
tmp_tts = tempfile.mkdtemp(prefix="ddflow_tts_")
os.environ["DOUBAO_NOVEL_DATA"] = tmp_data

from novelreader import storage as st
from novelreader.book_loader import parse_book as load_content
from novelreader.content_key import content_key_of_chapters, rate_dir_key
from novelreader.tts_engine import SpeechController

sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample", "星火.txt")
s = st.Storage()
content = load_content(sample)
check("样例书解析成功", bool(content.chapters), "%d 章" % len(content.chapters))

bid = s.book_id(sample)
ckey = content_key_of_chapters(content.title, content.chapters)
meta = {
    "id": bid, "content_key": ckey, "title": content.title, "author": content.author,
    "format": content.format, "path": sample, "added_at": 0, "last_read_at": 0,
    "total_chars": content.total_chars,
    "chapter_titles": [c.title for c in content.chapters],
    "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
}
s.add_book(meta)
s.write_cache(bid, content)
check("meta 写入 content_key", s.get_book(bid).get("content_key") == ckey, ckey)

# --- 造旧布局：<tts>/<旧bid>/<语音>/200/ + progress(book_id=旧bid, rate=200 整数)
voice = "zh-CN-XiaoxiaoNeural"
old_rate = os.path.join(tmp_tts, bid, voice, "200")
os.makedirs(old_rate)
with open(os.path.join(old_rate, "0000_00000000.mp3"), "wb") as f:
    f.write(b"ID3" + b"\0" * 5000)
with open(os.path.join(old_rate, "progress.json"), "w", encoding="utf-8") as f:
    import json
    json.dump({"book_id": bid, "voice": voice, "rate": 200, "total": 1,
               "done": 1, "completed": [0], "state": "done"}, f)

ctl = SpeechController()
ctl.set_tts_cache_dir(tmp_tts)
ctl._backend = "edge"  # _cached_audio 只对 edge 后端生效（sapi 无磁盘缓存）
ctl.set_book_cache_keys({m.get("id"): m.get("content_key")
                         for m in s.all_books().values() if m.get("content_key")})
check("启动注册映射后 cache_key_for", ctl.cache_key_for(bid) == ckey, ctl.cache_key_for(bid))

# 打开书（真实 App 里 reader_ui.open_book 做的就是这个：set_book_id + 迁移）
ctl.set_book_id(bid, ckey)
ctl._lazy_migrate_book_dir(bid, ckey)
new_rate = os.path.join(tmp_tts, ckey, voice, rate_dir_key(200))
check("旧目录已迁移到 <content_key>/<语音>/<pct>", os.path.isdir(new_rate), new_rate)
check("旧目录不再存在", not os.path.exists(os.path.join(tmp_tts, bid)))
check("mp3 字节完好", os.path.getsize(os.path.join(new_rate, "0000_00000000.mp3")) == 5003)
check("按书统计读到缓存（索引+落盘）", ctl.tts_cache_size(bid) > 0, str(ctl.tts_cache_size(bid)))

# --- 缓存读写命中真实句子偏移（切句/偏移链路必须通，否则跨端文件对不上）
ch = content.chapters[0]
clean_text, cmap = ch.tts_content()
from novelreader.tts_engine import split_sentences, clean_to_orig
text = split_sentences(clean_text)[0]
orig_off = clean_to_orig(cmap, clean_text.find(text), len(ch.content))
hit_path = os.path.join(new_rate, "%04d_%08d.mp3" % (0, orig_off))
with open(hit_path, "wb") as f:
    f.write(b"ID3" + b"\0" * 100)
check("真实首句偏移文件已生成", os.path.isfile(hit_path), os.path.basename(hit_path))
check("_cached_audio 按当前书命中", ctl._cached_audio(0, orig_off) is not None)

# --- 另一种语速：300 → 目录 50（安卓 1.50 倍率对应同一合成参数）
ctl2 = SpeechController()
ctl2.set_tts_cache_dir(tmp_tts)
ctl2.set_book_id(bid, ckey)
os.makedirs(os.path.join(tmp_tts, ckey, voice, rate_dir_key(300)), exist_ok=True)
check("语速 300 目录名 = 50", rate_dir_key(300) == "50")

# --- 幂等：再迁移一次不动任何东西
ctl._lazy_migrate_book_dir(bid, ckey)
check("二次懒迁移幂等", os.path.isdir(new_rate))

print("\n==== %d FAIL ====" % len(FAIL))
for f in FAIL:
    print(f)
sys.exit(1 if FAIL else 0)
