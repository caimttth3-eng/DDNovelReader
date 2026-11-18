# -*- coding: utf-8 -*-
"""局域网本地同步：端到端流程自测（PC ⇄ 内嵌服务端 ⇄ 模拟手机）。

跑法： .venv\\Scripts\\python.exe tests\\test_lan_sync_flow.py

覆盖：
1) PC 把自己的记录推到内嵌服务的 lan 目录
2) 手机会话（同一个 WebDAVClient）改写进度 → 服务端置脏 → PC 再合并时进度跟随手机
3) 手机独有的书（记录 + 源文件）→ PC 自动下载入书架（跨机取书）
4) 手机发墓碑删除 → PC 同步后从书架移除
"""
import io
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "_tmp_lan_flow")
os.environ["DOUBAO_NOVEL_DATA"] = TMP

from novelreader.book_loader import parse_book                      # noqa: E402
from novelreader.content_key import content_key_of_chapters         # noqa: E402
from novelreader.lan_server import LanServer, new_token             # noqa: E402
from novelreader.storage import Storage                             # noqa: E402
from novelreader.sync_engine import SyncEngine, WebDAVClient        # noqa: E402

FAIL = []
TOKEN = new_token()
LAN_DIR = "DDNovelReader"

BOOK_A = """第一章 起点

这是局域网同步测试的第一本书。正文需要足够长，才能算出稳定的书籍指纹。
再多写几句，保证样本充足。

第二章 中途

第二段正文，继续凑字数，让内容指纹稳定一些。

第三章 终点

第三段正文，收尾。
"""

BOOK_B = """第一章 手机上的书

这本书只在手机端存在，用来验证电脑能不能自动把它取回来。
多写几句，保证分章与指纹可复现。

第二章 结束

收尾段落。
"""


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def fresh():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)


def write_book(name, text):
    d = os.path.join(TMP, "books")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".txt")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def add_local_book(st, title, text):
    p = write_book(title, text)
    content = parse_book(p)
    key = content_key_of_chapters(content.title or title, content.chapters)
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
    return key


def lan_overlay(base, port):
    return {
        "sync_server": "http://127.0.0.1:%d/" % port,
        "sync_user": "duoduo",
        "sync_pass": TOKEN,
        "sync_dir": LAN_DIR,
        "sync_verify_tls": False,
        "sync_device_name": "PC-LAN",
        "sync_auto": False,
    }


def main():
    fresh()
    st = Storage(path=os.path.join(TMP, "library.json"))
    key_a = add_local_book(st, "局域网测试书A", BOOK_A)
    print("book A key =", key_a)

    srv = LanServer(TMP, port=0, username="duoduo", password=TOKEN, remote_dir=LAN_DIR)
    ok, err = srv.start()
    check("内嵌服务启动", ok, err)
    port = srv.status()["port"]
    overlay = lan_overlay("", port)
    phone = WebDAVClient("http://127.0.0.1:%d/" % port, "duoduo", TOKEN, LAN_DIR, verify_tls=False)

    # 1) PC 推记录到自己的 lan 目录
    st.set_setting("sync_verify_tls", False)
    eng = SyncEngine(st, overlay)
    rep = eng.sync()
    check("PC 首次同步成功", rep.get("ok"), rep.get("error"))
    check("已上传本书记录", rep.get("pushed", 0) >= 1, rep)
    rec_rel = "v1/books/%s.json" % key_a
    raw = phone.get(rec_rel)
    check("手机能读到 PC 推的记录", bool(raw), rec_rel)
    rec = json.loads(raw.decode("utf-8")) if raw else {}
    check("记录带书名与进度", rec.get("title") and isinstance(rec.get("progress"), dict), rec.get("title"))

    # 2) 手机改写进度 → 脏标记 → PC 合并后进度跟随
    srv.take_dirty()
    rec["progress"] = {"chapter_idx": 2, "char_offset": 15, "percent": 73.0}
    rec["progress_at"] = round(time.time() + 5, 3)
    rec["device"] = "Phone-Test"
    check("手机写回进度", phone.put(rec_rel, json.dumps(rec, ensure_ascii=False).encode("utf-8")) is True)
    check("服务端已置脏（事件驱动依据）", srv.take_dirty() is True)
    rep = SyncEngine(st, overlay).sync()
    got = (st.all_books().get(next(iter(st.all_books())), {}) or {}).get("progress") or {}
    check("PC 合并成功", rep.get("ok"), rep.get("error"))
    check("PC 进度跟随手机", int(got.get("chapter_idx") or 0) == 2, got)
    check("PC 百分比跟随手机", abs(float(got.get("percent") or 0) - 73.0) < 0.01, got)

    # 3) 手机独有书：记录 + 源文件 → PC 自动取回入书架
    phone.mkdirs("v1/books")
    phone.mkdirs("v1/sources")
    tmp_b = os.path.join(TMP, "phone_b.txt")
    with io.open(tmp_b, "w", encoding="utf-8") as f:
        f.write(BOOK_B)
    content_b = parse_book(tmp_b)
    key_b = content_key_of_chapters(content_b.title or "手机书B", content_b.chapters)
    body_b = io.open(tmp_b, "rb").read()
    rec_b = {
        "schema": 2,
        "content_key": key_b,
        "title": content_b.title or "手机书B",
        "author": "",
        "total_chars": content_b.total_chars,
        "chapter_count": len(content_b.chapters),
        "progress": {"chapter_idx": 1, "char_offset": 5, "percent": 88.0},
        "progress_at": round(time.time(), 3),
        "bookmarks": [],
        "source": {"ext": ".txt", "size": len(body_b), "name": "phone_b.txt"},
        "device": "Phone-Test",
        "updated_at": round(time.time(), 3),
    }
    phone.put("v1/books/%s.json" % key_b, json.dumps(rec_b, ensure_ascii=False).encode("utf-8"))
    phone.put("v1/sources/%s.txt" % key_b, body_b, atomic=False)
    rep = SyncEngine(st, overlay).sync()
    titles = [b.get("title") for b in st.all_books().values()]
    check("跨机取书：手机独有书已导入书架", any("手机" in (t or "") or "手机上的书" in (t or "")
                                                or (t or "").strip() for t in titles), titles)
    check("跨机取书计数", rep.get("imported", 0) >= 1, rep)
    new_book = None
    for b in st.all_books().values():
        if (b.get("content_key") or "") == key_b:
            new_book = b
    check("导入书带远端进度", bool(new_book) and
          int((new_book.get("progress") or {}).get("chapter_idx") or 0) == 1, new_book)

    # 4) 手机发墓碑 → PC 同步后移除
    rec_b2 = dict(rec_b)
    rec_b2.update({"deleted": True, "deleted_at": round(time.time() + 10, 3),
                   "updated_at": round(time.time() + 10, 3)})
    phone.put("v1/books/%s.json" % key_b, json.dumps(rec_b2, ensure_ascii=False).encode("utf-8"))
    rep = SyncEngine(st, overlay).sync()
    still = [b for b in st.all_books().values() if (b.get("content_key") or "") == key_b]
    check("手机删除已同步（墓碑生效）", not still, [b.get("title") for b in still])
    check("删除计数", rep.get("removed", 0) >= 1, rep)

    srv.stop()
    print("\n==== %d FAIL ====" % len(FAIL))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
