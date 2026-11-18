# -*- coding: utf-8 -*-
"""PC ⇄ 远端 同步引擎自测（schema 2：按字段合并 + 删除墓碑）。

跑法： .venv\\Scripts\\python.exe tests\\test_sync_engine_v2.py
"""
import json
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="dd_sync_test_")
os.environ["DOUBAO_NOVEL_DATA"] = TMP   # 隔离：缓存/数据目录都落在临时目录

from novelreader.storage import Storage
from novelreader.sync_engine import SyncEngine, WebDAVClient, progress_at_of
from novelreader.content_key import compute_content_key, content_key_of_chapters
from novelreader.book_loader import parse_book
from tests.mock_dav import MockDav

# 统一 UTF-8 输出：Windows 控制台默认 cp1252，直接 print 中文会 UnicodeEncodeError
try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


BASE = time.time()   # 以“现在”为基准，避免墓碑 TTL 把测试数据当成过期
FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def make_storage(name, books):
    """独立的一套"设备"数据。books: [meta dict]"""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    st = Storage(path=os.path.join(d, "library.json"))
    st.set_setting("sync_verify_tls", False)
    for meta in books:
        st.add_book(meta)
    return st


def book_meta(bid, key, title, chapter=1, progress_at=BASE - 900.0, last_read_at=None,
              bms=None, added_at=90.0, path=""):
    meta = {
        "id": bid,
        "content_key": key,
        "title": title,
        "author": "",
        "format": "txt",
        "path": path or os.path.join(TMP, key + ".txt"),
        "source_bak": path or "",
        "added_at": added_at,
        "last_read_at": last_read_at if last_read_at is not None else progress_at,
        "progress_at": progress_at,
        "total_chars": 10000,
        "chapter_titles": ["第%d章" % (i + 1) for i in range(6)],
        "progress": {"chapter_idx": chapter, "char_offset": 10, "percent": round(chapter / 6 * 100, 2)},
    }
    if bms:
        meta["bookmarks"] = bms
        meta["bookmarks_at"] = max(b.get("created_at", 0) for b in bms)
    if not path:
        meta.pop("source_bak", None)
    return meta


def dav():
    d = tempfile.mkdtemp(prefix="dd_dav_")
    m = MockDav(d)
    m.__enter__()
    return m


def settings_for(server):
    return {
        "sync_server": server.url,
        "sync_user": "test",
        "sync_pass": "test",
        "sync_dir": "DDNovelReader",
        "sync_verify_tls": False,
        "sync_device_name": "PC-Test",
    }


def push_remote(server, rec, key=None):
    cli = WebDAVClient(server.url, "test", "test", "DDNovelReader", verify_tls=False)
    cli.mkdirs("v1/books")
    cli.put("v1/books/%s.json" % (key or rec["content_key"]),
            json.dumps(rec, ensure_ascii=False).encode("utf-8"))


def read_remote(server, key):
    cli = WebDAVClient(server.url, "test", "test", "DDNovelReader", verify_tls=False)
    raw = cli.get("v1/books/%s.json" % key)
    return json.loads(raw.decode("utf-8")) if raw else None


def rec_of(key, title, progress, progress_at, bookmarks=None, bookmarks_at=0,
           deleted=False, deleted_at=0, schema=2):
    rec = {
        "schema": schema,
        "content_key": key,
        "content_key_version": "ck2",
        "title": title,
        "format": "txt",
        "chapter_count": 6,
        "total_chars": 10000,
        "progress": progress,
        "bookmarks": bookmarks or [],
        "updated_at": max(progress_at, bookmarks_at, deleted_at),
        "device": "Android",
        "progress_at": progress_at,
        "bookmarks_at": bookmarks_at,
    }
    if deleted:
        rec["deleted"] = True
        rec["deleted_at"] = deleted_at
    if schema == 1:
        rec.pop("progress_at", None)
        rec.pop("bookmarks_at", None)
    return rec


# ============================================================ 1) 进度拉取
def case_progress_pull_beats_touch():
    srv = dav()
    st = make_storage("c1", [book_meta("bid1", "ck_prog", "进度测试",
                                       chapter=1, progress_at=BASE - 900.0, last_read_at=BASE - 100.0)])
    eng = SyncEngine(st, settings_for(srv))
    push_remote(srv, rec_of("ck_prog", "进度测试",
                            {"chapter_idx": 40, "char_offset": 7, "percent": 60.0},
                            progress_at=BASE - 500.0))
    rep = eng.sync()
    b = st.all_books()["bid1"]
    check("1a 拉取成功", rep["ok"] and rep["pulled"] == 1, json.dumps(rep, ensure_ascii=False))
    check("1b 进度被远端覆盖", b["progress"]["chapter_idx"] == 40, json.dumps(b["progress"], ensure_ascii=False))
    check("1c progress_at 跟随远端", abs(progress_at_of(b) - (BASE - 500.0)) < 0.01, progress_at_of(b))
    # touch()（只打开书、没读）不再影响合并基准
    st.touch("bid1")
    check("1d touch 不改 progress_at", abs(progress_at_of(b) - (BASE - 500.0)) < 0.01, progress_at_of(b))
    srv.__exit__(None, None, None)


