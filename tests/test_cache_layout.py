# -*- coding: utf-8 -*-
"""跨端缓存布局（content_key + 语速百分比）自测。

覆盖：指纹稳定性与旧算法一致、语速映射、缓存目录、progress 双键兼容、
旧整数 rate 兼容、按书懒迁移与 progress 改写、大小索引键一致。
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from novelreader import content_key as ck
from novelreader import storage
from novelreader.tts_engine import SpeechController, WholeBookCacher

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append("%s %s" % (name, extra))
        print("[FAIL] %s %s" % (name, extra))
    else:
        print("[ OK ] %s" % name)


# 1) 指纹与旧 sync_engine 实现逐字节一致（老文件写下的 content_key 不能漂移）
import hashlib
import re


def legacy_key(title, total_chars, sample_text):
    raw = "ck1|%s|%d|%s" % (
        re.sub(r"\s+", "", title or ""),
        int(total_chars or 0),
        re.sub(r"\s+", "", sample_text or "")[:1000],
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


t, tc, s = "斗破 苍穹", 1234567, "第一章\n  少年  萧炎，斗志为零。"
check("ck1 兜底函数与旧算法一致", ck.compute_legacy_key(t, tc, s) == legacy_key(t, tc, s),
      ck.compute_legacy_key(t, tc, s))
check("ck2 指纹基于开头样本（不含空白，与段落结构无关）",
      ck.compute_content_key("斗破苍穹", 9, "第一章少年萧炎，斗志为零。")
      == ck.compute_content_key("斗破 苍穹", 9, "第一章\n少年  萧炎，斗志为零。"))
check("ck2 章节数参与指纹",
      ck.compute_content_key("斗破苍穹", 9, "第一章少年萧炎")
      != ck.compute_content_key("斗破苍穹", 8, "第一章少年萧炎"))
check("ck2 与 ck1 不同版本前缀",
      ck.compute_content_key(t, 9, s) != ck.compute_legacy_key(t, tc, s))
# sync_engine 转发同一实现
from novelreader import sync_engine
check("sync_engine 使用同一实现",
      sync_engine.CK_VERSION == ck.CK_VERSION)

# 2) 语速映射：PC 整数标度 ↔ 合成百分比
cases = [(200, "0"), (300, "50"), (100, "-50"), (50, "-75"), (400, "100"), (250, "25")]
for rate, pct in cases:
    check("rate_dir_key(%d)==%s" % (rate, pct), ck.rate_dir_key(rate) == pct, ck.rate_dir_key(rate))
check("rate_from_pct 回环", ck.rate_from_pct(ck.rate_dir_key(300)) == 300)

# 3) 缓存目录：一级键 = content_key，三级 = 语速百分比
tmp = tempfile.mkdtemp(prefix="ddlay_")
book = types.SimpleNamespace(title="测试书", total_chars=100, chapters=[])
c = WholeBookCacher(book, "legacy_bid_xxx", tmp, "zh-CN-XiaoxiaoNeural", 200,
                    preset_tasks=[(0, 0, "a"), (0, 10, "b"), (0, 20, "c")],
                    cache_key="ck_abcdef")
d = os.path.relpath(c._dir, tmp).replace("\\", "/")
check("目录 = <content_key>/<语音>/<pct>", d == "ck_abcdef/zh-CN-XiaoxiaoNeural/0", d)

# 4) progress 双键写出 + 新键读取
c._done = 2
c._completed = {0, 1}
c._state = "paused"
c._save_progress()
with open(os.path.join(c._dir, "progress.json"), "r", encoding="utf-8") as f:
    data = json.load(f)
check("progress 同时写 book_id/bookId", data.get("book_id") == "ck_abcdef" and data.get("bookId") == "ck_abcdef", str(data))
check("progress rate 是百分比字符串", data.get("rate") == "0", str(data.get("rate")))
c2 = WholeBookCacher(book, "legacy_bid_xxx", tmp, "zh-CN-XiaoxiaoNeural", 200,
                     preset_tasks=[(0, 0, "a"), (0, 10, "b"), (0, 20, "c")],
                     cache_key="ck_abcdef")
prog = c2._load_progress()
check("新格式 progress 可读", prog == (2, {0, 1}), str(prog))

# 5) 旧格式 prose：book_id=旧 bid、rate=200（整数）→ 仍可读（升级后进度不丢）
with open(os.path.join(c._dir, "progress.json"), "w", encoding="utf-8") as f:
    json.dump({"book_id": "legacy_bid_xxx", "voice": "zh-CN-XiaoxiaoNeural", "rate": 200,
               "total": 3, "done": 1, "completed": [0], "state": "paused"}, f)
check("旧整数 rate + 旧 book_id 可读", c2._load_progress() == (1, {0}))
# 安卓格式：bookId + rate "0"
with open(os.path.join(c._dir, "progress.json"), "w", encoding="utf-8") as f:
    json.dump({"bookId": "ck_abcdef", "voice": "zh-CN-XiaoxiaoNeural", "rate": "0",
               "total": 3, "done": 3, "completed": [0, 1, 2], "state": "done"}, f)
check("安卓 camelCase 键可读", c2._load_progress() == (3, {0, 1, 2}))

# 6) 按书懒迁移：<root>/<bid>/<voice>/200 → <root>/<key>/<voice>/0，并改写 progress
tmp2 = tempfile.mkdtemp(prefix="ddmig_")
old_rate_dir = os.path.join(tmp2, "legacy_bid_xxx", "zh-CN-XiaoxiaoNeural", "200")
os.makedirs(old_rate_dir)
with open(os.path.join(old_rate_dir, "0000_00000000.mp3"), "wb") as f:
    f.write(b"ID3fake")
with open(os.path.join(old_rate_dir, "progress.json"), "w", encoding="utf-8") as f:
    json.dump({"book_id": "legacy_bid_xxx", "voice": "zh-CN-XiaoxiaoNeural", "rate": 200,
               "total": 1, "done": 1, "completed": [0], "state": "done"}, f)
ctl = SpeechController()
ctl.set_tts_cache_dir(tmp2)
ctl._lazy_migrate_book_dir("legacy_bid_xxx", "ck_abcdef")
new_rate_dir = os.path.join(tmp2, "ck_abcdef", "zh-CN-XiaoxiaoNeural", "0")
check("顶层目录改名 + 语速目录改名", os.path.isdir(new_rate_dir), new_rate_dir)
check("mp3 仍在（rename 不搬数据）", os.path.isfile(os.path.join(new_rate_dir, "0000_00000000.mp3")))
check("旧目录已消失", not os.path.exists(os.path.join(tmp2, "legacy_bid_xxx")))
with open(os.path.join(new_rate_dir, "progress.json"), "r", encoding="utf-8") as f:
    pd = json.load(f)
check("progress 键与 rate 已改写",
      pd.get("bookId") == "ck_abcdef" and pd.get("rate") == "0", str(pd))
# 幂等：再跑一次不报错、目录不变
ctl._lazy_migrate_book_dir("legacy_bid_xxx", "ck_abcdef")
check("懒迁移幂等", os.path.isdir(new_rate_dir))

# 7) 大小索引键与真实目录一一对应
check("tts_size_key 整数入参归一", storage.tts_size_key("ck_abcdef", "zh-CN-XiaoxiaoNeural", 200)
      == "ck_abcdef/zh-CN-XiaoxiaoNeural/0")
check("tts_size_key 目录名入参保持", storage.tts_size_key("ck_abcdef", "zh-CN-XiaoxiaoNeural", "-50")
      == "ck_abcdef/zh-CN-XiaoxiaoNeural/-50")
res = storage.calibrate_tts_size(tmp2)
idx = storage.load_tts_size_index(tmp2)  # 直接是 entries dict
check("calibrate 索引键 = 目录层级", "ck_abcdef/zh-CN-XiaoxiaoNeural/0" in idx, str(list(idx.keys())))
check("calibrate 汇总该书总字节", res.get("ck_abcdef", 0) > 0, str(res))
check("audio_cache_dirs 按 key 命中", len(storage.audio_cache_dirs(tmp2, "ck_abcdef")) == 1)
check("未知 book_id 回退原 id（旧目录仍可统计）",
      ctl.cache_key_for("no_such_bid") == "no_such_bid")

# 8) 批量注册映射
ctl.set_book_cache_keys({"bid_a": "ck_a", "bid_b": "ck_b"})
check("set_book_cache_keys / cache_key_for", ctl.cache_key_for("bid_a") == "ck_a")
check("tts_cache_size 用映射键", ctl.tts_cache_size("bid_b") >= 0)

print("\n==== %d FAIL ====" % len(FAIL))
for f in FAIL:
    print(f)
sys.exit(1 if FAIL else 0)
