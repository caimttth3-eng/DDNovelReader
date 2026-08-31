# -*- coding: utf-8 -*-
"""章节切分模块：把整本小说的纯文本按章节标题切分为章节列表。"""
import re

_NUM = r"[0-9０-９零〇○一二三四五六七八九十百千万两]+"
_UNIT = r"[章节回卷部集篇]"

# 严格匹配：行首就是第N章 / Chapter N / 楔子等
_HEAD_LINE = re.compile(
    r"^\s{0,4}(?:"
    rf"第{_NUM}{_UNIT}(?:\s*[：:、.\-—]?.*)?"
    r"|(?:[Cc]hapter|CHAPTER)\s+[0-9IVXLCivxl]+[：:.\s]?.*"
    r"|(?:楔子|序章|序言|前言|引子|引言|尾声|后记|番外|终章|大结局|完结篇)(?:\s*[：:、.\-—]?.*)?"
    r")\s*$"
)

# 宽松匹配：行尾是第N章（可能带书名前缀），用于匹配"书名 第1章"格式
_TAIL_CHAPTER = re.compile(
    r"(?:"
    rf"第{_NUM}{_UNIT}(?:\s*[：:、.\-—]?.*)?"
    r"|(?:[Cc]hapter|CHAPTER)\s+[0-9IVXLCivxl]+[：:.\s]?.*"
    r"|(?:楔子|序章|序言|前言|引子|引言|尾声|后记|番外|终章|大结局|完结篇)(?:\s*[：:、.\-—]?.*)?"
    r")\s*$"
)

# 分隔线：连续的破折号/等号/星号/波浪号（>=10个），常见于章节标题下方
_SEP_LINE = re.compile(r"^[\s\-=*~—–]{10,}$")

# 非标题标记：分隔线匹配时排除这些行
_NON_TITLE_MARKERS = ("http", "来源", "作者", "简介", "目录", "更新", "下载", "更多", "推荐", "收藏", "点击")


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


def split_chapters(text):
    """尝试按章节标题切分。成功返回 [(title, content), ...]；无法可靠切分返回 None。"""
    lines = text.split("\n")
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
        if body.strip():
            chapters.append((title, body))

    if len(chapters) < 2:
        # 标题集中在文首（如目录），正文没被切到 → 不可靠
        return None
    return chapters


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
