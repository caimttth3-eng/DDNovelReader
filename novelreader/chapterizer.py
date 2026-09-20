# -*- coding: utf-8 -*-
"""章节切分模块：把整本小说的纯文本按章节标题切分为章节列表。"""
import re

_NUM = r"[0-9０-９零〇○一二三四五六七八九十百千万两]+"
_UNIT = r"[章节回卷部集篇]"

# 严格匹配：行首就是第N章 / Chapter N / 楔子等
_HEAD_LINE = re.compile(
    r"^\s{0,4}(?:"
    rf"第{_NUM}{_UNIT}(?:\s*[：:、.\-—].*|\s+.*|\s*)?"
    r"|(?:[Cc]hapter|CHAPTER)\s+[0-9IVXLCivxl]+[：:.\s]?.*"
    r"|(?:楔子|序章|序言|前言|引子|引言|尾声|后记|番外|终章|大结局|完结篇)(?:\s*[：:、.\-—].*|\s+.*|\s*)?"
    r")\s*$"
)

# 宽松匹配：行尾是第N章（可能带书名前缀），用于匹配"书名 第1章"格式
_TAIL_CHAPTER = re.compile(
    r"(?:"
    rf"第{_NUM}{_UNIT}(?:\s*[：:、.\-—].*|\s+.*|\s*)?"
    r"|(?:[Cc]hapter|CHAPTER)\s+[0-9IVXLCivxl]+[：:.\s]?.*"
    r"|(?:楔子|序章|序言|前言|引子|引言|尾声|后记|番外|终章|大结局|完结篇)(?:\s*[：:、.\-—].*|\s+.*|\s*)?"
    r")\s*$"
)

# 分隔线：连续的破折号/等号/星号/波浪号（>=10个），常见于章节标题下方
_SEP_LINE = re.compile(r"^[\s\-=*~—–]{10,}$")

# 非标题标记：分隔线匹配时排除这些行
_NON_TITLE_MARKERS = ("http", "来源", "作者", "简介", "目录", "更新", "下载", "更多", "推荐", "收藏", "点击")

# ---------- 内层章节标题：正文中穿插的"第X章 章节名"（带章节名，前面是句尾标点/省略号/双全角空格/行首） ----------
# 很多网站书是"外层大段(书名 第N章) + 内层真章节(第N章 章节名)"结构，内层才是真正章节。
_INNER_NUM_RE = re.compile(rf"第({_NUM})章")
_INNER_NAME_RE = re.compile(r"(?=[ \t\u3000：:])[ \t\u3000]*[：:]?[ \t\u3000]*([^\s，。；…,.!;:]{2,30})")
# 前置：行首 / 双全角空格 / 句尾标点或省略号或右引号（允许 "……" 场景）
_INNER_PRE = re.compile(r"(?:[\n。！？；…”’!?;】》〕）]|\.{2,})$")

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000, "万": 10000}


def _parse_ord(seg):
    """把'第N章'里的 N 解析为 int（支持 阿拉伯数字 / 中文数字 / 全角数字）。"""
    seg = re.sub(r"[０-９]", lambda m: str("０１２３４５６７８９".index(m.group(0))), seg)
    if seg.isdigit():
        return int(seg)
    total, cur = 0, 0
    for ch in seg:
        if ch in "零〇":
            continue
        if ch in _CN_NUM:
            v = _CN_NUM[ch]
            if v >= 10:
                if cur == 0:
                    cur = 1
                total += cur * v
                cur = 0
            else:
                cur += v
    return total + cur


def _extract_title(line):
    """从一行中提取章节标题。如果行首不是第N章（带书名前缀），则从第N章开始截取。"""
    line = line.strip(" \t\u3000")
    # 尝试从"第N章"位置开始截取
    m = re.search(rf"第{_NUM}{_UNIT}", line)
    if m and m.start() > 0:
        return line[m.start():].strip()
    # 尝试从 Chapter 开始截取
    m = re.search(r"(?:[Cc]hapter|CHAPTER)\s+[0-9IVXLCivxl]+", line)
    if m and m.start() > 0:
        return line[m.start():].strip()
    # 尝试从特殊章节名开始截取
    for kw in ("楔子", "序章", "序言", "前言", "引子", "引言", "尾声", "后记", "番外", "终章", "大结局", "完结篇"):
        idx = line.find(kw)
        if idx > 0:
            return line[idx:].strip()
    return line


def _looks_like_title(line):
    """判断一行是否'看起来像'章节标题（用于分隔线匹配的二次过滤）。"""
    if len(line) > 60:
        return False
    low = line.lower()
    for marker in _NON_TITLE_MARKERS:
        if marker in low:
            return False
    # 包含章节关键词
    if re.search(rf"第{_NUM}{_UNIT}", line):
        return True
    if re.search(r"(?:[Cc]hapter|CHAPTER)\s+[0-9IVXLCivxl]+", line):
        return True
    for kw in ("楔子", "序章", "序言", "前言", "引子", "引言", "尾声", "后记", "番外", "终章", "大结局", "完结篇"):
        if kw in line:
            return True
    # 纯短标题（< 15字，以数字开头，可能是"001 初入江湖"格式）
    if len(line) <= 15 and re.match(r"^[0-9０-９]+[\s、.\-—]", line):
        return True
    return False


