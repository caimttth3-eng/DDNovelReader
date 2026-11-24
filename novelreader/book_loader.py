# -*- coding: utf-8 -*-
"""小说文件解析引擎：把 txt / epub / mobi / azw3 / pdf / docx / html 统一解析为 BookContent。"""
import codecs
import hashlib
import os
import re
import unicodedata
import zipfile
from html.parser import HTMLParser

from .chapterizer import split_chapters, fallback_split

# 惰性导入：docx / mobi 只在导入对应格式时加载。
# 两者合计约 320~400ms 的导入成本（mobi 406ms / docx 319ms 实测），而启动路径
# 绝大多数是 txt，没必要为它们付启动代价；PyInstaller 仍会静态收集这两个包。
_docx = None
_mobi = None


def _load_docx():
    global _docx
    if _docx is None:
        try:
            import docx as _m
        except Exception:
            return None
        _docx = _m
    return _docx


def _load_mobi():
    global _mobi
    if _mobi is None:
        try:
            import mobi as _m
        except Exception:
            return None
        _mobi = _m
    return _mobi

SUPPORTED_EXTS = {
    ".txt", ".epub", ".mobi", ".azw3", ".pdf", ".docx", ".html", ".htm", ".zip",
    ".chm",
}


class Chapter:
    __slots__ = ("title", "content", "art", "_tts_text", "_tts_map")

    def __init__(self, title, content, art=None):
        self.title = (title or "正文").strip()
        self.content = content or ""
        # 插图：[(content 中的字符锚点, "<sha1>.<ext>")]，文件落在 <缓存根>/art/
        # 只存相对文件名（不含绝对路径），换设备/换缓存目录后由本机重新解压生成
        self.art = list(art or [])
        self._tts_text = None
        self._tts_map = None

    def tts_content(self):
        """惰性生成 TTS 朗读用纯净文本与「纯净偏移→原文偏移」映射表。

        显示仍使用原始 content（保留标点/空白/颜文字）；
        朗读使用清理后的纯净文本，避免连续标点/空白/颜文字导致 TTS 静音卡顿。
        """
        if self._tts_text is None:
            from .textproc import preprocess_for_tts

            self._tts_text, self._tts_map = preprocess_for_tts(self.content)
        return self._tts_text, self._tts_map

    def to_dict(self):
        d = {"title": self.title, "content": self.content}
        if self.art:
            d["art"] = [[int(a), str(n)] for a, n in self.art]
        return d

    @staticmethod
    def from_dict(d):
        art = []
        for item in d.get("art") or []:
            try:
                art.append((int(item[0]), str(item[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return Chapter(d.get("title", "正文"), d.get("content", ""), art)


class BookContent:
    def __init__(self, title, author, fmt, chapters):
        self.title = title or "未命名"
        self.author = author or ""
        self.format = fmt or ""
        self.chapters = chapters or []
        # 每章之前的累计字符数，用于 O(1) 计算总进度
        self.cum = [0]
        for c in self.chapters:
            self.cum.append(self.cum[-1] + len(c.content))
        self.total_chars = self.cum[-1]

    def art_names(self):
        """全书插图文件名的去重列表（用于检查本地插图缓存是否完整）。"""
        names = []
        for c in self.chapters:
            for _, n in getattr(c, "art", ()):
                if n not in names:
                    names.append(n)
        return names

    def to_dict(self):
        return {
            "v": CONTENT_CACHE_VERSION,
            "title": self.title,
            "author": self.author,
            "format": self.format,
            "chapters": [c.to_dict() for c in self.chapters],
        }

    @staticmethod
    def from_dict(d):
        book = BookContent(
            d.get("title", "未命名"),
            d.get("author", ""),
            d.get("format", ""),
            [Chapter.from_dict(c) for c in d.get("chapters", [])],
        )
        return book


# ---------------- 文本处理 ----------------

def _text_quality(s: str) -> float:
    """解码质量打分：CJK 占比高、无替换符/控制字符的候选更可信。

    GBK 字节流经常"碰巧"是合法 UTF-8（解码成功但全是乱码拉丁字符），
    单靠 try/except 无法区分，必须按解码结果的质量选。
    """
    if not s:
        return -1.0
    total = max(len(s), 1)
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    repl = s.count("\ufffd")
    ctrl = sum(
        1 for ch in s if unicodedata.category(ch) == "Cc" and ch not in "\n\r\t"
    )
    return cjk / total * 2.0 - repl / total * 5.0 - ctrl / total * 3.0


def _decode(raw: bytes) -> str:
    """多编码探测解码，保证中文 txt 正确读取。

    按解码质量打分选择（UTF-8 优先，但 GBK 误判为 UTF-8 时能纠正），
    避免网文 GBK 文本被错误解码成乱码。
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig")
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16")
    best = None
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        score = _text_quality(s)
        if best is None or score > best[0]:
            best = (score, s)
    if best is not None and best[0] >= 0.6:
        return best[1]
    try:
        from charset_normalizer import from_bytes
        b = from_bytes(raw).best()
        if b is not None and str(b):
            return str(b)
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


CONTENT_CACHE_VERSION = 9  # epub 插图（art）落盘，强制重解析一次  # 缓存格式版本，改动解析结果后需递增以强制重解析


def normalize_body(text: str) -> str:
    """规范化正文：统一换行、折叠多余空行、段落间以单个换行分隔（不保留空行）。

    空行已移除，段间距完全由阅读区的行距设置控制，避免"每段隔一整行"。
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 网文常见的“全角空格分段”格式：段落之间没有换行，只用 　　 分隔。
    # 若不转换，整章会被连成一段，导致“从该段朗读/高亮跟随”无法定位段落。
    # 先转换双全角空格段首为换行，再处理其余单全角空格（缩进）。
    text = text.replace("\u3000\u3000", "\n")
    text = text.replace("\u3000", "  ")
    text = text.replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)  # 行尾空格
    text = re.sub(r"\n{2,}", "\n", text)  # 折叠空行：段落间只留一个换行
    text = text.strip("\n")
    if "\n" in text:
        paras = [p.strip("\n").strip() for p in text.split("\n")]
        paras = [p for p in paras if p]
        return "\n".join(paras)
    lines = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def _make_chapters(text: str) -> list:
    """对整段文本执行章节切分，返回 Chapter 列表。"""
    spl = split_chapters(text)
    if spl:
        return [Chapter(t, normalize_body(b)) for t, b in spl]
    fb = fallback_split(text)
    return [Chapter(t, normalize_body(b)) for t, b in fb]


# ---------------- HTML 提取 ----------------

class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "blockquote", "section", "article", "tr", "pre",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip = 0
        self._head = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._head += 1
        if tag in self.BLOCK_TAGS or tag == "br":
            # 标题里的 <br> 只是标题内换行（如「第二章<br/>青牛镇」），压成空格，
            # 否则按行取标题会丢掉后半截。
            if tag == "br" and self._head > 0:
                self._parts.append(" ")
            else:
                self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._head = max(0, self._head - 1)
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip == 0 and data:
            self._parts.append(data)

    def text(self):
        s = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", s)


def _extract_html(raw) -> str:
    if isinstance(raw, bytes):
        raw = _decode(raw)
    p = _TextExtractor()
    try:
        p.feed(raw)
    except Exception:
        pass
    return p.text()


# 标题里的 <br> 在纯文本里会变成换行，按「第一行」取标题就会丢掉后半截
# （<h2 class="head">第二章<br/><b>青牛镇</b></h2> → 只得到「第二章」）。
# 这里直接在 HTML 层把整个标题元素压成一行的标题文本。
_HTML_HEAD_RE = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1\s*>", re.S | re.I)


def _html_heading_text(raw) -> str:
    """取页面第一个 h1~h6 的完整标题（<br> 视作空格，保留 <b> 等内联文字）。"""
    if isinstance(raw, bytes):
        raw = _decode(raw)
    if not raw:
        return ""
    m = _HTML_HEAD_RE.search(raw)
    if not m:
        return ""
    inner = m.group(2)
    inner = re.sub(r"<br\s*/?>", " ", inner, flags=re.I)
    inner = re.sub(r"<[^>]+>", "", inner)
    import html as _html_mod
    inner = _html_mod.unescape(inner)
    inner = re.sub(r"\s+", " ", inner).replace("\u3000", " ").strip()
    return inner[:80]


def _strip_heading_from_body(body: str, title: str) -> str:
    """从正文开头去掉与标题重复的行（标题两行被 <br> 拆开时逐行合并比对）。"""
    if not body or not title:
        return body
    key = re.sub(r"\s+", "", title)
    if not key:
        return body
    lines = body.split("\n")
    acc = ""
    i = 0
    while i < len(lines) and i < 3:
        acc += re.sub(r"\s+", "", lines[i])
        if acc == key:
            return "\n".join(lines[i + 1:]).lstrip("\n")
        if not key.startswith(acc):
            break
        i += 1
    return body


# ---------------- epub 插图提取 ----------------
# 正文里用私有区字符包住图片序号做占位标记，规范化后再按标记把插图挂到段落锚点。
_IMG_TOKEN_PREFIX = "\ue000"
_IMG_TOKEN_SUFFIX = "\ue001"
_IMG_TOKEN_RE = re.compile("\ue000(\\d+)\ue001")

_ART_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_ART_MAX_BYTES = 24 * 1024 * 1024


def _img_token(idx):
    return "%s%d%s" % (_IMG_TOKEN_PREFIX, idx, _IMG_TOKEN_SUFFIX)


class _ArtExtractor(HTMLParser):
    """在纯文本提取的同时，记录每个 <img>/<svg><image> 的位置与 src。

    与 _TextExtractor 保持同样的块级换行规则，保证正文文本与无图时的解析结果一致。
    """

    BLOCK_TAGS = _TextExtractor.BLOCK_TAGS

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip = 0
        self._head = 0
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._head += 1
        if tag in ("img", "image"):
            if self._skip == 0:
                src = ""
                alt_href = ""
                for k, v in attrs:
                    if not v:
                        continue
                    k = (k or "").lower()
                    if k == "src":
                        src = v.strip()
                    elif k in ("xlink:href", "href") and not alt_href:
                        alt_href = v.strip()
                src = src or alt_href
                if src:
                    self.images.append(src)
                    self._parts.append(_img_token(len(self.images) - 1))
            return
        if tag in self.BLOCK_TAGS or tag == "br":
            if tag == "br" and self._head > 0:
                self._parts.append(" ")
            else:
                self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._head = max(0, self._head - 1)
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip == 0 and data:
            self._parts.append(data)

    def text(self):
        s = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", s)


def _extract_page_with_art(raw):
    """返回 (带图片标记的原始文本, [图片 src])。"""
    if isinstance(raw, bytes):
        raw = _decode(raw)
    p = _ArtExtractor()
    try:
        p.feed(raw)
    except Exception:
        pass
    return p.text(), p.images


def _art_ext(href, media_type, data):
    """由扩展名/媒体类型/文件头推断图片扩展名；svg 等不可栅格化的返回 None。"""
    mt = (media_type or "").lower()
    if "svg" in mt:
        return None
    low = (href or "").lower().split("?")[0].split("#")[0]
    for e in _ART_EXTS:
        if low.endswith(e):
            return ".jpg" if e == ".jpeg" else e
    m = re.search(r"image/([a-z0-9+.-]+)", mt)
    if m:
        sub = m.group(1)
        if sub == "jpeg":
            return ".jpg"
        if sub in ("png", "gif", "webp", "bmp"):
            return "." + sub
    if data:
        if data[:3] == b"\xff\xd8\xff":
            return ".jpg"
        if data[:4] == b"\x89PNG":
            return ".png"
        if data[:4] in (b"GIF8",):
            return ".gif"
        if data[:2] == b"BM":
            return ".bmp"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
    return None


def _store_art(art_dir, data, href, media_type=""):
    """把图片字节写入 art 目录，按 sha1 去重，返回相对文件名（失败返回 None）。

    art_dir 为 None（如分章诊断/指纹计算等只读场景）时直接跳过，不产生任何写盘。
    """
    if not art_dir or not data or len(data) > _ART_MAX_BYTES:
        return None
    ext = _art_ext(href, media_type, data)
    if not ext:
        return None
    name = hashlib.sha1(data).hexdigest() + ext
    target = os.path.join(art_dir, name)
    if not os.path.exists(target):
        try:
            os.makedirs(art_dir, exist_ok=True)
            tmp = target + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return None
    return name


def _split_text_and_art(norm_text):
    """把带图片标记的规范化正文拆成 (正文, [(锚点偏移, 图片序号)])。

    图片一律挂到所在段落之后（图片独占一行的则挂在上一段之后），
    这样不改变章节数量与章节索引，PC/Android 的进度、书签完全不受影响。
    """
    lines = norm_text.split("\n")
    out = []
    marks = []
    for line in lines:
        idxs = [int(x) for x in _IMG_TOKEN_RE.findall(line)]
        if not idxs:
            if line:
                out.append(line)
            continue
        clean = _IMG_TOKEN_RE.sub("", line).strip()
        if clean:
            out.append(clean)
        for i in idxs:
            marks.append((len(out), i))
    body = "\n".join(out)
    starts = [0]
    for p in out:
        starts.append(starts[-1] + len(p) + 1)
    anchors = []
    for para_count, i in marks:
        off = starts[para_count] if para_count < len(starts) else len(body) + 1
        anchors.append((min(off, len(body)), i))
    return body, anchors


_DOC_WRITE_RE = re.compile(
    r"""document\.write\s*\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""",
    re.I,
)


def _chm_js_html_to_text(raw: bytes):
    """把 CHM 里 JS 包裹的 HTML 页转成 (标题, 正文纯文本)。

    很多网文合订本 CHM 的"txt 页"实际是多个 document.write("...") 拼接的
    HTML（用于页面动态渲染），需要先剥 JS 壳、解转义，再走 HTML 提取；
    顺带从 <b> / <h1-6> / <a name> 里抠出章节标题。
    """
    s = _decode(raw)
    segs = []
    for m in _DOC_WRITE_RE.finditer(s):
        seg = m.group(1) if m.group(1) is not None else m.group(2)
        seg = seg.replace(r"\"", '"').replace(r"\'", "'").replace(r"\\", "\\")
        seg = seg.replace(r"\n", "\n").replace(r"\t", "\t")
        segs.append(seg)
    htm = "\n".join(segs) if segs else s

    title = _html_heading_text(htm)
    if not title:
        m = re.search(r"<b[^>]*>(.*?)</b>", htm, re.S | re.I)
        if not m:
            m = re.search(r"<a[^>]*name=[\"']?([^\"'>\s]+)", htm, re.I)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1))
            t = t.replace("&nbsp;", " ").strip(" \u3000")
            if t:
                title = t
    text = _extract_html(htm)
    if title:
        text = _strip_heading_from_body(text, title)
    return title, text


# ---------------- 各格式解析 ----------------

def _guess_chapter_title(text: str, max_len=24):
    """从正文开头猜标题；标题被 <br> 拆成两行时把后半截并回来（第二章 + 青牛镇）。"""
    lines = []
    for line in text.split("\n"):
        line = line.strip(" \t\u3000")
        if line:
            lines.append(line)
    if not lines:
        return None
    first = lines[0]
    title = first
    if re.match(r"^(第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[章节回卷部篇]|Chapter\s+\d+|楔子|序章|引子)", first, re.I) \
            and len(first) <= max_len:
        for nxt in lines[1:3]:
            if len(nxt) > max_len:
                break
            if re.search(r"[，。！？；：、,\.!?;:]$", nxt):
                break
            if re.match(r"^(第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[章节回卷部篇]|Chapter\s+\d+)", nxt, re.I):
                break
            if len(first) + 1 + len(nxt) <= max_len:
                title = first + " " + nxt
            break
    return title[:max_len]


def _parse_txt(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = _decode(raw)
    title = os.path.splitext(os.path.basename(path))[0]
    return BookContent(title, "", "txt", _make_chapters(text))


def _epub_cover_item(opf, manifest):
    """找封面：EPUB3 properties="cover-image" → EPUB2 <meta name="cover"> → guide。"""
    for href, mt, props in manifest.values():
        if "cover-image" in (props or "").lower():
            return href, mt
    for pat in (
        r'<meta[^>]*name=["\']cover["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']cover["\']',
    ):
        m = re.search(pat, opf, re.I)
        if m and m.group(1) in manifest:
            href, mt, _ = manifest[m.group(1)]
            return href, mt
    m = re.search(
        r'<reference[^>]*type=["\']cover["\'][^>]*href=["\']([^"\']+)["\']', opf, re.I
    )
    if m:
        return m.group(1), ""
    return None, ""


def _parse_epub(path, art_dir=None):
    import urllib.parse

    zf = zipfile.ZipFile(path)
    try:
        names = zf.namelist()
        # zip 内路径大小写敏感，HTML 里写的路径常与实际 entry 大小写不一致 → 小写兜底表
        lower_map = {}
        for n in names:
            lower_map.setdefault(n.replace("\\", "/").lower(), n)
        container = next((n for n in names if n.endswith("META-INF/container.xml")), None)
        if not container:
            raise ValueError("epub 缺少 container.xml，文件可能损坏")
        cdata = zf.read(container).decode("utf-8", "replace")
        m = re.search(r'full-path="([^"]+)"', cdata)
        if not m:
            raise ValueError("epub 的 container.xml 无法解析")
        opf_path = m.group(1).replace("\\", "/")
        opf = zf.read(opf_path).decode("utf-8", "replace")

        title = _first_meta(opf, "dc:title") or os.path.splitext(os.path.basename(path))[0]
        author = _first_meta(opf, "dc:creator") or ""
        author = re.sub(r"<[^>]+>", "", author)

        manifest = {}
        for mm in re.finditer(r"<item\s+([^>]*?)/?>", opf):
            attrs = _tag_attrs(mm.group(1))
            if attrs.get("id"):
                manifest[attrs["id"]] = (
                    attrs.get("href", ""),
                    attrs.get("media-type", ""),
                    attrs.get("properties", ""),
                )

        base = os.path.dirname(opf_path)

        def resolve(href):
            href = href.split("#")[0]
            return os.path.normpath(os.path.join(base, href)).replace("\\", "/")

        # 清单内文件路径 → 媒体类型（插图写盘时用它判断扩展名）
        mt_by_path = {}
        for href, mt, _props in manifest.values():
            if href:
                mt_by_path[resolve(href)] = mt

        def zip_read(zpath):
            """按路径读 entry：先精确匹配，失败再按小写匹配（zip 内路径大小写敏感）。"""
            zpath = (zpath or "").replace("\\", "/").lstrip("/")
            if not zpath:
                return None, None
            real = lower_map.get(zpath) or lower_map.get(zpath.lower())
            if real is None:
                return None, None
            try:
                return zf.read(real), real
            except KeyError:
                return None, None

        def page_images(srcs, page_path):
            """取出一页里的本地插图：写盘并返回与 srcs 等长的文件名列表。

            外链（http/https/data）与未落盘的插图填 None，保持下标与正文标记一一对应。
            """
            out = []
            page_dir = os.path.dirname(page_path)
            for src in srcs:
                name = None
                if src:
                    low = src.lower()
                    if not (
                        low.startswith("data:")
                        or low.startswith("//")
                        or re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I)
                    ):
                        s = urllib.parse.unquote(src.split("#")[0].split("?")[0])
                        full = os.path.normpath(os.path.join(page_dir, s)).replace("\\", "/")
                        while full.startswith("../"):
                            full = full[3:]
                        data, real = zip_read(full)
                        if data is not None:
                            name = _store_art(
                                art_dir, data, real or full, mt_by_path.get(real or full, "")
                            )
                out.append(name)
            return out


        spine = []
        for sm in re.finditer(r"<itemref\s+([^>]*?)/?>", opf):
            attrs = _tag_attrs(sm.group(1))
            idref = attrs.get("idref")
            if idref and idref in manifest:
                href, mt, _props = manifest[idref]
                if "html" in mt.lower() or href.lower().endswith((".html", ".xhtml", ".htm")):
                    spine.append(resolve(href))

        # 封面：优先独立 cover-image；封面页是 xhtml 时取其首图
        cover_name = None
        chref, cmt = _epub_cover_item(opf, manifest)
        if chref:
            cpath = resolve(chref)
            low = chref.lower()
            if "html" in cmt.lower() or low.endswith((".html", ".xhtml", ".htm")):
                craw, creal = zip_read(cpath)
                if craw is not None:
                    _ctxt, csrcs = _extract_page_with_art(craw)
                    imgs = [n for n in page_images(csrcs, creal or cpath) if n]
                    cover_name = imgs[0] if imgs else None
            else:
                cdata_img, creal = zip_read(cpath)
                if cdata_img is not None:
                    cover_name = _store_art(
                        art_dir, cdata_img, creal or cpath, mt_by_path.get(creal or cpath, cmt)
                    )

        chapters = []
        seen = set()
        pending = []  # 只有图片、没有正文的页面（封面/彩插）→ 挂到下一章开头
        for full in spine:
            if full in seen:
                continue
            seen.add(full)
            raw, real = zip_read(full)
            if raw is None:
                continue
            page_path = real or full
            page_txt, srcs = _extract_page_with_art(raw)
            norm = normalize_body(page_txt)
            body, anchors = _split_text_and_art(norm)
            heading = _html_heading_text(raw)
            if heading:
                stripped = _strip_heading_from_body(body, heading)
                if stripped != body:
                    delta = len(body) - len(stripped)  # 去掉的前缀长度：插图锚点跟着前移
                    anchors = [(max(0, off - delta), i) for off, i in anchors]
                    body = stripped
            imgs = page_images(srcs, page_path)
            if len(body) < 20:
                # 纯图片页（封面/整页彩插）：不新建章节，图片顺延到下一章章首
                pending.extend([n for n in imgs if n])
                continue
            art = []
            used = set()
            for off, idx in anchors:
                if idx >= len(imgs):
                    continue
                name = imgs[idx]
                if not name or name in used:
                    continue
                used.add(name)
                art.append((off, name))
            if pending:
                art = [(0, n) for n in pending] + art
                pending = []
            chapters.append(
                Chapter(heading or _guess_chapter_title(body) or os.path.basename(full), body, art)
            )

        if pending and chapters:
            last = chapters[-1]
            last.art = list(last.art) + [(len(last.content), n) for n in pending]
            pending = []
        if cover_name and chapters:
            first = chapters[0]
            existing = {n for _a, n in first.art}
            if cover_name not in existing:
                first.art = [(0, cover_name)] + list(first.art)

        if not chapters:
            raise ValueError("epub 未提取到正文内容")
        chapters = _finalize(chapters, "epub")
        return BookContent(title, author, "epub", chapters)
    finally:
        zf.close()



def _parse_mobi(path):
    import glob
    import shutil
    import tempfile

    mobi_mod = _load_mobi()
    if mobi_mod is None:
        raise ValueError("未安装 mobi 解析库，无法解析 mobi/azw3")
    tmp = tempfile.mkdtemp(prefix="novel_reader_mobi_")
    extracted = None
    try:
        title, extracted = mobi_mod.extract(path)
        html_files = []
        if os.path.isdir(extracted):
            html_files = sorted(glob.glob(os.path.join(extracted, "**", "*.htm*"), recursive=True))
        elif os.path.isfile(extracted):
            html_files = [extracted]
        if not html_files:
            raise ValueError("mobi/azw3 中未找到正文文件")
        chapters = []
        for hf in html_files:
            with open(hf, "rb") as f:
                raw = f.read()
            text = normalize_body(_extract_html(raw))
            if len(text) < 20:
                continue
            chapters.append(Chapter(_guess_chapter_title(text) or os.path.basename(hf), text))
        if not chapters:
            raise ValueError("mobi/azw3 未提取到正文内容")
        chapters = _finalize(chapters, "mobi")
        return BookContent(
            title or os.path.splitext(os.path.basename(path))[0], "", "mobi", chapters
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if extracted and os.path.isdir(extracted):
            try:
                shutil.rmtree(extracted, ignore_errors=True)
            except Exception:
                pass


def _parse_pdf(path):
    """用 pypdfium2（PDFium，Chrome 同款引擎）解析 PDF 文本。

    比 PyMuPDF 轻量约 10MB+，速度接近，Apache 协议更宽松。
    """
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
    finally:
        pdf.close()
    text = "\n".join(pages)
    title = os.path.splitext(os.path.basename(path))[0]
    return BookContent(title, "", "pdf", _make_chapters(text))


def _parse_docx(path):
    docx_mod = _load_docx()
    if docx_mod is None:
        raise ValueError("未安装 python-docx，无法解析 docx")
    d = docx_mod.Document(path)
    lines = [p.text for p in d.paragraphs if p.text and p.text.strip()]
    text = "\n".join(lines)
    try:
        title = d.core_properties.title or os.path.splitext(os.path.basename(path))[0]
        author = d.core_properties.author or ""
    except Exception:
        title = os.path.splitext(os.path.basename(path))[0]
        author = ""
    return BookContent(title, author, "docx", _make_chapters(text))


def _parse_html(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = _extract_html(raw)
    title = os.path.splitext(os.path.basename(path))[0]
    return BookContent(title, "", "html", _make_chapters(text))


def _natural_key(name):
    """把 '1.html'、'2.html'、'10.html' 按数字自然排序。"""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def _parse_chm(path):
    """解析 CHM 帮助文档/电子书：内置 chmlib 解包，正文优先取 .txt 章节页（网文合订本常见），其次 .html 页面。"""
    from .chm_reader import CHMReader, CHMError

    try:
        reader = CHMReader(path)
    except CHMError as e:
        raise ValueError(str(e))
    try:
        files = reader.list_files()

        def _candidates(ext):
            return [p for p in files
                    if p.lower().endswith(ext) and not p.startswith(("/#", "/$", "/::"))
                    and not p.endswith("/")]

        txt_pages = _candidates(".txt")
        html_pages = _candidates((".html", ".htm"))
        # 优先 txt（章节正文通常按 1_001.txt / 2_001.txt 命名），没有才用 html
        if txt_pages:
            raw_pages = txt_pages
            use_txt = True
        else:
            raw_pages = html_pages
            use_txt = False

        pages = []
        for p in raw_pages:
            try:
                raw = reader.read_file(p)
            except CHMError:
                continue
            if not raw:
                continue
            if use_txt:
                title, text = _chm_js_html_to_text(raw)
            else:
                title, text = "", _extract_html(raw)
            if not text or len(text) < 20:  # 过滤导航页/空页
                continue
            if not title or len(title) < 2 or title.isdigit():
                base = os.path.splitext(os.path.basename(p))[0].strip()
                title = _guess_chapter_title(text) or base
            pages.append((_natural_key(p), title, text))

        if not pages:
            raise ValueError("CHM 内未提取到正文页面")
        # 按路径自然排序，保证章节顺序（001.html → 002.html → 010.html）
        pages.sort(key=lambda x: x[0])
        chapters = [Chapter(t, normalize_body(b)) for _, t, b in pages]

        # 单页超大文本：尝试内部再切分（整本书塞进一个文件的情况）
        if len(chapters) == 1 and len(chapters[0].content) > 40000:
            spl = split_chapters(chapters[0].content)
            if spl and len(spl) >= 2:
                chapters = [Chapter(t, normalize_body(b)) for t, b in spl]
        title = os.path.splitext(os.path.basename(path))[0]
        return BookContent(title, "", "chm", chapters)
    finally:
        reader.close()


def _parse_zip(path, art_dir=None):
    """解析 zip 压缩包：自动查找其中的 txt / epub 小说文件。"""
    import tempfile
    import shutil

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("zip 文件损坏或不是有效的压缩包")
    except RuntimeError:
        raise ValueError("加密的 zip 暂不支持，请先解压")

    try:
        names = zf.namelist()
        txt_files = [n for n in names
                     if n.lower().endswith(".txt") and not n.endswith("/")
                     and "__MACOSX" not in n]
        epub_files = [n for n in names
                      if n.lower().endswith(".epub") and not n.endswith("/")
                      and "__MACOSX" not in n]

        if not txt_files and not epub_files:
            raise ValueError("压缩包内未找到 txt 或 epub 小说文件")

        if epub_files:
            target = epub_files[0]
            tmp = tempfile.mkdtemp(prefix="ddnovel_zip_")
            try:
                epub_path = os.path.join(tmp, os.path.basename(target))
                with zf.open(target) as src, open(epub_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                book = _parse_epub(epub_path, art_dir=art_dir)
                book.format = "zip"
                return book
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        target = max(txt_files, key=lambda n: zf.getinfo(n).file_size)
        raw = zf.read(target)
        text = _decode(raw)
        title = os.path.splitext(os.path.basename(path))[0]
        return BookContent(title, "", "zip", _make_chapters(text))
    finally:
        zf.close()


# ---------------- 辅助 ----------------

def _tag_attrs(s):
    return dict(re.findall(r'([\w:.-]+)="([^"]*)"', s))


def _first_meta(opf, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", opf, re.S | re.I)
    return m.group(1).strip() if m else ""


def _finalize(chapters, fmt):
    # 带插图的章节即使正文很短也保留（纯图页已并入相邻章，此处只做兜底）
    chapters = [c for c in chapters if len(c.content) >= 20 or getattr(c, "art", None)]
    if not chapters:
        raise ValueError("未能提取到正文")
    # 单章超大文本：再尝试内部切分（常见于整本书塞进一个 html 的情况）
    if len(chapters) == 1 and len(chapters[0].content) > 40000:
        spl = split_chapters(chapters[0].content)
        if spl and len(spl) >= 2:
            chapters = [Chapter(t, normalize_body(b)) for t, b in spl]
    return chapters


_PARSERS = {
    ".txt": _parse_txt,
    ".epub": _parse_epub,
    ".mobi": _parse_mobi,
    ".azw3": _parse_mobi,
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".html": _parse_html,
    ".htm": _parse_html,
    ".zip": _parse_zip,
    ".chm": _parse_chm,
}


def parse_book(path, art_dir=None):
    """解析文件为 BookContent。path 必须存在。

    art_dir 非 None 时才把 epub 插图落盘到该目录（阅读器/导入用缓存根下的 art/）；
    默认 None 表示只解析正文，不产生任何写盘（指纹计算、后台同步等只读场景）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in _PARSERS:
        raise ValueError(f"暂不支持该格式：{ext or '未知'}")
    parser = _PARSERS[ext]
    if ext in (".epub", ".zip"):
        return parser(path, art_dir=art_dir)
    return parser(path)
