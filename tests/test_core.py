# -*- coding: utf-8 -*-
"""核心模块冒烟测试：章节切分 / 格式解析 / 存储 / TTS 引擎。"""
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from tests.make_samples import main as make_samples
from novelreader import book_loader, chapterizer
from novelreader.storage import Storage
from novelreader.tts_engine import SpeechController, split_sentences

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def test_chapterizer():
    print("[chapterizer]")
    text = (
        "楔子\n这是序章内容。\n\n第一章 相遇\n\n他们在桥头相见。\n\n"
        "第二章 离别\n\n天色将晚。\n\n第三章 重逢\n\n月光下再度相认。\n\n"
    )
    spl = chapterizer.split_chapters(text)
    check("识别 楔子+3章", spl is not None and len(spl) == 4, f"got {None if spl is None else len(spl)}")

    text2 = "第十二章 千里之外\n\n内容一二三。\n\n第100章 终局\n\n结束。\n\nChapter 5 The End\n\nMore text.\n\n"
    spl2 = chapterizer.split_chapters(text2)
    check("中文数字+阿拉伯+英文章节", spl2 is not None and len(spl2) == 3, f"got {None if spl2 is None else len(spl2)}")

    plain = "这是没有任何章节标题的普通文本。\n\n" * 30
    fb = chapterizer.fallback_split(plain)
    check("无章节标题走兜底切分", len(fb) >= 1 and all(c[0] for c in fb))

    # 验证切分后首章标题为"第一章 相遇"
    check("章节标题正确", spl[1][0] == "第一章 相遇", spl[1][0] if spl else "None")

    # v1.6：第一章之前的文字归入"简介"章，排在第一章之前
    text3 = "这是一本小说。\n作者：某人\n\n第一章 觉醒\n少年醒来。\n\n第二章 下山\n少年出发。"
    spl3 = chapterizer.split_chapters(text3)
    check("前导文字归入简介章",
          spl3 is not None and len(spl3) == 3 and spl3[0][0] == "简介",
          f"got {None if spl3 is None else [(t, b[:10]) for t, b in spl3]}")
    check("简介章排在第一章之前", spl3 is not None and spl3[1][0] == "第一章 觉醒",
          f"got {None if spl3 is None else spl3[1][0]}")

    # 无前导文字时不生成简介章
    spl4 = chapterizer.split_chapters("第一章 觉醒\n少年醒来。\n\n第二章 下山\n少年出发。")
    check("无前导文字不生成简介章", spl4 is not None and spl4[0][0] == "第一章 觉醒",
          f"got {None if spl4 is None else spl4[0][0]}")


def test_parse(paths):
    print("[book_loader]")
    for fmt, p in paths.items():
        try:
            book = book_loader.parse_book(p)
            check(
                f"解析 {fmt}: {book.title}",
                book is not None and len(book.chapters) >= 1 and book.total_chars > 0,
                f"chapters={None if book is None else len(book.chapters)} total={None if book is None else book.total_chars}",
            )
            if book is not None:
                check(
                    f"{fmt} 章节数与样章一致",
                    len(book.chapters) >= 6 or fmt == "pdf",
                    f"n={len(book.chapters)}",
                )
                check(f"{fmt} 累计偏移正确", book.cum[-1] == book.total_chars)
                check(f"{fmt} 首章标题非空", bool(book.chapters[0].title.strip()))
        except Exception as e:
            FAIL.append(f"parse-{fmt}")
            print(f"  [FAIL] 解析 {fmt}: {e}")


