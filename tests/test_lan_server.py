# -*- coding: utf-8 -*-
"""局域网本地同步服务端自测（tests/test_lan_server.py）。

跑法： .venv\\Scripts\\python.exe tests\\test_lan_server.py
覆盖：鉴权 / CRUD / PROPFIND 深度 / MOVE 原子写 / Range / 路径穿越 / 脏标记 / 状态计数 / 重启。
"""
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from novelreader.lan_server import LanServer, ST_RUNNING, ST_STOPPED, new_token  # noqa: E402
from novelreader.sync_engine import WebDAVClient, WebDAVError                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "_tmp_lan_test")
FAIL = []
TOKEN = new_token()


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def fresh_root():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    return TMP


def raw(method, url, token=None, data=None, headers=None, user="duoduo"):
    hdrs = dict(headers or {})
    if token is not None:
        import base64
        hdrs["Authorization"] = "Basic " + base64.b64encode(
            ("%s:%s" % (user, token)).encode()).decode()
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    fresh_root()
    srv = LanServer(TMP, port=0, username="duoduo", password=TOKEN, remote_dir="DDNovelReader")
    st = srv.status()
    check("初始状态未运行", st["state"] == ST_STOPPED, st["state"])

    ok, err = srv.start()
    check("启动成功", ok, err)
    port = srv.status()["port"]
    check("端口已分配", port > 0, port)
    base = "http://127.0.0.1:%d/" % port
    cli = WebDAVClient(base, "duoduo", TOKEN, "DDNovelReader", verify_tls=False)

    # 1) 鉴权
    bad = WebDAVClient(base, "duoduo", "wrong-token", "DDNovelReader", verify_tls=False)
    try:
        bad.propfind("")
        code = None
    except WebDAVError as e:
        code = e.status
    check("错配对码被拒（401）", code == 401, code)
    code, _ = raw("GET", base + "DDNovelReader/v1/meta.json", token=None)
    check("未带凭据被拒（401）", code == 401, code)

    # 2) 建目录 + PUT/GET
    check("逐级建目录", cli.mkdirs("v1/books") is True)
    payload = json.dumps({"title": "测试书", "progress": {"percent": 12.5}},
                         ensure_ascii=False).encode("utf-8")
    check("原子写（.part + MOVE）", cli.put("v1/books/ck_test.json", payload) is True)
    got = cli.get("v1/books/ck_test.json")
    check("读回内容一致", got == payload, len(got or b""))
    check("读到不存在的文件返回 None", cli.get("v1/books/nope.json") is None)

    # 3) PROPFIND 深度
    items = cli.propfind("v1/books", depth="1")
    names = sorted(i["name"] for i in items)
    check("深度 1 列出文件", "ck_test.json" in names, names)
    one = [i for i in items if i["name"] == "ck_test.json"][0]
    check("清单带大小", one["size"] == len(payload), one)
    check("清单带 mtime", one["mtime"] > 0, one)
    check("深度 0 目录存在", cli.exists("v1/books") is True)
    check("深度 0 不存在目录为 False", cli.exists("v1/none") is False)
    top = sorted(i["name"] for i in cli.propfind("", depth="1"))
    check("根目录可见 v1", "v1" in top, top)

    # 4) 中文名 + 二进制（书源文件）
    bin_data = b"\x00\x01PK\x03\x04" + os.urandom(2000)
    check("中文文件名写入", cli.put("v1/sources/中文书名.epub", bin_data, atomic=False) is True)
    check("中文文件名读回", cli.get("v1/sources/中文书名.epub") == bin_data)
    names = sorted(i["name"] for i in cli.propfind("v1/sources", depth="1"))
    check("中文名出现在清单", "中文书名.epub" in names, names)

    # 5) Range 断点续传
    body = bytes(range(256)) * 4
    cli.put("v1/sources/range.bin", body, atomic=False)
    code, part = raw("GET", base + "DDNovelReader/v1/sources/range.bin", token=TOKEN,
                     headers={"Range": "bytes=10-19"})
    check("Range 返回 206", code == 206, code)
    check("Range 内容正确", part == body[10:20], part[:20])

    # 6) DELETE
    check("删除文件", cli.delete("v1/books/ck_test.json") is True)
    check("删除后读不到", cli.get("v1/books/ck_test.json") is None)
    check("删除不存在的返回 True（幂等）", cli.delete("v1/books/ck_test.json") is True)

    # 7) 路径穿越（两种写法都必须挡住）
    code, _ = raw("GET", base + "DDNovelReader/../../library.json", token=TOKEN)
    check("穿越 ../ 被拒", code in (400, 403, 404), code)
    code, _ = raw("GET", base + "DDNovelReader/%2e%2e/%2e%2e/library.json", token=TOKEN)
    check("编码穿越被拒", code in (400, 403, 404), code)
    code, _ = raw("PUT", base + "DDNovelReader/../../evil.json", token=TOKEN, data=b"x")
    check("穿越写被拒", code in (400, 403, 404), code)
    check("库文件未被写入", not os.path.exists(os.path.join(ROOT, "evil.json")))

    # 8) 脏标记（事件驱动合并的依据）
    srv.take_dirty()
    cli.put("v1/books/ck_dirty.json", b"{}")
    check("写入后置脏", srv.take_dirty() is True)
    check("取走后不再脏", srv.take_dirty() is False)

    # 9) 状态计数
    st = srv.status()
    check("状态为运行中", st["state"] == ST_RUNNING, st["state"])
    check("请求计数 > 10", st["requests"] > 10, st["requests"])
    check("活跃连接归零", st["active"] == 0, st["active"])
    check("有运行时长", st["uptime"] >= 0)
    check("配对信息四项齐全",
          all(srv.pairing_info().get(k) for k in ("url", "user", "pass", "dir")),
          srv.pairing_info())

    # 10) 停止 + 端口释放 + 可再次启动
    check("停止干净", srv.stop() is True)
    check("停止后状态", srv.status()["state"] == ST_STOPPED, srv.status()["state"])
    ok2, err2 = srv.start()
    check("可再次启动（端口已释放）", ok2, err2)
    srv.stop()

    # 11) 端口占用时给出可读错误
    srv2 = LanServer(TMP, port=srv.port, username="duoduo", password=TOKEN)
    srv2.start()
    busy_port = srv2.status()["port"]
    srv3 = LanServer(TMP, port=busy_port, username="duoduo", password=TOKEN)
    ok3, err3 = srv3.start()
    check("端口占用时启动失败并带提示", ok3 is False and "端口" in err3, (ok3, err3))
    srv2.stop()

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n==== %d FAIL ====" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
