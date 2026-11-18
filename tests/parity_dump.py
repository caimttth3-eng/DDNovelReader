# -*- coding: utf-8 -*-
"""跨端缓存契约对拍（PC 侧）：解析同一本书，导出 key + 每句章内偏移 + 朗读文本，
并与安卓端导出的 JSON 逐项比对。

用法：
  .venv\\Scripts\\python.exe tests\\parity_dump.py [源文件] [书名] [安卓JSON] [前N章]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from novelreader.book_loader import parse_book                       # noqa: E402
from novelreader.content_key import (                                # noqa: E402
    chapters_text,
    compute_content_key,
    norm_text,
)
from novelreader.textproc import clean_to_orig                       # noqa: E402
from novelreader.tts_engine import split_sentences                    # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "sample", "星火.txt")
TITLE = sys.argv[2] if len(sys.argv) > 2 else ""
ANDROID = sys.argv[3] if len(sys.argv) > 3 else (
    r"C:\Users\lyuga\Doubao\chats\2026-09-01\new-chat\duoduo_langdu\build\parity_android.json"
)
LIMIT = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
TAIL_CHAPTER = int(sys.argv[5]) if len(sys.argv) > 5 else -1
OUT = os.path.join(ROOT, "build", "parity_pc.json")


def build_pc_dump():
    book = parse_book(SRC)
    title = TITLE or book.title
    full = chapters_text(book.chapters)
    key = compute_content_key(title, len(book.chapters), full)
    n = min(len(book.chapters), LIMIT)
    sentences = []
    for ci in range(n):
        ch = book.chapters[ci]
        clean_text, cmap = ch.tts_content()
        orig_len = len(ch.content)
        clean_off = 0
        for text in split_sentences(clean_text):
            if not text:
                continue
            start = clean_text.find(text, clean_off)
            if start < 0:
                start = clean_off
            off = clean_to_orig(cmap, start, orig_len)
            sentences.append({"ci": ci, "off": off, "tts": text})
            clean_off = start + len(text)
    return {
        "key": key,
        "title": title,
        "full_norm_len": len(norm_text(full)),
        "sample_norm": norm_text(full)[:2000],
        "chapter_count": len(book.chapters),
        "chapters_head": [c.title for c in book.chapters[:n]],
        "compare_chapters": n,
        "all_titles": [c.title for c in book.chapters],
        "chapter_para_counts": [
            len([x for x in c.content.split("\n") if x.strip()])
            for c in book.chapters[:n]
        ],
        "chapter_norm_lens": [len(norm_text(c.content)) for c in book.chapters],
        "tail_probe": (
            book.chapters[TAIL_CHAPTER].content
            if 0 <= TAIL_CHAPTER < len(book.chapters) else ""
        ),
        "sentences": sentences,
    }


def main():
    pc = build_pc_dump()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(pc, f, ensure_ascii=False)

    print("PC: key=%s title=%r chapters=%d sentences(first %d)=%d norm_len=%d"
          % (pc["key"], pc["title"], pc["chapter_count"], pc["compare_chapters"],
             len(pc["sentences"]), pc["full_norm_len"]))

    if not os.path.exists(ANDROID):
        print("安卓导出文件不存在：%s" % ANDROID)
        return
    with open(ANDROID, encoding="utf-8") as f:
        ad = json.load(f)
    print("AN: key=%s chapters=%d sentences=%d norm_len=%d"
          % (ad["key"], ad["chapter_count"], len(ad["sentences"]),
             ad["full_norm_len"]))

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    check("content_key 相同", pc["key"] == ad["key"], "%s vs %s" % (pc["key"], ad["key"]))
    a_sample = pc.get("sample_norm") or ""
    b_sample = ad.get("sample_norm") or ""
    check("开头 2000 字样本相同", a_sample == b_sample,
          "等长？%s；字数 %d vs %d" % (a_sample == b_sample, len(a_sample), len(b_sample)))
    if a_sample != b_sample:
        for i in range(min(len(a_sample), len(b_sample))):
            if a_sample[i] != b_sample[i]:
                print("  首个样本差异 @%d: PC=%r AN=%r" % (
                    i, a_sample[i:i + 30], b_sample[i:i + 30]))
                break
    check("全文 norm 字数相同（诊断项）", pc["full_norm_len"] == ad["full_norm_len"],
          "%d vs %d" % (pc["full_norm_len"], ad["full_norm_len"]))
    check("章节总数相同", pc["chapter_count"] == ad["chapter_count"],
          "%d vs %d" % (pc["chapter_count"], ad["chapter_count"]))
    check("前 N 章标题相同", pc["chapters_head"] == ad["chapters_head"],
          "%r vs %r" % (pc["chapters_head"][:3], ad["chapters_head"][:3]))
    check("句子数相同", len(pc["sentences"]) == len(ad["sentences"]),
          "%d vs %d" % (len(pc["sentences"]), len(ad["sentences"])))

    mism = []
    for i, (a, b) in enumerate(zip(pc["sentences"], ad["sentences"])):
        if a["ci"] != b["ci"] or a["off"] != b["off"] or a["tts"] != b["tts"]:
            mism.append((i, a, b))
    detail = ""
    if mism:
        detail = "共 %d 处；首个 #%d: PC=%r / AN=%r" % (
            len(mism), mism[0][0], mism[0][1], mism[0][2])
    check("句子坐标与文本逐项相同", not mism, detail)

    pc_lens = pc.get("chapter_norm_lens") or []
    an_lens = ad.get("chapter_norm_lens") or []
    diff = [(i, pc_lens[i], an_lens[i]) for i in range(min(len(pc_lens), len(an_lens)))
            if pc_lens[i] != an_lens[i]]
    check("逐章 norm 字数相同", not diff,
          "" if not diff else "共 %d 章不同；前3：%r" % (len(diff), diff[:3]))
    if diff:
        for i, a, b in diff[:5]:
            print("  章 %d: PC=%d AN=%d" % (i, a, b))
    # 全书标题逐项比对：定位首个分章分歧
    pc_t = pc.get("all_titles") or []
    an_t = ad.get("all_titles") or []
    first_bad = None
    for i in range(min(len(pc_t), len(an_t))):
        if pc_t[i] != an_t[i]:
            first_bad = i
            break
    check("全书标题逐项相同", first_bad is None and len(pc_t) == len(an_t),
          "首个分歧 #%s: PC=%r AN=%r" % (
              first_bad,
              pc_t[first_bad] if first_bad is not None else "",
              an_t[first_bad] if first_bad is not None else ""))
    if first_bad is not None:
        print("  PC 附近:", [repr(x) for x in pc_t[max(0, first_bad - 2):first_bad + 3]])
        print("  AN 附近:", [repr(x) for x in an_t[max(0, first_bad - 2):first_bad + 3]])

    if TAIL_CHAPTER >= 0:
        a = pc.get("tail_probe") or ""
        b = ad.get("tail_probe") or ""
        print("\n探针章 norm 长度: PC=%d AN=%d"
              % (len(norm_text(a)), len(norm_text(b))))
        na, nb = norm_text(a), norm_text(b)
        for i in range(min(len(na), len(nb))):
            if na[i] != nb[i]:
                print("  首个差异 @%d: PC=%r AN=%r" % (i, na[max(0, i - 20):i + 30],
                                                     nb[max(0, i - 20):i + 30]))
                break

    for name, ok, d in checks:
        print("[%s] %s %s" % ("OK" if ok else "FAIL", name, d))
    print("\n==== %d FAIL / %d ====" % (len([c for c in checks if not c[1]]), len(checks)))


if __name__ == "__main__":
    main()
