# -*- coding: utf-8 -*-
"""epub 插图（轻小说/网文）解析测试：封面、整页彩插、行内小图、缓存与序列化。

样书在测试内自建（zip 内存拼装），不写入 sample/，避免仓库常脏。
"""
import io
import os
import sys
import tempfile
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from novelreader import book_loader

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _png(w=8, h=6, color=(200, 30, 30)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>插图样书</dc:title><dc:creator>测试作者</dc:creator>
  </metadata>
  <manifest>
    <item id="cover-img" href="Images/cover.png" media-type="image/png" properties="cover-image"/>
    <item id="cover-page" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="Text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="inline" href="Images/inline.png" media-type="image/png"/>
    <item id="vec" href="Images/vec.svg" media-type="image/svg+xml"/>
  </manifest>
  <spine>
    <itemref idref="cover-page"/>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""

COVER_PAGE = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div><img src="../Images/cover.png" alt="封面"/></div>
</body></html>
"""

CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>第一章 插图测试</h1>
<p>第一段文字，用来确认行内小图挂在段落之后。</p>
<p><img src="../Images/inline.png" alt="行内图"/></p>
<p>第二段文字，中间带一个外链插图<img src="https://example.com/a.jpg"/>与一个 svg
<svg xmlns="http://www.w3.org/2000/svg"><image xlink:href="../Images/vec.svg"/></svg>，
这些都不应产生本地文件。</p>
<p>第三段文字，重复引用同一张图，应只存一份。</p>
<p><img src="../Images/inline.png"/></p>
</body></html>
"""

CH2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>第二章 纯文字</h1>
<p>这一章没有任何插图，章节数不应因为插图而改变。</p>
<p>再补一段文字，确保长度足够进入正文。</p>
</body></html>
"""


def make_epub(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/Text/cover.xhtml", COVER_PAGE)
        zf.writestr("OEBPS/Text/ch1.xhtml", CH1)
        zf.writestr("OEBPS/Text/ch2.xhtml", CH2)
        zf.writestr("OEBPS/Images/cover.png", _png(120, 160, (20, 60, 160)))
        zf.writestr("OEBPS/Images/inline.png", _png(64, 48, (30, 160, 60)))
        zf.writestr("OEBPS/Images/vec.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")


def main():
    tmp = tempfile.mkdtemp(prefix="dd_epub_img_", dir=BASE)
    try:
        src = os.path.join(tmp, "sample_art.epub")
        make_epub(src)
        art = os.path.join(tmp, "art")

        print("[解析：不落盘]")
        plain = book_loader.parse_book(src)
        check("章节数 = 2（纯图封面页不新增章节）", len(plain.chapters) == 2, len(plain.chapters))
        check("不落盘模式无 art", all(not c.art for c in plain.chapters))
        check("不落盘模式不创建 art 目录", not os.path.isdir(art))

        print("[解析：落盘插图]")
        book = book_loader.parse_book(src, art_dir=art)
        check("章节数不变 = 2", len(book.chapters) == 2, len(book.chapters))
        ch1, ch2 = book.chapters[0], book.chapters[1]
        check("第一章有插图", len(ch1.art) > 0, ch1.art)
        names = [n for _a, n in ch1.art]
        check("封面挂到第一章章首", ch1.art[0][0] == 0, ch1.art[0])
        check("插图文件全部落盘", all(os.path.isfile(os.path.join(art, n)) for n in book.art_names()))
        check("同名插图去重（每图一个文件）", len(set(names)) == len(names), names)
        check("外链/svg 未落盘（文件数 = 2）", len(os.listdir(art)) == 2, os.listdir(art))
        check("锚点落在正文范围内", all(0 <= a <= len(ch1.content) for a, _n in ch1.art))
        check("正文保留文字且不含外链占位", "第一段文字" in ch1.content and "example.com" not in ch1.content)
        check("第二章无插图", not ch2.art)

        print("[缓存序列化]")
        d = book.to_dict()
        check("缓存版本已递增", d["v"] == book_loader.CONTENT_CACHE_VERSION, d["v"])
        check("章节 JSON 带 art", any("art" in c for c in d["chapters"]))
        back = book_loader.BookContent.from_dict(d)
        check("往返后章节数一致", len(back.chapters) == 2)
        check("往返后插图锚点/文件名一致", back.chapters[0].art == ch1.art, back.chapters[0].art)
        check("总字数计算不受插图影响", back.total_chars == sum(len(c.content) for c in back.chapters))
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
