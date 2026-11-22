# -*- coding: utf-8 -*-
"""跨端联调编排：PC 端（本脚本）⇄ 安卓端（flutter test test/e2e_cross_test.dart）
共用一台 mock WebDAV（127.0.0.1:8080/dav，demo/demo）。

流程：
  setup     PC 建两本书 A/B 并上传（安卓之后拉）
  verify1   PC 同步后校验：A 的进度 = 安卓读到的那章；B 已被安卓删掉
  prep2     PC 删掉 A 并上传墓碑（安卓之后应移除）
  status    打印远端当前记录

用法： python tests/e2e_cross.py setup|verify1|prep2|status --dir <远端目录> --ctx <ctx.json>
"""
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SERVER = "http://127.0.0.1:8080/dav"
USER, PASS = "demo", "demo"
LOCAL = "DDCrossReader"

FIXTURE_A = r"C:\Users\lyuga\Doubao\chats\2026-09-01\new-chat\duoduo_langdu\test\fixtures\test_book.txt"
FIXTURE_B = """第一章 起点

跨端测试第二本书的正文。这一本用来验证删除能不能同步到另一端。
多写几句话，好让分章和字数在两端算得一样。

第二章 终点

第二段正文，收尾。
"""

FAIL = []


def check(name, cond, extra=""):
    print(("[ OK ] " if cond else "[FAIL] ") + name + ("" if cond else " %s" % (extra,)))
    if not cond:
        FAIL.append(name)


def pc_base():
    base = os.environ.get("DD_CROSS_BASE") or os.path.join(tempfile.gettempdir(), "dd_cross_pc")
    os.makedirs(base, exist_ok=True)
    os.environ["DOUBAO_NOVEL_DATA"] = base
    return base


def make_books(base):
    from novelreader.book_loader import parse_book
    from novelreader.content_key import content_key_of_chapters
    import io

    book_dir = os.path.join(base, "books")
    os.makedirs(book_dir, exist_ok=True)
    paths = {}
    texts = {"跨端测试A": io.open(FIXTURE_A, encoding="utf-8").read(),
             "跨端测试B": FIXTURE_B}
    for title, text in texts.items():
        p = os.path.join(book_dir, title + ".txt")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(text)
        content = parse_book(p)
        key = content_key_of_chapters(content.title or title, content.chapters)
        paths[title] = (p, content, key)
    return paths


def make_storage(base):
    from novelreader.storage import Storage
    st = Storage(path=os.path.join(base, "library.json"))
    st.set_setting("sync_verify_tls", False)
    return st


def settings_of(remote_dir):
    return {
        "sync_server": SERVER,
        "sync_user": USER,
        "sync_pass": PASS,
        "sync_dir": remote_dir,
        "sync_verify_tls": False,
        "sync_device_name": "PC-Cross",
    }


def setup(args):
    base = pc_base()
    paths = make_books(base)
    st = make_storage(base)
    from novelreader.storage import Storage
    for title, (p, content, key) in paths.items():
        st.add_book({
            "id": Storage.book_id(p),
            "content_key": key,
            "title": content.title or title,
            "author": "",
            "format": "txt",
            "path": p,
            "source_bak": p,
            "added_at": time.time() - 500,
            "last_read_at": time.time() - 400,
            "progress_at": time.time() - 400,
            "total_chars": content.total_chars,
            "chapter_titles": [c.title for c in content.chapters],
            "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
        })
    from novelreader.sync_engine import SyncEngine
    rep = SyncEngine(st, settings_of(args.dir)).sync()
    check("setup PC 上传两本书", rep["ok"] and rep["pushed"] >= 2, rep)
    keys = {t: v[2] for t, v in paths.items()}
    print("keys:", json.dumps(keys, ensure_ascii=False))
    with open(args.ctx, "w", encoding="utf-8") as f:
        json.dump({"server": SERVER, "dir": args.dir, "user": USER, "pass": PASS,
                   "keys": keys, "bookA": paths["跨端测试A"][0]}, f, ensure_ascii=False, indent=1)


def verify1(args):
    base = pc_base()
    st = make_storage(base)
    from novelreader.sync_engine import SyncEngine
    make_books(base)
    rep = SyncEngine(st, settings_of(args.dir)).sync()
    titles = {b.get("title"): b for b in st.all_books().values()}
    check("verify1 同步成功", rep["ok"], rep)
    a = titles.get("跨端测试A")
    prog = (a or {}).get("progress") or {}
    check("verify1 A 进度 = 安卓读到的第 3 章", bool(a) and int(prog.get("chapter_idx") or 0) == 2, prog)
    check("verify1 A 的 percent 来自安卓（并折算到 0~100）",
          bool(a) and abs(float(prog.get("percent") or 0) - 90.0) < 0.01, prog)
    check("verify1 A 的 char_offset 来自安卓", bool(a) and int(prog.get("char_offset") or 0) == 321, prog)
    check("verify1 B 已按安卓的删除移出书架", "跨端测试B" not in titles, sorted(titles))
    check("verify1 removed 计数", rep["removed"] >= 1, rep)
    print("PC 书架：", json.dumps(sorted(titles), ensure_ascii=False))


def prep2(args):
    base = pc_base()
    st = make_storage(base)
    from novelreader.sync_engine import SyncEngine
    bid = None
    for k, b in st.all_books().items():
        if (b.get("title") or "") == "跨端测试A":
            bid = k
    check("prep2 找到 A", bid is not None, list(st.all_books()))
    if bid:
        st.remove_book(bid)
    rep = SyncEngine(st, settings_of(args.dir)).sync()
    check("prep2 删除事件已上传", rep["ok"] and rep["deleted"] >= 1, rep)
    print("PC 书架（应为空）：", json.dumps(list(st.all_books()), ensure_ascii=False))


def status(args):
    from novelreader.sync_engine import SyncEngine, WebDAVClient
    st = make_storage(pc_base())
    cli = WebDAVClient(SERVER, USER, PASS, args.dir, verify_tls=False)
    eng = SyncEngine(st, settings_of(args.dir))
    remote, _etags = eng.read_remote_books(cli)
    print("远端记录 %d 条：" % len(remote))
    for k, r in sorted(remote.items()):
        print("  %s deleted=%s title=%s progress=%s upd=%s" % (
            k, r.get("deleted"), r.get("title"), r.get("progress"), r.get("updated_at")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["setup", "verify1", "prep2", "status"])
    ap.add_argument("--dir", default="DDCrossReader_test")
    ap.add_argument("--ctx", default=os.path.join(tempfile.gettempdir(), "dd_cross_ctx.json"))
    a = ap.parse_args()
    {"setup": setup, "verify1": verify1, "prep2": prep2, "status": status}[a.phase](a)
    if FAIL:
        print("FAILED: %s" % FAIL)
        return 1
    print("PHASE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
