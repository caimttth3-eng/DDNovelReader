# -*- coding: utf-8 -*-
"""2.3.2 字段级合并 / 三模式 / ETag 条件写 专项自测。

跑法： .venv\\Scripts\\python.exe tests\\test_sync_patch_v232.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_sync_engine_v2 import (BASE, FAIL, book_meta, check, dav, make_storage,
                                      push_remote, read_remote, rec_of, settings_for)
from novelreader.sync_engine import SyncEngine, WebDAVClient, WebDAVError


def raw_remote(server, key):
    cli = WebDAVClient(server.url, "test", "test", "DDNovelReader", verify_tls=False)
    raw = cli.get("v1/books/%s.json" % key)
    return raw


def bm(bid, created_at, chapter=0):
    return {"id": bid, "chapter_idx": chapter, "char_offset": 0,
            "created_at": created_at, "note": ""}


def has_key(st, key):
    return any((b or {}).get("content_key") == key for b in st.all_books().values())


def case_concurrent_different_fields():
    """PC 只改了书签、另一台只改了进度 → 一次合并后两边都不丢。"""
    srv = dav()
    st = make_storage("m1", [book_meta("bid1", "ck_m1", "并发合并", chapter=1,
                                     progress_at=BASE - 900.0,
                                     bms=[bm("b1", BASE - 600.0)], added_at=BASE - 1000.0)])
    push_remote(srv, rec_of("ck_m1", "并发合并",
                            {"chapter_idx": 5, "char_offset": 0, "percent": 83.33},
                            progress_at=BASE - 60.0, bookmarks=[], bookmarks_at=0))
    rep = SyncEngine(st, settings_for(srv)).sync()
    r = read_remote(srv, "ck_m1")
    check("A1 对端新进度没被本地旧进度覆盖", r.get("progress", {}).get("chapter_idx") == 5, r.get("progress"))
    check("A2 本地新书签已并入远端", any(b.get("id") == "b1" for b in (r.get("bookmarks") or [])),
          r.get("bookmarks"))
    check("A3 progress_at 保留对端的较新时间",
          abs(float(r.get("progress_at")) - (BASE - 60.0)) < 0.01, r.get("progress_at"))
    check("A4 本地也采纳了对端进度", st.all_books()["bid1"]["progress"]["chapter_idx"] == 5,
          st.all_books()["bid1"]["progress"])
    check("A5 本地书签没丢", any(b.get("id") == "b1" for b in st.all_books()["bid1"].get("bookmarks") or []))
    check("A6 无冲突计数", rep.get("conflicts") == 0, rep.get("conflicts"))
    srv.__exit__(None, None, None)


def case_local_bookmark_only():
    """本地只加了一条书签（进度没动）→ 推送时不得用本地旧进度覆盖对端。"""
    srv = dav()
    st = make_storage("m2", [book_meta("bid2", "ck_m2", "只加书签", chapter=1,
                                      progress_at=BASE - 900.0,
                                      bms=[bm("bx", BASE - 30.0)], added_at=BASE - 1000.0)])
    push_remote(srv, rec_of("ck_m2", "只加书签",
                            {"chapter_idx": 6, "char_offset": 0, "percent": 100.0},
                            progress_at=BASE - 20.0))
    SyncEngine(st, settings_for(srv)).sync()
    r = read_remote(srv, "ck_m2")
    check("B1 远端进度仍是它自己的", r.get("progress", {}).get("chapter_idx") == 6, r.get("progress"))
    check("B2 远端 bookmarks_at 取本地新值", abs(float(r.get("bookmarks_at")) - (BASE - 30.0)) < 0.01,
          r.get("bookmarks_at"))
    srv.__exit__(None, None, None)


def case_push_mode_only():
    """mode="push"：只往上写，绝不往下拉。"""
    srv = dav()
    st = make_storage("m3", [book_meta("bid3", "ck_m3l", "本机书", chapter=2,
                                       progress_at=BASE - 100.0, added_at=BASE - 1000.0)])
    push_remote(srv, rec_of("ck_m3r", "远端独有", {"chapter_idx": 3, "char_offset": 0, "percent": 50.0},
                            progress_at=BASE - 50.0))
    rep = SyncEngine(st, settings_for(srv)).sync(mode="push")
    check("C1 本机书已上传", read_remote(srv, "ck_m3l") is not None)
    check("C2 没导入远端书", not has_key(st, "ck_m3r"), list(st.all_books()))
    check("C3 计数只在上传侧", rep.get("pushed", 0) >= 1 and rep.get("imported", 0) == 0
          and rep.get("pulled", 0) == 0, rep)
    srv.__exit__(None, None, None)


def case_pull_mode_only():
    """mode="pull"：只往下拉，绝不往上写；远端的新进度要落到本机。"""
    srv = dav()
    st = make_storage("m4", [book_meta("bid4", "ck_m4", "双向书", chapter=1,
                                       progress_at=BASE - 900.0, added_at=BASE - 1000.0)])
    push_remote(srv, rec_of("ck_m4", "双向书", {"chapter_idx": 6, "char_offset": 0, "percent": 100.0},
                            progress_at=BASE - 40.0))
    rep = SyncEngine(st, settings_for(srv)).sync(mode="pull")
    check("D1 本机采纳远端进度", st.all_books()["bid4"]["progress"]["chapter_idx"] == 6,
          st.all_books()["bid4"]["progress"])
    check("D2 远端记录没被本机改写时间",
          abs(float(read_remote(srv, "ck_m4").get("progress_at")) - (BASE - 40.0)) < 0.01,
          read_remote(srv, "ck_m4").get("progress_at"))
    check("D3 没有上传计数", rep.get("pushed", 0) == 0, rep)
    srv.__exit__(None, None, None)


def case_remote_only_then_pull_no_write():
    """本机独有书在 pull 模式下不许被写上去。"""
    srv = dav()
    st = make_storage("m5", [book_meta("bid5", "ck_m5l", "只在本机", chapter=1,
                                       progress_at=BASE - 200.0, added_at=BASE - 1000.0)])
    SyncEngine(st, settings_for(srv)).sync(mode="pull")
    check("E1 远端没有出现本机独有书", read_remote(srv, "ck_m5l") is None)
    srv.__exit__(None, None, None)


def case_dry_run_no_write():
    """预览模式：计数正确但一个字节都不写。"""
    srv = dav()
    st = make_storage("m6", [book_meta("bid6", "ck_m6", "预览书", chapter=4,
                                       progress_at=BASE - 10.0, added_at=BASE - 1000.0)])
    push_remote(srv, rec_of("ck_m6", "预览书", {"chapter_idx": 1, "char_offset": 0, "percent": 16.67},
                            progress_at=BASE - 800.0))
    before = raw_remote(srv, "ck_m6")
    rep = SyncEngine(st, settings_for(srv)).sync(mode="push", dry_run=True)
    after = raw_remote(srv, "ck_m6")
    check("F1 dry_run 报告将上传 1 本", rep.get("pushed") == 1, rep)
    check("F2 远端字节完全未变", before == after)
    check("F3 本地进度未变", st.all_books()["bid6"]["progress"]["chapter_idx"] == 4,
          st.all_books()["bid6"]["progress"])
    srv.__exit__(None, None, None)


def case_412_remerge():
    """读取之后对端又写了一次 → 412 → 重读、重合并、再写；两边改动都保住。"""
    srv = dav()
    st = make_storage("m7", [book_meta("bid7", "ck_m7", "冲突重试", chapter=2,
                                       progress_at=BASE - 500.0,
                                       bms=[bm("bc", BASE - 300.0)], added_at=BASE - 1000.0)])
    push_remote(srv, rec_of("ck_m7", "冲突重试", {"chapter_idx": 2, "char_offset": 0, "percent": 33.33},
                            progress_at=BASE - 500.0))
    orig_put = WebDAVClient.put
    state = {"done": False}

    def flaky_put(self, rel, data, atomic=True, etag=None, content_type="application/json; charset=utf-8"):
        if etag and not state["done"]:
            state["done"] = True
            # 模拟"对端在我们 PROPFIND 之后又读了这本书"
            concurrent = rec_of("ck_m7", "冲突重试",
                                {"chapter_idx": 6, "char_offset": 0, "percent": 100.0},
                                progress_at=BASE - 5.0, bookmarks=[], bookmarks_at=0)
            orig_put(self, rel, json.dumps(concurrent, ensure_ascii=False).encode("utf-8"), etag=None)
            raise WebDAVError("412 Precondition Failed", 412)
        return orig_put(self, rel, data, atomic=atomic, etag=etag, content_type=content_type)

    WebDAVClient.put = flaky_put
    try:
        rep = SyncEngine(st, settings_for(srv)).sync()
    finally:
        WebDAVClient.put = orig_put
    r = read_remote(srv, "ck_m7")
    check("G1 发生了一次冲突重试", rep.get("conflicts") == 1, rep.get("conflicts"))
    check("G2 对端的更新进度胜出", r.get("progress", {}).get("chapter_idx") == 6, r.get("progress"))
    check("G3 本机书签仍在", any(b.get("id") == "bc" for b in (r.get("bookmarks") or [])),
          r.get("bookmarks"))
    srv.__exit__(None, None, None)


def case_idempotent():
    """连续两次 both：第二次不该再产生上传。"""
    srv = dav()
    st = make_storage("m8", [book_meta("bid8", "ck_m8", "幂等书", chapter=2,
                                       progress_at=BASE - 100.0, added_at=BASE - 1000.0)])
    SyncEngine(st, settings_for(srv)).sync()
    snap = raw_remote(srv, "ck_m8")
    rep2 = SyncEngine(st, settings_for(srv)).sync()
    check("H1 第二次没有上传", rep2.get("pushed") == 0 and rep2.get("deleted") == 0, rep2)
    check("H2 远端记录未被改写", raw_remote(srv, "ck_m8") == snap)
    srv.__exit__(None, None, None)


def case_delete_tombstone_still_bidirectional():
    """减量：本地删书 → 对端墓碑；对端再删另一本 → 本机跟随删除（三模式中的 both）。"""
    srv = dav()
    st = make_storage("m9", [book_meta("bid9a", "ck_m9a", "本地要删", chapter=1, added_at=BASE - 1000.0),
                             book_meta("bid9b", "ck_m9b", "对端要删", chapter=1, added_at=BASE - 1000.0)])
    SyncEngine(st, settings_for(srv)).sync()
    push_remote(srv, rec_of("ck_m9b", "对端要删", {}, progress_at=0, deleted=True,
                            deleted_at=BASE - 5.0))
    st.remove_book("bid9a")
    rep = SyncEngine(st, settings_for(srv)).sync()
    r_a = read_remote(srv, "ck_m9a")
    check("I1 本地删除推到远端成墓碑", (r_a or {}).get("deleted") is True, r_a)
    check("I2 对端删除让本机也删掉", "bid9b" not in st.all_books(), list(st.all_books()))
    check("I3 删除计数", rep.get("removed") >= 1 and rep.get("deleted") >= 1, rep)
    srv.__exit__(None, None, None)


def main():
    case_concurrent_different_fields()
    case_local_bookmark_only()
    case_push_mode_only()
    case_pull_mode_only()
    case_remote_only_then_pull_no_write()
    case_dry_run_no_write()
    case_412_remerge()
    case_idempotent()
    case_delete_tombstone_still_bidirectional()
    print("")
    if FAIL:
        print("FAILED: %d 项 %s" % (len(FAIL), FAIL))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
