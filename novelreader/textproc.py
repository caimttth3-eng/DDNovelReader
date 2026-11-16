# -*- coding: utf-8 -*-
"""TTS 朗读用文本预处理：生成纯净朗读文本 + 原文偏移映射。

背景：同一份正文有两套呈现——
- 显示给用户：原文保留全部标点、空白、颜文字；
- 后台朗读：去除会造成 TTS 静音/卡顿的内容（连续标点、空白、emoji、颜文字）。

本模块提供 `preprocess_for_tts(content)`：
- 返回 (纯净文本, 映射表)，映射表[i] = 纯净文本第 i 个字符在原文中的下标；
- 利用映射表可在「纯净偏移 ↔ 原文偏移」间双向换算（原文→纯净用二分查找）。

百万字级长篇小说性能：
- 清洗为单次线性扫描（< 0.2s / 百万字）；
- 映射表用 array('I') 存储，每字符 4 字节（百万字 ≈ 4MB），可接受。
"""
import bisect
import re
import unicodedata
from array import array

# 需要删除的空白字符：半角/全角空格、制表、换行、零宽、BOM 等
_WS = frozenset(
    " \t\n\r\v\f\u3000\u00a0\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"
)

# 标点（TTS 停顿点）：保留单个，折叠连续多个
_PUNCT = frozenset(
    "，。！？；：、,.!?;:…·—～~‘’“”\"'()（）[]【】{}《》〈〉"
)
# 句子终止符：段落/句子停顿的关键，折叠时优先保留
_SENT_END = frozenset("。！？；!?;")


def _is_emoji(ch):
    """判断是否 emoji / 装饰符号（含变体选择符、ZWJ）。"""
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF  # 表情符号、补充符号等
        or 0x2600 <= cp <= 0x27BF  # 杂项符号、装饰符号、几何形状
        or 0x2190 <= cp <= 0x21FF  # 箭头
        or 0x2B00 <= cp <= 0x2BFF  # 杂项符号和箭头
        or cp in (0xFE0F, 0xFE0E, 0x200D)
    )


def _is_decor_symbol(ch):
    """判断是否颜文字 / 装饰类符号（符号类 Unicode 类别，非 CJK/字母/数字）。"""
    try:
        cat = unicodedata.category(ch)
    except Exception:
        return False
    return cat in ("Sk", "So", "Pc")  # 修饰符、其它符号、连接符（如 ^_^ 的下划线）


def _is_ws(ch):
    return ch in _WS


def _is_punct(ch):
    return ch in _PUNCT


def _is_sent_end(ch):
    return ch in _SENT_END


# 有朗读价值的最小字符集：中日韩文字 / 假名 / 谚文 / 字母 / 数字
_READABLE_RE = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7afA-Za-z0-9]"
)
# 行尾连续出现的装饰性符号（如 -------------、====、***），不构成朗读内容
_TAIL_DECOR_RE = re.compile(r"[=\-_~*#·—]{3,}\s*$")


def _strip_decor_lines(content):
    """剔除装饰/分隔行，返回 (过滤后文本, 过滤文本下标 → 原文下标)。

    网文正文常在章末挂一行分隔符（如 `------------`）。这类行不含任何可读字符，
    却会被切成“一句”送去合成：产出音频仅几十毫秒，而合成要花掉一次完整网络往返，
    正好把预取队列在这一位置抽干 → 自动跨章时出现一次可感知空档。
    """
    out = []
    idx = array("I")
    pos = 0
    n = len(content)
    while pos < n:
        nl = content.find("\n", pos)
        end = n if nl < 0 else nl
        line = content[pos:end]
        m = _TAIL_DECOR_RE.search(line)
        keep_len = m.start() if m else len(line)
        if _READABLE_RE.search(line[:keep_len]):
            out.append(line[:keep_len])
            idx.extend(range(pos, pos + keep_len))
        pos = end + 1
    return "".join(out), idx


def preprocess_for_tts(content):
    """把原文清洗为 TTS 朗读文本，并返回「纯净下标 → 原文下标」映射表。

    规则：
    1. 删除装饰/分隔行（整行无可读字符，如 `------------`），不送去合成；
    2. 删除所有空白（空格/制表/换行/全角空格/零宽字符等）；
    3. 删除 emoji 与颜文字/装饰符号；
    4. 连续标点折叠为一个：优先保留运行中的句子终止符（如“。！？”保留一个
       终止符），否则保留第一个标点——避免 TTS 长时间静音，同时保住句子停顿。

    返回：
        (clean_text: str, cmap: array('I'))
    """
    content, base = _strip_decor_lines(content)
    out = []
    cmap = array("I")
    n = len(content)
    i = 0
    while i < n:
        ch = content[i]
        # 0. 空白 → 删除
        if _is_ws(ch):
            i += 1
            continue
        # 1. emoji / 颜文字 / 装饰符号 → 删除
        if _is_emoji(ch) or _is_decor_symbol(ch):
            i += 1
            continue
        # 2. 连续标点折叠
        if _is_punct(ch):
            j = i + 1
            while j < n and (
                _is_punct(content[j]) or _is_ws(content[j]) or _is_emoji(content[j])
                or _is_decor_symbol(content[j])
            ):
                j += 1
            # 优先保留最后一个句子终止符，否则保留第一个标点
            keep = -1
            for k in range(j - 1, i - 1, -1):
                if _is_sent_end(content[k]):
                    keep = k
                    break
            if keep < 0:
                keep = i
            out.append(content[keep])
            cmap.append(base[keep])
            i = j
            continue
        # 普通字符保留
        out.append(ch)
        cmap.append(base[i])
        i += 1
    return "".join(out), cmap


def clean_to_orig(cmap, clean_off, orig_len):
    """纯净偏移 → 原文偏移。clean_off 越界（章节末尾）时返回原文长度。"""
    if clean_off >= len(cmap):
        return orig_len
    return cmap[clean_off]


def orig_to_clean(cmap, orig_off):
    """原文偏移 → 纯净偏移（二分查找：指向 >= orig_off 的第一个保留字符）。"""
    return bisect.bisect_left(cmap, orig_off)
