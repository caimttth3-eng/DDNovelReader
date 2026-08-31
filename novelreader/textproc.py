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


def preprocess_for_tts(content):
    """把原文清洗为 TTS 朗读文本，并返回「纯净下标 → 原文下标」映射表。

    规则：
    1. 删除所有空白（空格/制表/换行/全角空格/零宽字符等）；
    2. 删除 emoji 与颜文字/装饰符号；
    3. 连续标点折叠为一个：优先保留运行中的句子终止符（如“。！？”保留一个
       终止符），否则保留第一个标点——避免 TTS 长时间静音，同时保住句子停顿。

    返回：
        (clean_text: str, cmap: array('I'))
    """
    out = []
    cmap = array("I")
    n = len(content)
    i = 0
    while i < n:
        ch = content[i]
        # 1. 空白 → 删除
        if _is_ws(ch):
            i += 1
            continue
        # 2. emoji / 颜文字 / 装饰符号 → 删除
        if _is_emoji(ch) or _is_decor_symbol(ch):
            i += 1
            continue
        # 3. 连续标点折叠
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
            cmap.append(keep)
            i = j
            continue
        # 普通字符保留
        out.append(ch)
        cmap.append(i)
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