def case_old_schema1_record():
    srv = dav()
    st = make_storage("c2", [book_meta("bid1", "ck_old", "老记录", chapter=2, progress_at=BASE - 900.0)])
    eng = SyncEngine(st, settings_for(srv))
    push_remote(srv, rec_of("ck_old", "老记录",
                            {"chapter_idx": 9, "char_offset": 0, "percent": 30.0},
                            progress_at=BASE - 300.0, schema=1))
    eng.sync()
    b = st.all_books()["bid1"]
    check("2a schema1 记录仍按 updated_at 生效", b["progress"]["chapter_idx"] == 9,
          json.dumps(b["progress"], ensure_ascii=False))
    srv.__exit__(None, None, None)


def case_local_newer_not_overwritten():
    srv = dav()
    st = make_storage("c3", [book_meta("bid1", "ck_new", "本地较新", chapter=12, progress_at=BASE - 50.0)])
    eng = SyncEngine(st, settings_for(srv))
    push_remote(srv, rec_of("ck_new", "本地较新",
                            {"chapter_idx": 3, "char_offset": 0, "percent": 10.0}, progress_at=BASE - 700.0))
    rep = eng.sync()
    b = st.all_books()["bid1"]
    check("3a 远端旧记录不覆盖本地", b["progress"]["chapter_idx"] == 12, b["progress"])
    r = read_remote(srv, "ck_new")
    check("3b 本地较新会推上去", r["progress"]["chapter_idx"] == 12, r and r.get("progress"))
    srv.__exit__(None, None, None)


# ============================================================ 2) 书签
def case_bookmarks_union():
    srv = dav()
    local_bm = {"id": "b1", "created_at": 500.0, "chapter_idx": 1, "offset": 3, "offset_end": 9,
                "text": "本地划线", "note": "本地备注"}
    st = make_storage("c4", [book_meta("bid1", "ck_bm", "书签测试", progress_at=BASE - 900.0,
                                       bms=[local_bm])])
    eng = SyncEngine(st, settings_for(srv))
    remote_bm = {"id": "b2", "created_at": 600.0, "chapter_idx": 2, "offset": 1, "offset_end": 4,
                 "text": "安卓划线", "note": ""}
    push_remote(srv, rec_of("ck_bm", "书签测试", {"chapter_idx": 2, "char_offset": 0, "percent": 5.0},
                            progress_at=BASE - 800.0, bookmarks=[remote_bm], bookmarks_at=BASE - 200.0))
    rep = eng.sync()
    bms = st.all_books()["bid1"]["bookmarks"]
    check("4a 书签并集两条", len(bms) == 2, bms)
    check("4b 不丢本地书签", any(x["id"] == "b1" for x in bms), bms)
    check("4c 拉取计数", rep["pulled"] == 1, rep)
    srv.__exit__(None, None, None)


# ============================================================ 3) 删除（减量）
def case_remote_delete_removes_local():
    srv = dav()
    st = make_storage("c5", [book_meta("bid1", "ck_del", "对端删除", progress_at=BASE - 900.0, added_at=BASE - 1000.0)])
    eng = SyncEngine(st, settings_for(srv))
    push_remote(srv, rec_of("ck_del", "对端删除", {}, progress_at=0, deleted=True, deleted_at=BASE - 600.0))
    rep = eng.sync()
    check("5a 本地书被删除", "bid1" not in st.all_books(), list(st.all_books()))
    check("5b 删除计数", rep["removed"] == 1, rep)
    check("5c 留下本地墓碑", "ck_del" in st.deletions(), st.deletions())
    # 再同步一次不会把书拉回来
    eng2 = SyncEngine(st, settings_for(srv))
    rep2 = eng2.sync()
    check("5d 二次同步不复活", "bid1" not in st.all_books(), list(st.all_books()))
    check("5e 不重复下载源文件", rep2["imported"] == 0, rep2)
    srv.__exit__(None, None, None)