def _detect_heads(lines):
    """检测所有章节标题行，返回 [(line_index, title), ...]。

    三种检测方式（按优先级，同一行不重复）：
    1. 严格匹配：行首就是第N章 / Chapter N / 楔子等
    2. 分隔线匹配：下一行是分隔线（----------------），且当前行看起来像标题
    3. 宽松匹配：行尾是第N章（带书名前缀如"书名 第1章"），行长度 <= 80
    """
    heads = []
    seen = set()

    for i, raw in enumerate(lines):
        line = raw.strip(" \t\u3000")
        if not line or len(line) > 200:
            continue
        if i in seen:
            continue

        is_head = False

        # 方式1：严格匹配（行首就是第N章）
        if _HEAD_LINE.match(line):
            is_head = True

        # 方式2：下一行是分隔线，且当前行看起来像标题
        elif i + 1 < len(lines):
            next_line = lines[i + 1].strip(" \t\u3000")
            if _SEP_LINE.match(next_line) and _looks_like_title(line):
                is_head = True

        # 方式3：宽松匹配（行尾是第N章，带书名前缀）
        if not is_head and len(line) <= 80 and _TAIL_CHAPTER.search(line):
            m = re.search(rf"第{_NUM}{_UNIT}", line)
            if m:
                prefix_raw = line[:m.start()]
                prefix = prefix_raw.rstrip(" \t\u3000")
                # 第N章前面必须有空格/全角空格分隔，或前缀为空/以标点结尾
                has_sep = prefix_raw != prefix  # rstrip 去掉了东西 → 前面有空白
                if not prefix or has_sep or (prefix and prefix[-1] in "!！?？》]】)）.。、,，"):
                    is_head = True

        if is_head:
            seen.add(i)
            title = _extract_title(line)
            heads.append((i, title))

    return heads


def _detect_inner_heads(lines):
    """检测正文中穿插的'第X章 章节名'（内层真章节）。

    返回 [(abs_offset, title, num_int_or_None), ...]。
    策略：收集所有"第X章 章节名"样式（允许标题嵌在正文行中间、前置为上一章正文，
    允许分隔 '：/: 等），不做前置标点限制——是否采用内层分章由
    _inner_continuous 的编号连续性与数量占比决定，避免普通书正文引用误判。
    """
    offsets = [0]
    for ln in lines[:-1]:
        offsets.append(offsets[-1] + len(ln) + 1)
    heads = []
    pre_ok = set('。！？；…”’!?;】》〕）.!?;:：')
    for i, line in enumerate(lines):
        line = line.strip("\r")
        for m in _INNER_NUM_RE.finditer(line):
            pre = line[:m.start()]
            if pre and pre[-1] not in pre_ok:
                continue
            after = line[m.end():]
            m2 = _INNER_NAME_RE.match(after)
            if not m2:
                continue
            name = m2.group(1)
            if len(name) > 30:
                continue
            title = "第" + m.group(1) + "章 " + name
            off = offsets[i] + m.start()
            heads.append((off, title, _parse_ord(m.group(1))))
    return heads

def _inner_continuous(heads):
    """内层标题是否可信（支持分卷编号重置）。"""
    nums = [n for _, _, n in heads if n is not None and n > 0]
    if len(nums) < 2:
        return False
    if len(nums) < 0.5 * len(heads):
        return False
    # 分卷编号重置：同一编号出现多次但每次都是递增的小范围序列
    # 只要章节数量足够多（>30），就认为是内层真章节
    if len(nums) >= 30:
        return True
    hi = max(nums)
    if len(set(nums)) / len(nums) < 0.85:
        return False
    return abs(hi - len(nums)) / max(hi, 1) <= 0.15


# 外层残留："书名 第N章" + 分隔线（内层切分后这些大段标题混在正文里，需清理）
_OUTER_RESID = re.compile(
    r"(?:^|\n)[^\n]{0,26}?第" + _NUM + r"章[ \t\u3000]*\n[ \t\u3000]*[-=—~*·]{10,}[ \t\u3000]*(?:\n|$)"
)


def _strip_outer_resid(body):
    """删除正文中残留的外层大段标题（书名 第N章 + 分隔线行）。"""
    return _OUTER_RESID.sub("", body)


# 正文开头残留的裸"第X章 ：标题"（作者把章节名重复写在正文第一行，如"第8章 ：标题　　正文"）
_INLINE_TITLE_RESID = re.compile(r"^第" + _NUM + r"章\s*[：:]\s*[^\n]*?\u3000{2,}")


def _strip_inline_title_resid(body):
    """清理章节正文开头行首残留的裸'第X章 ：标题'，只保留正文。"""
    lines = body.split("\n")
    hit = False
    for i in range(min(2, len(lines))):
        m = _INLINE_TITLE_RESID.match(lines[i])
        if m:
            rest = lines[i][m.end():]
            lines[i] = rest.strip(" \t\u3000")
            hit = True
    if not hit:
        return body
    return "\n".join(lines).strip("\n")


