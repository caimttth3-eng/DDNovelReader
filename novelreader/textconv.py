# -*- coding: utf-8 -*-
"""正文简繁转换（严格 1:1 保长）。

只做「字/等长词组」替换，转换前后 len() 完全相等：
阅读位置、朗读高亮、书签、插图锚点全部按原文字符偏移计算，长度一变就会全错位。

模式：none（不转换）/ s2t（简体→繁体）/ t2s（繁体→简体）。
"""
MODE_NONE = "none"
MODE_S2T = "s2t"
MODE_T2S = "t2s"
MODES = (MODE_NONE, MODE_S2T, MODE_T2S)

from .textconv_data import (  # noqa: E402
    S2T_CHARS,
    S2T_PHRASES,
    T2S_CHARS,
    T2S_PHRASES,
)


def _bucket(phrases):
    """首字 → [(长度, 词组, 目标)]，长度降序：最长匹配优先。"""
    b = {}
    for k, v in phrases.items():
        if len(k) < 2:
            continue
        b.setdefault(k[0], []).append((len(k), k, v))
    for lst in b.values():
        lst.sort(key=lambda x: -x[0])
    return b


_S2T_BUCKET = _bucket(S2T_PHRASES)
_T2S_BUCKET = _bucket(T2S_PHRASES)
_TABLES = {
    MODE_S2T: (S2T_CHARS, _S2T_BUCKET),
    MODE_T2S: (T2S_CHARS, _T2S_BUCKET),
}


def normalize_mode(mode):
    return mode if mode in (MODE_S2T, MODE_T2S) else MODE_NONE


def convert(text, mode):
    """按 mode 转换 text；保证返回长度与输入完全一致（异常时原样返回）。"""
    mode = normalize_mode(mode)
    if mode == MODE_NONE or not text:
        return text
    chars, bucket = _TABLES[mode]
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        cands = bucket.get(ch)
        if cands:
            for length, src, dst in cands:
                if i + length <= n and text.startswith(src, i):
                    out.append(dst)
                    i += length
                    break
            else:
                out.append(chars.get(ch, ch))
                i += 1
            continue
        out.append(chars.get(ch, ch))
        i += 1
    result = "".join(out)
    if len(result) != n:
        # 理论上不会发生（表里全是等长映射）；真发生就原样返回，绝不破坏偏移
        return text
    return result