def case_local_delete_pushes_tombstone():
    srv = dav()
    st = make_storage("c6", [book_meta("bid1", "ck_gone", "本机删除", progress_at=BASE - 900.0, added_at=BASE - 1000.0)])
    eng = SyncEngine(st, settings_for(srv))
    eng.sync()                       # 先正常同步一次，让远端有这条记录
    r1 = read_remote(srv, "ck_gone")
    check("6a 首次同步已上传", r1 is not None and not r1.get("deleted"), r1)

    st.remove_book("bid1")           # 用户在 PC 删除
    rep = SyncEngine(st, settings_for(srv)).sync()
    r2 = read_remote(srv, "ck_gone")
    check("6b 上传墓碑", r2 is not None and r2.get("deleted") is True, r2)
    check("6c 删除事件计数", rep["deleted"] >= 1, rep)
    check("6d 墓碑带删除时间", float(r2.get("deleted_at") or 0) > 0, r2.get("deleted_at"))

    # 另一台设备（记录还在）同步后也被删除
    other = make_storage("c6b", [book_meta("bid_other", "ck_gone", "本机删除",
                                           progress_at=BASE - 900.0, added_at=BASE - 1000.0)])
    rep3 = SyncEngine(other, settings_for(srv)).sync()
    check("6e 对端删掉同本书", "bid_other" not in other.all_books(), list(other.all_books()))
    check("6f 对端删除计数", rep3["removed"] == 1, rep3)
    srv.__exit__(None, None, None)


def case_reimport_revives():
    srv = dav()
    st = make_storage("c7", [book_meta("bid1", "ck_re", "复活测试", progress_at=BASE - 900.0, added_at=BASE - 1000.0)])
    SyncEngine(st, settings_for(srv)).sync()
    st.remove_book("bid1")
    SyncEngine(st, settings_for(srv)).sync()
    check("7a 已变墓碑", (read_remote(srv, "ck_re") or {}).get("deleted") is True, read_remote(srv, "ck_re"))

    # 用户重新导入同一本书
    now = time.time()
    st.add_book(book_meta("bid1b", "ck_re", "复活测试", chapter=3, progress_at=now, added_at=now))
    rep = SyncEngine(st, settings_for(srv)).sync()
    r = read_remote(srv, "ck_re")
    check("7b 重新导入后远端恢复为正常记录", not (r or {}).get("deleted", False), r)
    check("7c 本地墓碑已清除", "ck_re" not in st.deletions(), st.deletions())
    check("7d 复活后进度为本地值", (r or {}).get("progress", {}).get("chapter_idx") == 3, (r or {}).get("progress"))
    srv.__exit__(None, None, None)


