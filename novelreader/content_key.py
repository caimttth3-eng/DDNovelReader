"""跨端稳定的书籍指纹与缓存参数命名（PC 与安卓共用的唯一实现）。

这里只放纯函数，禁止 import 本项目其它模块（sync_engine / tts_engine /
storage 都从这里取），避免循环依赖，也保证「WebDAV 同步算出的 content_key」
与「音频缓存目录一级键」永远同一个值。

口径要点（ck2，2026-11 起，两端必须一致）：
- 输入只有三样：书名、全文正文字符数、全文开头 1000 字（都先去掉所有空白）。
- 「全文」= 所有章节正文首尾相接，不含章节标题行。
- 字数按去掉空白后的长度算，因此段落数量、换行、缩进、全角空格都不影响结果；
  安卓端把「段落 display 首尾相接」喂进来，PC 端把「各章 content 首尾相接」喂进来，
  两端算出的数字相同。
- 不再用「第 0 章正文」或「源文件前 64KB」做样本：前者依赖分章规则，后者依赖文件
  编码识别，两端口径必然不同。ck1 时代的历史 key 由 compute_legacy_key 兼容。
"""

import hashlib
import os
import re

# content_key 版本前缀：算法变更时必须同时改 PC 与安卓，否则跨端 key 不再相等
CK_VERSION = "ck2"
CK_VERSION_LEGACY = "ck1"
SAMPLE_LIMIT = 2000          # 参与指纹的开头正文字符数上限（norm 之后）
LEGACY_SAMPLE_LIMIT = 1000   # ck1 口径的开头样本长度（历史记录匹配专用）
SAMPLE_FILE_BYTES = 65536    # 兜底读源文件的字节上限


def norm_text(s):
    """去掉所有空白，消除换行/编码差异对指纹的影响。"""
    return re.sub(r"\s+", "", s or "")


