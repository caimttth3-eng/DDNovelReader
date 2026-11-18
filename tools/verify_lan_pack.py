# -*- coding: utf-8 -*-
"""打包版局域网本地同步验证（v2.2.9）。

验证用户要求的三件事在**真实打包产物**里成立：
1) 软件启动 → 内嵌服务自动开服（端口可连）
2) 带配对码可读写（207），不带凭据被拒（401）
3) 关闭软件 → 服务停止、端口释放（不留后台监听）

用法： .venv\\Scripts\\python.exe _verify_lan_pack.py [onedir|onefile]
输出： _verify_lan_pack.txt
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import ctypes
from ctypes import wintypes

import psutil

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "_verify_lan_pack.txt")
LINES = []

TOKEN = "abc123def456"
USER = "duoduo"
LAN_DIR = "DDNovelReader"

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
GetWindowTextW = user32.GetWindowTextW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
PostMessageW = user32.PostMessageW
WM_CLOSE = 0x0010


def log(msg):
    LINES.append(str(msg))
    print(msg)


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def port_open(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def pids_of(root_pid):
    pids = {root_pid}
    try:
        pids |= {c.pid for c in psutil.Process(root_pid).children(recursive=True)}
    except Exception:
        pass
    return pids


def windows_of(pids):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            buf = ctypes.create_unicode_buffer(512)
            GetWindowTextW(hwnd, buf, 512)
            if buf.value.strip():
                found.append((hwnd, pid.value, buf.value))
        return True

    EnumWindows(cb, 0)
    return found


def basic_auth(token, user=USER):
    import base64
    return "Basic " + base64.b64encode(("%s:%s" % (user, token)).encode()).decode()


def propfind(port, token=None, depth="1"):
    url = "http://127.0.0.1:%d/%s/" % (port, LAN_DIR)
    body = ('<?xml version="1.0" encoding="utf-8"?>'
            '<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/>'
            "<D:getcontentlength/><D:getlastmodified/></D:prop></D:propfind>").encode()
    req = urllib.request.Request(url, data=body, method="PROPFIND",
                                 headers={"Depth": depth, "Content-Type": "application/xml"})
    if token:
        req.add_header("Authorization", basic_auth(token))
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:                                        # noqa: BLE001
        return None, str(e).encode()


def prepare_data(port):
    """用源码里的 Storage 造一份数据目录（含局域网设置）。"""
    data = os.path.join(ROOT, "_tmp_lan_pack", "data")
    shutil.rmtree(os.path.join(ROOT, "_tmp_lan_pack"), ignore_errors=True)
    os.makedirs(data, exist_ok=True)
    os.environ["DOUBAO_NOVEL_DATA"] = data
    from novelreader.storage import Storage
    st = Storage(path=os.path.join(data, "library.json"))
    for k, v in (("lan_enabled", True), ("lan_port", port), ("lan_token", TOKEN),
                 ("lan_user", USER), ("lan_dir", LAN_DIR), ("lan_auto_merge", True)):
        st.set_setting(k, v)
    return data


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "onedir"
    exe = (os.path.join(ROOT, "dist", "多多朗读", "多多朗读.exe") if mode == "onedir"
           else os.path.join(ROOT, "dist", "多多朗读.exe"))
    if not os.path.exists(exe):
        log("exe 不存在：%s" % exe)
        return 1
    port = free_port()
    data = prepare_data(port)
    envdir = os.path.join(ROOT, "_tmp_lan_pack", "env")
    os.makedirs(envdir, exist_ok=True)
    env = dict(os.environ)
    env.update({"TEMP": envdir, "TMP": envdir, "TMPDIR": envdir, "DOUBAO_NOVEL_DATA": data})
    log("== %s: %s（端口 %d）==" % (mode, exe, port))
    proc = subprocess.Popen([exe], env=env)

    ok = True
    # 1) 等端口打开
    t0 = time.time()
    up = False
    while time.time() - t0 < 90:
        if port_open(port):
            up = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    log("服务端口监听：%s（%.1fs）" % (up, time.time() - t0))
    ok &= up
    if not up:
        wins = windows_of(pids_of(proc.pid))
        log("进程存活=%s 窗口=%s" % (proc.poll() is None, wins))

    if up:
        code, body = propfind(port)
        log("无凭据 PROPFIND → %s（应 401）" % code)
        ok &= code == 401
        code, body = propfind(port, TOKEN)
        log("带配对码 PROPFIND → %s（应 207），响应 %d 字节" % (code, len(body or b"")))
        ok &= code == 207 and b"multistatus" in (body or b"")
        # 主窗口确实开着
        wins = [w for w in windows_of(pids_of(proc.pid)) if "多多朗读" in w[2]]
        log("主窗口：%s" % (wins[:2] or "未找到"))
        ok &= bool(wins)

    # 2) 关窗口 → 服务应停止、端口释放
    closed = False
    for hwnd, pid, title in windows_of(pids_of(proc.pid)):
        if "多多朗读" in title:
            PostMessageW(hwnd, WM_CLOSE, 0, 0)
            closed = True
            break
    if not closed:
        log("未找到主窗口，直接 kill")
        proc.kill()
    t1 = time.time()
    while time.time() - t1 < 30:
        if proc.poll() is not None and not port_open(port):
            break
        time.sleep(0.5)
    gone = proc.poll() is not None
    released = not port_open(port)
    log("关窗口后：进程退出=%s 端口释放=%s（%.1fs）" % (gone, released, time.time() - t1))
    ok &= released

    if not gone:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    log("RESULT %s %s" % (mode, "PASS" if ok else "FAIL"))
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