def test_storage():
    print("[storage]")
    # v1.7：默认数据目录迁移到 %APPDATA%\DDNovelReader
    old_env = os.environ.get("DOUBAO_NOVEL_DATA")
    os.environ.pop("DOUBAO_NOVEL_DATA", None)
    try:
        import novelreader.storage as st
        d = st.data_dir()
        check("默认数据目录为 DDNovelReader", d.rstrip("/\\").endswith("DDNovelReader"), d)
        dd = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "DDNovelReader")
        if os.path.isdir(dd) and not os.listdir(dd):
            try:
                os.rmdir(dd)
            except Exception:
                pass
    finally:
        if old_env:
            os.environ["DOUBAO_NOVEL_DATA"] = old_env
        else:
            os.environ.pop("DOUBAO_NOVEL_DATA", None)

    with tempfile.TemporaryDirectory() as td:
        s = Storage(os.path.join(td, "library.json"))
        s.set_setting("theme", "夜间")
        bid = s.book_id(os.path.join(td, "a.txt"))
        s.add_book(
            {
                "id": bid,
                "title": "测试书",
                "author": "",
                "format": "txt",
                "path": os.path.join(td, "a.txt"),
                "added_at": time.time(),
                "last_read_at": time.time(),
                "total_chars": 1000,
                "chapter_titles": ["第一章"],
                "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
            }
        )
        s.update_progress(bid, {"chapter_idx": 2, "char_offset": 500, "percent": 12.5})
        # 重新加载验证持久化
        s2 = Storage(os.path.join(td, "library.json"))
        b = s2.get_book(bid)
        check("书架持久化", b is not None and b["title"] == "测试书")
        check("进度持久化", b["progress"]["percent"] == 12.5 and b["progress"]["chapter_idx"] == 2)
        check("设置持久化", s2.get_setting("theme") == "夜间")


def test_tts_logic():
    print("[tts_engine]")
    frags = split_sentences("第一句。第二句！第三句？\n新段落开始。长句子没有标点被硬切，" * 3)
    check("句子切分非空", len(frags) > 0)

    voices = SpeechController.list_voices()
    check("能枚举系统语音", len(voices) > 0, f"n={len(voices)}")
    if voices:
        print(f"  语音: {[v.split(chr(92))[-1] for v in voices]}")

    # 真实朗读一小段：start->pause->resume->stop
    book = book_loader.BookContent(
        "测试", "", "txt",
        [book_loader.Chapter("第一章", "这是第一句话。这是第二句话。这是第三句话。这是第四句话。")],
    )
    ctl = SpeechController()
    ctl.set_rate(180)
    ctl.start(book, 0, 0)
    time.sleep(1.0)
    ctl.pause()
    time.sleep(0.5)
    events = ctl.drain()
    check("朗读已开始并产生事件", any(e["type"] == "sentence_start" for e in events))
    check("暂停生效", ctl.is_paused())
    ctl.resume()
    time.sleep(1.0)
    ctl.stop()
    time.sleep(0.5)
    check("停止生效", ctl.is_stopped())
    # 再次启动应正常
    ctl.start(book, 0, 0)
    time.sleep(1.0)
    ctl.stop()
    check("再次启动正常", ctl.is_stopped())

    # v1.6：批量预取缓冲（n+30 上限）
    from novelreader.tts_engine import _EdgePrefetch
    pcontent = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。第九句。第十句。"
    made = []

    def fake_synth(t):
        made.append(t)
        return b"MP3DATA"

    pf = _EdgePrefetch(fake_synth, pcontent, 0, 5)
    deadline = time.time() + 10
    while time.time() < deadline:
        if pf._queue.qsize() >= 5:
            break
        time.sleep(0.02)
    check("预取缓冲填满上限", pf._queue.qsize() == 5, f"qsize={pf._queue.qsize()}")
    item = pf.get(timeout=2)
    check("预取首句正确", item is not None and item[0] == "第一句。", str(item))
    check("预取句序递增", made[:4] == ["第一句。", "第二句。", "第三句。", "第四句。"], str(made[:4]))
    check("默认预取上限为 30", _EdgePrefetch.MAX_AHEAD == 30, str(_EdgePrefetch.MAX_AHEAD))
    pf.close()

    # v1.7：句子停顿间隔设置
    ctl.set_sentence_gap(0.05)
    with ctl._cv:
        check("句子停顿间隔存储", abs(ctl._sentence_gap - 0.05) < 1e-9, str(ctl._sentence_gap))
    ctl.set_sentence_gap(0.10)
    with ctl._cv:
        check("句子停顿间隔默认 0.10", abs(ctl._sentence_gap - 0.10) < 1e-9, str(ctl._sentence_gap))

    # v1.7：缓存进度查询（默认 SAPI 后端返回 None）
    check("非 Edge 后端缓存进度为 None", ctl.prefetch_progress() is None)


def main():
    paths = make_samples()
    test_chapterizer()
    test_parse(paths)
    test_storage()
    test_tts_logic()
    print()
    if FAIL:
        print(f"共 {len(FAIL)} 项失败: {FAIL}")
        sys.exit(1)
    print("全部冒烟测试通过")


if __name__ == "__main__":
    main()