def case_delete_older_than_remote_activity():
    """对端在本机删除之后又读了这本书 → 对端更新更晚，不能被删掉。"""
    srv = dav()
    src = os.path.join(TMP, "fight.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("第一章 甲\n正文甲。\n\n第二章 乙\n正文乙。\n")
    content = parse_book(src)
    key = content_key_of_chapters(content.title or "", content.chapters)
    st = make_storage("c8", [])
    st.add_book({
        "id": "bid1", "content_key": key, "title": content.title, "format": "txt",
        "path": src, "source_bak": src, "added_at": BASE - 1000.0,
        "last_read_at": BASE - 900.0, "progress_at": BASE - 900.0,
        "total_chars": content.total_chars,
        "chapter_titles": [c.title for c in content.chapters],
        "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
    })
    cli = WebDAVClient(srv.url, "test", "test", "DDNovelReader", verify_tls=False)
    cli.mkdirs("v1/sources")
    with open(src, "rb") as f:
        cli.put("v1/sources/%s.txt" % key, f.read(), atomic=False)
    SyncEngine(st, settings_for(srv)).sync()
    st.remove_book("bid1")
    # 远端在本机删除之后被另一台设备读过
    _now = time.time()   # 用"此刻"为基准：本用例前面的用例可能跑了几十秒，用测试开始的 BASE+30 会被本地删除时间反超
    rec = rec_of(key, content.title, {"chapter_idx": 1, "char_offset": 0, "percent": 80.0},
                 progress_at=_now + 30, bookmarks=[], bookmarks_at=0)
    rec["source"] = {"ext": ".txt", "size": os.path.getsize(src), "name": "fight.txt"}
    rec["chapter_count"] = len(content.chapters)
    rec["total_chars"] = content.total_chars
    push_remote(srv, rec, key)
    rep = SyncEngine(st, settings_for(srv)).sync()
    r = read_remote(srv, key)
    check("8a 远端更新更晚 → 不推删除", not (r or {}).get("deleted", False), r)
    check("8b 书被拉回本地", len(st.all_books()) == 1, rep)
    srv.__exit__(None, None, None)


# ============================================================ 4) 增量（远端独有书导入）
def case_import_remote_only_book():
    srv = dav()
    sample = "第一章 开端\n正文一。\n\n第二章 转折\n正文二。\n"
    src = os.path.join(TMP, "remote_book.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write(sample)
    content = parse_book(src)
    key = content_key_of_chapters(content.title or "", content.chapters)
    cli = WebDAVClient(srv.url, "test", "test", "DDNovelReader", verify_tls=False)
    cli.mkdirs("v1/books")
    cli.mkdirs("v1/sources")
    rec = rec_of(key, content.title, {"chapter_idx": 1, "char_offset": 3, "percent": 50.0},
                 progress_at=BASE - 20.0, bookmarks=[{"id": "bm9", "created_at": BASE - 20.0, "chapter_idx": 1,
                                                "offset": 0, "offset_end": 2, "text": "正文二", "note": ""}])
    rec["chapter_count"] = len(content.chapters)
    rec["total_chars"] = content.total_chars
    rec["source"] = {"ext": ".txt", "size": os.path.getsize(src), "name": "remote_book.txt"}
    push_remote(srv, rec, key)
    with open(src, "rb") as f:
        cli.put("v1/sources/%s.txt" % key, f.read(), atomic=False)

    st = make_storage("c9", [])
    rep = SyncEngine(st, settings_for(srv)).sync()
    books = list(st.all_books().values())
    check("9a 远端独有书被导入", rep["imported"] == 1 and len(books) == 1, rep)
    if books:
        b = books[0]
        check("9b 进度被套上", b["progress"]["chapter_idx"] == 1, b["progress"])
        check("9c 书签被套上", len(b.get("bookmarks") or []) == 1, b.get("bookmarks"))
        check("9d key 一致", b["content_key"] == key, (b["content_key"], key))
        check("9e progress_at 保留远端时间", abs(progress_at_of(b) - (BASE - 20.0)) < 1.0, progress_at_of(b))
    rep2 = SyncEngine(st, settings_for(srv)).sync()
    check("9f 二次同步不重复导入", rep2["imported"] == 0 and len(st.all_books()) == 1, rep2)
    srv.__exit__(None, None, None)


def case_percent_scale():
    """percent 口径：安卓内部 0~1、PC 0~100，入站要折算，不能显示成 0.4%。"""
    srv = dav()
    st = make_storage("c10", [book_meta("bid1", "ck_pct", "百分比口径", chapter=1, progress_at=BASE - 900.0)])
    eng = SyncEngine(st, settings_for(srv))
    push_remote(srv, rec_of("ck_pct", "百分比口径",
                            {"chapter_idx": 4, "char_offset": 0, "percent": 0.42},
                            progress_at=BASE - 500.0))
    eng.sync()
    b = st.all_books()["bid1"]
    check("10a 0~1 的 percent 折成 0~100", abs(float(b["progress"]["percent"]) - 42.0) < 0.01, b["progress"])
    # PC 端本地有变化后再推：出站必须是 0~100 口径
    st.update_progress("bid1", {"chapter_idx": 5, "char_offset": 0, "percent": 55.0})
    SyncEngine(st, settings_for(srv)).sync()
    r = read_remote(srv, "ck_pct")
    check("10b 回推 percent 是 0~100", abs(float(r["progress"]["percent"]) - 55.0) < 0.01, r.get("progress"))

    # 已经是 0~100 的记录不能被二次放大
    st2 = make_storage("c11", [book_meta("bid2", "ck_pct2", "百分比口径2", chapter=1, progress_at=BASE - 900.0)])
    push_remote(srv, rec_of("ck_pct2", "百分比口径2",
                            {"chapter_idx": 2, "char_offset": 0, "percent": 66.0},
                            progress_at=BASE - 500.0))
    SyncEngine(st2, settings_for(srv)).sync()
    check("10c 66.0 保持 66.0", abs(float(st2.all_books()["bid2"]["progress"]["percent"]) - 66.0) < 0.01,
          st2.all_books()["bid2"]["progress"])
    srv.__exit__(None, None, None)


def main():
    case_progress_pull_beats_touch()
    case_old_schema1_record()
    case_local_newer_not_overwritten()
    case_bookmarks_union()
    case_remote_delete_removes_local()
    case_local_delete_pushes_tombstone()
    case_reimport_revives()
    case_delete_older_than_remote_activity()
    case_import_remote_only_book()
    case_percent_scale()
    print("")
    if FAIL:
        print("FAILED: %d 项 %s" % (len(FAIL), FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
