# -*- coding: utf-8 -*-
"""局域网同步数据迁移自测（v2.2.9 补丁：<数据目录>/lan → <缓存根>/lan）。

跑法： .venv\\Scripts\\python.exe tests\\test_lan_migration.py
"""
import io
import os
import shutil
import sys

# 统一 UTF-8 输出：Windows 控制台默认 cp1252，直接 print 中文会 UnicodeEncodeError
try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "_tmp_lan_mig")

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def fresh(data=None):
    shutil.rmtree(BASE, ignore_errors=True)
    data = data or os.path.join(BASE, "data")
    os.makedirs(data, exist_ok=True)
    os.environ["DOUBAO_NOVEL_DATA"] = data
    return data


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def main():
    # ---------- 1) 默认位置：<数据目录>/lan → <数据目录>/cache/lan ----------
    data = fresh()
    import importlib
    import novelreader.storage as st
    importlib.reload(st)

    check("旧位置 = <数据目录>/lan",
          os.path.normcase(st.legacy_lan_dir()) == os.path.normcase(os.path.join(data, "lan")),
          st.legacy_lan_dir())
    check("新位置 = <缓存根>/lan",
          os.path.normcase(st.lan_dir("")) == os.path.normcase(os.path.join(data, "cache", "lan")),
          st.lan_dir(""))

    legacy_books = os.path.join(st.legacy_lan_dir(), "DDNovelReader", "v1", "books")
    write(os.path.join(legacy_books, "ck_a.json"), '{"title":"A"}')
    write(os.path.join(legacy_books, "ck_b.json"), '{"title":"B"}')
    write(os.path.join(st.legacy_lan_dir(), "DDNovelReader", "v1", "meta.json"), '{"x":1}')

    res = st.migrate_legacy_lan_dir("")
    check("迁移成功", res.get("moved") is True, res)
    check("迁移文件数 = 3", res.get("files") == 3, res)
    check("旧目录已删除", not os.path.isdir(st.legacy_lan_dir()))
    check("新位置有全部文件",
          all(os.path.isfile(os.path.join(st.lan_dir(""), "DDNovelReader", "v1", "books", n))
              for n in ("ck_a.json", "ck_b.json")))
    check("内容一致", read(os.path.join(st.lan_dir(""), "DDNovelReader", "v1", "books", "ck_a.json"))
          == '{"title":"A"}')

    res2 = st.migrate_legacy_lan_dir("")
    check("二次迁移幂等（no-legacy）", res2.get("moved") is False and res2.get("reason") == "no-legacy", res2)

    # ---------- 2) 自定义缓存目录：迁移目标跟随 cache_dir ----------
    fresh()
    importlib.reload(st)
    custom = os.path.join(BASE, "D_drive_cache")
    os.makedirs(custom, exist_ok=True)
    write(os.path.join(st.legacy_lan_dir(), "DDNovelReader", "v1", "books", "ck_c.json"), '{"title":"C"}')
    res = st.migrate_legacy_lan_dir(custom)
    check("自定义缓存目录迁移成功", res.get("moved") is True, res)
    check("落到自定义目录下 <custom>/lan",
          os.path.isfile(os.path.join(custom, "lan", "DDNovelReader", "v1", "books", "ck_c.json")),
          os.path.join(custom, "lan"))
    check("自定义目录下迁移后旧目录删除", not os.path.isdir(st.legacy_lan_dir()))

    # ---------- 3) 冲突：新位置已有同名文件则保留新文件 ----------
    fresh()
    importlib.reload(st)
    write(os.path.join(st.legacy_lan_dir(), "DDNovelReader", "v1", "books", "ck_d.json"), "OLD")
    write(os.path.join(st.lan_dir(""), "DDNovelReader", "v1", "books", "ck_d.json"), "NEW")
    res = st.migrate_legacy_lan_dir("")
    got = read(os.path.join(st.lan_dir(""), "DDNovelReader", "v1", "books", "ck_d.json"))
    check("冲突时保留新位置的文件", got == "NEW", got)
    check("旧目录最终被清理", not os.path.isdir(st.legacy_lan_dir()), res)

    print("\n==== %d FAIL ====" % len(FAIL))
    shutil.rmtree(BASE, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