# 正文开头的"第X章 章节名"（作者把章节名重复写在正文第一行），支持"第8章 ：标题"样式
_INLINE_TITLE_FULL = re.compile(
    r"^第(\d+)章[\s\u3000]*[：:]?[\s\u3000]*([^\n]{1,40}?)(?=\u3000{2,}|\s{2,}|\n|$)"
)


def _absorb_inline_title(title, body):
    """识别正文开头行首的'第N章 章节名'：编号匹配 → 用带章节名的标题替换该章标题并清理该行；
    编号不匹配（如作者'二合一'错位）→ 仅清理该行标题文字，标题保持不变。"""
    m = re.match(r"第(\d+)章", title)
    num = int(m.group(1)) if m else None
    lines = body.split("\n")
    for i in range(min(2, len(lines))):
        mm = _INLINE_TITLE_FULL.match(lines[i])
        if not mm:
            continue
        inum = int(mm.group(1))
        name = mm.group(2).strip(" \t\u3000")
        rest = lines[i][mm.end():].strip(" \t\u3000")
        lines[i] = rest
        if num is not None and inum == num and name:
            title = "第%d章 %s" % (num, name)
        break
    return title, "\n".join(lines).strip("\n")


def _split_by_inner(text, heads):
    """按字符偏移切分（内层标题）。返回 [(title, body), ...]。"""
    heads = sorted(heads, key=lambda h: h[0])
    chapters = []
    intro = _strip_outer_resid(text[:heads[0][0]]).strip(" \t\u3000\n")
    if intro:
        chapters.append(("简介", intro))
    for k, (off, title, _num) in enumerate(heads):
        start = off + len(title)
        end = heads[k + 1][0] if k + 1 < len(heads) else len(text)
        body = _strip_outer_resid(text[start:end]).strip("\n")
        title, body = _absorb_inline_title(title, body)
        if body.strip():
            chapters.append((title, body))
    return chapters


def split_chapters(text):
    """尝试按章节标题切分。成功返回 [(title, content), ...]；无法可靠切分返回 None。

    优先识别正文中穿插的"第X章 章节名"内层标题（编号连续时按它切分），
    否则回退到行首标题（书名 第N章 / 严格第N章 / 分隔线）。
    """
    lines = text.split("\n")

    # 内层标题（带章节名的行内标题，编号连续）优先
    inner = _detect_inner_heads(lines)
    if _inner_continuous(inner):
        chapters = _split_by_inner(text, inner)
        if len(chapters) >= 2:
            return chapters

    heads = _detect_heads(lines)

    # 至少 2 个标题才认为是已带章节的文本，避免误切
    if len(heads) < 2:
        return None

    chapters = []
    # 第一章标题之前的文字（书名/简介/前言等）归入"简介"章，排在第一章之前
    intro = "\n".join(lines[:heads[0][0]]).strip(" \t\u3000\n")
    if intro:
        chapters.append(("简介", intro))

    for k, (idx, title) in enumerate(heads):
        start = idx + 1
        # 跳过标题下方的分隔线（----------------）
        if start < len(lines) and _SEP_LINE.match(lines[start].strip(" \t\u3000")):
            start += 1
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = "\n".join(lines[start:end]).strip("\n")
        title, body = _absorb_inline_title(title, body)
        if body.strip():
            chapters.append((title, body))

    if len(chapters) < 2:
        # 标题集中在文首（如目录），正文没被切到 → 不可靠
        return None
    # 给没有换行的正文按句子加上换行（如空格分段的文本）
    chapters = [(_t, _add_linebreaks(_b)) for _t, _b in chapters]
    return chapters


def _add_linebreaks(text):
    """如果正文没有换行，按句末标点自动加上换行，让每句一行。"""
    if not text or "\n" in text:
        return text
    # 按句末标点拆分，保留标点
    parts = re.split(r'([。！？!?…；;])', text)
    lines = []
    cur = ""
    for i in range(0, len(parts) - 1, 2):
        cur += parts[i] + parts[i + 1]
        if len(cur) >= 80:  # 每 80 字左右换一行
            lines.append(cur.strip())
            cur = ""
    if cur:
        lines.append(cur.strip())
    return "\n".join(lines)


def fallback_split(text, para_target=5000):
    """文本没有章节标题时，按自然段落聚合成小节（约 5000 字一段）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text if text else "", ) if p.strip()]
    if not paras:
        paras = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    chapters = []
    cur, cur_len = [], 0
    for p in paras:
        if cur and cur_len + len(p) > para_target:
            chapters.append((f"第 {len(chapters) + 1} 节", "\n\n".join(cur)))
            cur, cur_len = [p], len(p)
        else:
            cur.append(p)
            cur_len += len(p)
    if cur:
        chapters.append((f"第 {len(chapters) + 1} 节", "\n\n".join(cur)))
    if not chapters:
        return [("正文", (text or "").strip())]
    return chapters