def _digest(version, title, number, sample_text, limit=SAMPLE_LIMIT):
    raw = "%s|%s|%d|%s" % (
        version,
        norm_text(title),
        int(number or 0),
        norm_text(sample_text)[:limit],
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def compute_content_key(title, chapter_count, full_text):
    """书名 + 章节数 + 全文开头 2000 字 → 16 位指纹（ck2）。

    为什么不用「全文总字数」：两端解码器对源文件里的非法字节处理不同
    （PC 有 GB18030/big5 探测，安卓只有 UTF-8），一本 50 万字书里夹几段乱码
    就会让总字数差几个数，整本 key 随之漂移。改成开头样本后，只有「开头 2000
    字本身解码不同」这种书才会 key 不同——那种情况下两端正文本来就不一样，
    共用缓存也没有意义。
    """
    return _digest(CK_VERSION, title, chapter_count, full_text)


def compute_legacy_key(title, total_chars, sample_text):
    """ck1 指纹：仅供匹配历史 WebDAV 记录，不要用于新目录命名。"""
    return _digest(CK_VERSION_LEGACY, title, total_chars, sample_text,
                   limit=LEGACY_SAMPLE_LIMIT)


def chapters_text(chapters):
    """章节对象/字典列表 → 首尾相接的全文正文（不含标题）。"""
    out = []
    for c in chapters or []:
        if isinstance(c, dict):
            out.append(c.get("content") or c.get("text") or "")
        else:
            out.append(getattr(c, "content", "") or "")
    return "".join(out)


def sample_text_for_book(storage, meta):
    """取开头正文样本：优先解析缓存里的全部章节正文，其次源文件前 64KB。

    只作为「算不出全文时」的兜底；正常路径应走 content_key_of_book 的全文口径。
    """
    bid = (meta or {}).get("id") or ""
    if bid:
        try:
            data = storage.read_cache(bid, any_version=True)
            if isinstance(data, dict):
                chapters = data.get("chapters") or []
                if chapters:
                    return chapters_text(chapters)
        except Exception:
            pass
    for key in ("source_bak", "path"):
        p = (meta or {}).get(key) or ""
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                raw = f.read(SAMPLE_FILE_BYTES)
            from .book_loader import _decode
            txt = _decode(raw)
            if txt:
                return txt
        except Exception:
            continue
    return ""


def book_full_text(storage, meta):
    """元数据 → 全文正文：先读解析缓存（完整），读不到再退回源文件全文。"""
    bid = (meta or {}).get("id") or ""
    if bid:
        try:
            data = storage.read_cache(bid, any_version=True)
            if isinstance(data, dict):
                chapters = data.get("chapters") or []
                if chapters:
                    return chapters_text(chapters)
        except Exception:
            pass
    return _file_text(meta)


def _file_text(meta):
    for key in ("source_bak", "path"):
        p = (meta or {}).get(key) or ""
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                raw = f.read()
            from .book_loader import _decode
            txt = _decode(raw)
            if txt:
                return txt
        except Exception:
            continue
    return ""


def book_parts(storage, meta):
    """元数据 → (章节数, 全文正文)。

    章节数与全文必须来自**同一份解析结果**（缓存里的 chapters），否则两端数字
    会对不上；读不到缓存时退回源文件全文，章节数取元数据里的 chapter_titles。
    """
    meta = meta or {}
    bid = meta.get("id") or ""
    if bid:
        try:
            data = storage.read_cache(bid, any_version=True)
            if isinstance(data, dict):
                chapters = data.get("chapters") or []
                if chapters:
                    return len(chapters), chapters_text(chapters)
        except Exception:
            pass
    count = len(meta.get("chapter_titles") or []) or int(meta.get("chapter_count") or 0)
    return count, _file_text(meta)


def content_key_of_book(storage, meta):
    """书架元数据 → content_key。已有缓存值直接返回，否则现算（调用方可回写）。"""
    key = str((meta or {}).get("content_key") or "").strip()
    if key:
        return key
    count, text = book_parts(storage, meta)
    return compute_content_key((meta or {}).get("title") or "", count, text)


def content_key_of_chapters(title, chapters, total_chars=None):
    """内存里的 BookContent → content_key（导入时/无 storage 时使用）。

    total_chars 只为兼容旧调用保留，不再参与计算。
    """
    chapters = chapters or []
    return compute_content_key(title, len(chapters), chapters_text(chapters))


def rate_dir_key(rate):
    """语速目录名 = 实际传给 Edge TTS 的百分比整数，与安卓端对齐。

    PC 内部 rate 用 50~400 的整数标度（200 = +0%，见 synth_audio：
    rate_adj = int((clamp(rate) - 200) / 2)）；
    安卓 speechRate 用倍率（1.0 = +0%，pct = (speechRate - 1) * 100）。
    两端在「同一合成参数」下 pct 相同：rate=300 ↔ 1.50 ↔ +50%。
    因此目录名统一取 pct，"-50" / "0" / "50" 直接对应合成参数，跨端可命中。
    """
    try:
        pct = int((max(50, min(400, int(rate))) - 200) / 2)
    except Exception:
        pct = 0
    return str(pct)


def rate_pct_of_speech_rate(speech_rate):
    """安卓倍率 → pct（安卓侧对齐用，PC 侧不直接调用，保持算法同源可查）。"""
    try:
        return int(round((float(speech_rate) - 1.0) * 100))
    except Exception:
        return 0


def rate_from_pct(pct):
    """pct → PC 整数标度（迁移/换算用）。"""
    try:
        return 200 + 2 * int(pct)
    except Exception:
        return 200


# ---------------------------------------------------------------- 语速倍数（UI 展示口径，v2.3.0）
# 实测（2026-11-18，Edge zh-CN-XiaoxiaoNeural，同一句话去静音后时长）：
#   rate 100(-50%) → 0.50×   rate 200(+0%) → 1.00×   rate 300(+50%) → 1.50×
#   rate 350(+75%) → 1.74×   rate 400(+100%) → 1.99×
# 即真实倍数 ≈ rate / 200；Edge 服务端上限就是 +100%（2.00×），再往上（+125%…）被忽略，
# 所以界面只暴露 0.50×~2.00×，步进 0.05×（对应内部 rate 每 10 一档，缓存目录 pct 每 5 一档）。

RATE_MIN = 100      # 0.50× —— Edge 再慢也只能到约 0.50×，更小的标度没有实际效果
RATE_MAX = 400      # 2.00× —— Edge 上限 +100%
RATE_STEP = 10      # 0.05×


def multiplier_of_rate(rate):
    """PC 整数标度 → 真实语速倍数（0.50~2.00）。"""
    try:
        v = max(RATE_MIN, min(RATE_MAX, int(round(float(rate)))))
    except Exception:
        v = 200
    return v / 200.0


def rate_of_multiplier(mult):
    """真实语速倍数 → PC 整数标度（按 RATE_STEP 取整，夹在 0.50~2.00）。"""
    try:
        m = float(mult)
    except Exception:
        m = 1.0
    rate = int(round(m * 200.0 / RATE_STEP)) * RATE_STEP
    return max(RATE_MIN, min(RATE_MAX, rate))


def format_multiplier(rate):
    """界面显示用：'1.55×'。"""
    return "%.2f×" % multiplier_of_rate(rate)
