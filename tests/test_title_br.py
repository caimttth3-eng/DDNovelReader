# -*- coding: utf-8 -*-
"""章节标题被 <br> 拆行时不能丢后半截（第二章<br/><b>青牛镇</b>）。"""
import os
import sys
import tempfile
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from novelreader import book_loader

FAIL = []
TMP = tempfile.mkdtemp(prefix="novel_title_test_")


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def make_epub(path, heading_html, paras):
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>凡人</dc:title><dc:creator>忘语</dc:creator></metadata>'
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    body = "".join("<p>%s</p>" % p for p in paras)
    c1 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head>'
        '<body>%s%s</body></html>' % (heading_html, body)
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/c1.xhtml", c1)


def main():
    print("[title] EPUB：h2 + br + b 结构")
    p = os.path.join(TMP, "凡人.epub")
    make_epub(
        p, '<h2 class="head">第二章<br /><b>青牛镇</b></h2>',
        ["韩立跟着队伍走进青牛镇。", "街上人来人往，十分热闹。", "他在镇上住了下来。"],
    )
    c = book_loader.parse_book(p)
    check("EPUB 标题并入后半截", c.chapters[0].title == "第二章 青牛镇", repr(c.chapters[0].title))
    check("EPUB 正文不重复标题", c.chapters[0].content.startswith("韩立"), repr(c.chapters[0].content[:20]))

    print("[title] HTML 多章：h2 + br + b 结构")
    h = (
        '<html><body>'
        '<h2 class="head">第一章<br/><b>山边小村</b></h2>'
        + ("<p>%s</p>" % ("韩立蹲在村口。" * 30))
        + '<h2 class="head">第二章<br/><b>青牛镇</b></h2>'
        + ("<p>%s</p>" % ("队伍走进镇子。" * 30))
        + "</body></html>"
    )
    hp = os.path.join(TMP, "凡人.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(h)
    c2 = book_loader.parse_book(hp)
    titles = [ch.title for ch in c2.chapters]
    check("HTML 第一章标题完整", titles and titles[0] == "第一章 山边小村", repr(titles))
    check("HTML 第二章标题完整", len(titles) > 1 and titles[1] == "第二章 青牛镇", repr(titles))
    check("HTML 正文不含标题行", c2.chapters[0].content.startswith("韩立"),
          repr(c2.chapters[0].content[:20]))

    print("[title] 普通章节标题不受影响")
    txtp = os.path.join(TMP, "plain.txt")
    with open(txtp, "w", encoding="utf-8") as f:
        f.write("第一章 起点\n\n" + ("正文内容。" * 200) + "\n\n第二章 出发\n\n" + ("继续前进。" * 200))
    c3 = book_loader.parse_book(txtp)
    check("普通标题保持原样", [ch.title for ch in c3.chapters][:2] == ["第一章 起点", "第二章 出发"],
          repr([ch.title for ch in c3.chapters][:3]))

    print("\n%s（失败 %d 项）" % ("ALL PASS" if not FAIL else "FAILED", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
