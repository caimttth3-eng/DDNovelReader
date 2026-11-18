# -*- coding: utf-8 -*-
"""逐词高亮词级模型自测（novelreader/speech_word.py）。

跑法： .venv\\Scripts\\python.exe tests\\test_speech_word.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from novelreader.speech_word import (                                   # noqa: E402
    SpeechWord, SpeechWordCollector, align_to_display, shift_words,
    word_at, words_from_json, words_to_json,
)

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def main():
    # 1) 基本定位：词在朗读文本里的坐标 + 100ns→ms 换算
    src = "第一章开始了今天天气很好"
    c = SpeechWordCollector(src)
    ok1 = c.add("第一章", 0, 3000000)          # 0~300ms
    ok2 = c.add("开始", 3000000, 2000000)      # 300~500ms
    ok3 = c.add("了", 5000000, 1000000)        # 500~600ms
    words = c.finish()
    check("三个词都定位成功", ok1 and ok2 and ok3 and len(words) == 3, words)
    check("时间换算正确（100ns→ms）", words[1].start_ms == 300 and words[1].duration_ms == 200, words[1])
    check("字符区间正确", (words[1].text_start, words[1].text_end) == (3, 5), words[1])
    check("end_ms = start+duration", words[1].end_ms == 500, words[1])

    # 2) 重复词各有坐标（安卓逻辑：按顺序 indexOf，而不是取第一次出现）
    src2 = "哈哈很好哈哈"
    c2 = SpeechWordCollector(src2)
    c2.add("哈哈", 0, 1000)
    c2.add("很好", 10000, 1000)
    c2.add("哈哈", 20000, 1000)
    w2 = c2.finish()
    check("重复词保留两个坐标", len(w2) == 3 and w2[0].text_start == 0 and w2[2].text_start == 4,
          [(w.text, w.text_start) for w in w2])

    # 3) 定位不到 / 时间非法的词直接丢弃
    c3 = SpeechWordCollector("你好世界")
    bad1 = c3.add("不存在", 0, 1000)
    bad2 = c3.add("你好", -1, 1000)
    bad3 = c3.add("世界", 0, 0)
    check("找不到位置的词被丢弃", bad1 is False)
    check("偏移为负被丢弃", bad2 is False)
    check("时长非正被丢弃", bad3 is False)
    check("全部丢弃后没有词", c3.finish() == [])

    # 4) HTML 转义还原
    c4 = SpeechWordCollector("a<b & c")
    c4.add("a&lt;b", 0, 1000)
    c4.add("&amp;", 5000, 500)
    w4 = c4.finish()
    check("转义后能定位", len(w4) == 2, [(w.text, w.text_start) for w in w4])
    check("转义词文本已还原", w4[1].text == "&", w4[1])

    # 5) edge_tts 分片入口（dict）
    c5 = SpeechWordCollector("今天很好")
    used = c5.add_edge_chunk({"type": "WordBoundary", "text": "今天", "offset": 0, "duration": 2000000})
    ignored = c5.add_edge_chunk({"type": "audio", "data": b"xx"})
    check("能吃 WordBoundary 分片", used is True)
    check("忽略 audio 分片", ignored is False)
    check("分片转换后 1 个词", len(c5.finish()) == 1)

    # 6) 播放位置查询（二分）
    ws = [SpeechWord(0, 100, 0, 1, "a"), SpeechWord(200, 100, 1, 2, "b"),
          SpeechWord(400, 100, 2, 3, "c")]
    check("位置 50 → 词 a", word_at(ws, 50) is not None and word_at(ws, 50).text == "a")
    check("位置 150（词间空隙）→ None", word_at(ws, 150) is None)
    check("位置 250 → 词 b", word_at(ws, 250).text == "b")
    check("位置 499 → None（c 已结束 500）", word_at(ws, 499).text == "c")
    check("位置 500 → None", word_at(ws, 500) is None)
    check("越界/负数安全", word_at(ws, -1) is None and word_at([], 10) is None)

    # 7) 裁剪平移
    shifted = shift_words(ws, -120)
    check("平移后起点按差额前移（200-120=80）", shifted[0].start_ms == 80, shifted)
    check("完全落在头部的词被丢掉", len(shifted) == 2, [w.text for w in shifted])
    check("未平移时原样返回", shift_words(ws, 0) is ws)

    # 8) 缓存序列化往返
    raw = words_to_json(ws)
    back = words_from_json(raw)
    check("序列化往返词数一致", len(back) == 3)
    check("序列化往返字段一致",
          (back[1].start_ms, back[1].duration_ms, back[1].text_start, back[1].text_end, back[1].text)
          == (200, 100, 1, 2, "b"), back[1])
    check("坏 JSON 安全返回空", words_from_json("{not json") == [])
    check("空输入安全返回空", words_from_json("") == [] and words_to_json([]) .startswith("{"))

    # 9) 朗读文本 → 显示文本对齐（标点/空白被清洗）
    clean = "今天天气很好"
    display = "今天，天气很好！"
    m = align_to_display(clean, display)
    check("对齐长度等于朗读文本", len(m) == len(clean), m)
    check("第一个字对齐到 0", m[0] == 0, m)
    check("标点被跳过（好 → 显示下标 6）", m[-1] == len(display) - 2, (m, display))
    check("空输入安全", align_to_display("", "x") == [] and align_to_display("x", "") == [-1])

    print("\n==== %d FAIL ====" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
