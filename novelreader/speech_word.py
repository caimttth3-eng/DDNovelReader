# -*- coding: utf-8 -*-
"""逐词高亮：词级时间轴模型（对齐安卓 1.3.0 的 SpeechWord / SpeechWordCollector）。

设计来源（安卓 v1.3.0 `lib/models/speech_word.dart`）：
- Edge 返回的 WordBoundary 元数据里，`Offset`/`Duration` 单位是 **100ns**，换算成毫秒要 /10000；
- 词文本是 HTML 转义的，需要反转义（&lt; &gt; &quot; &apos; &amp;）；
- 用「在朗读文本里按顺序 indexOf」的方式定位每个词，**同一个词重复出现也各有位置**；
- 定位不到的元数据直接丢掉，绝不凭空造位置（宁可少高亮，不可错高亮）。

PC 端差异：Python 的 edge_tts 库已经把元数据解析成 dict
（`{"type": "WordBoundary", "offset": <100ns>, "duration": <100ns>, "text": "词"}`），
所以这里主要做「定位 + 时间轴查询 + 缓存序列化」。
"""
import json

# Edge 时间单位：100 纳秒 → 毫秒
TICKS_PER_MS = 10000


class SpeechWord:
    """一个词的发音区间 + 它在朗读文本里的字符区间。"""

    __slots__ = ("start_ms", "duration_ms", "text_start", "text_end", "text")

    def __init__(self, start_ms, duration_ms, text_start, text_end, text):
        self.start_ms = int(start_ms)
        self.duration_ms = int(duration_ms)
        self.text_start = int(text_start)
        self.text_end = int(text_end)
        self.text = text

    @property
    def end_ms(self):
        return self.start_ms + self.duration_ms

    def to_json(self):
        return {
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
            "text_start": self.text_start,
            "text_end": self.text_end,
            "text": self.text,
        }

    @classmethod
    def from_json(cls, d):
        return cls(d.get("start_ms", 0), d.get("duration_ms", 0),
                   d.get("text_start", 0), d.get("text_end", 0), d.get("text", ""))

    def __repr__(self):                                          # pragma: no cover
        return "SpeechWord(%s, %d+%dms, [%d:%d])" % (
            self.text, self.start_ms, self.duration_ms, self.text_start, self.text_end)


def _unescape(text):
    """Edge 元数据里的 HTML 转义还原（顺序与安卓一致：先实体后 &amp;）。"""
    if not text:
        return ""
    return (text.replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&"))


class SpeechWordCollector:
    """按顺序把 Edge 的 WordBoundary 元数据落到朗读文本的字符坐标上。"""

    def __init__(self, source):
        self.source = source or ""
        self.words = []
        self._cursor = 0

    def add(self, text, offset_ticks, duration_ticks):
        """加入一个词；找不到位置 / 时间非法则忽略（与安卓一致）。"""
        word = _unescape(text or "")
        if not word:
            return False
        try:
            start = self.source.index(word, self._cursor)
        except ValueError:
            return False
        try:
            ticks = float(offset_ticks)
            dur = float(duration_ticks)
        except (TypeError, ValueError):
            return False
        if ticks < 0 or dur <= 0:
            return False
        self.words.append(SpeechWord(ticks / TICKS_PER_MS, dur / TICKS_PER_MS,
                                     start, start + len(word), word))
        self._cursor = start + len(word)
        return True

    def add_edge_chunk(self, chunk):
        """吃一个 edge_tts 的 WordBoundary 分片（dict）。"""
        if not isinstance(chunk, dict):
            return False
        if chunk.get("type") != "WordBoundary":
            return False
        return self.add(chunk.get("text"), chunk.get("offset"), chunk.get("duration"))

    def finish(self):
        return self.words


def word_at(words, position_ms):
    """二分查找播放位置命中的词；落在词间空隙或没有词时返回 None。"""
    if not words or position_ms is None or position_ms < 0:
        return None
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi) // 2
        if words[mid].start_ms <= position_ms:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    w = words[lo - 1]
    return w if position_ms < w.end_ms else None


def shift_words(words, delta_ms):
    """整句音频被裁剪后平移时间轴（delta_ms 为负表示去掉开头若干毫秒）。"""
    if not words or not delta_ms:
        return words
    out = []
    for w in words:
        start = w.start_ms + delta_ms
        if start + w.duration_ms <= 0:      # 完全落在被裁掉的头里
            continue
        start = max(0, start)
        out.append(SpeechWord(start, w.duration_ms, w.text_start, w.text_end, w.text))
    return out


def words_to_json(words):
    return json.dumps({"version": 1, "words": [w.to_json() for w in (words or [])]},
                      ensure_ascii=False)


def words_from_json(raw):
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    items = data.get("words") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for d in items:
        if isinstance(d, dict):
            try:
                out.append(SpeechWord.from_json(d))
            except Exception:                                    # noqa: BLE001
                pass
    return out


def align_to_display(clean_text, display_window):
    """把「朗读文本下标」映射到「显示窗口下标」。

    朗读文本是清洗过的（去掉标点/空白/表情），显示文本保留原样；
    两个指针同步前进：显示字符与朗读字符相同则建立映射，否则只推进显示指针。
    返回 list，长度 = len(clean_text)，元素是显示窗口内的下标（找不到时为 -1）。
    """
    mapping = [-1] * len(clean_text or "")
    if not clean_text or not display_window:
        return mapping
    di = 0
    disp_len = len(display_window)
    for ci, ch in enumerate(clean_text):
        while di < disp_len:
            if display_window[di] == ch:
                mapping[ci] = di
                di += 1
                break
            di += 1
    return mapping
