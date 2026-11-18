# -*- coding: utf-8 -*-
"""GUI 接线冒烟（v2.2.9 局域网本地同步）：真 App（窗口 withdraw）。

覆盖：随软件启停 / 状态快照 / 书架「一键同步」按钮存在 / 数据同步弹窗两页签 /
脏标记看门狗触发合并不崩 / 关闭软件后服务停止且端口释放。

跑法： .venv\\Scripts\\python.exe tests\\test_lan_gui_wiring.py
"""
import os
import shutil
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "_tmp_lan_gui")
shutil.rmtree(TMP, ignore_errors=True)      # 每轮干净起步（否则上一轮保存的 lan_enabled 会残留）
os.makedirs(TMP, exist_ok=True)
os.environ["DOUBAO_NOVEL_DATA"] = TMP

import tkinter as tk                                              # noqa: E402

from novelreader.gui import NovelReaderApp                        # noqa: E402
from novelreader.lan_server import ST_RUNNING, ST_STOPPED          # noqa: E402
from novelreader.sync_ui import _RUN_LOCK, _SyncDlg                # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_open(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def main():
    os.makedirs(TMP, exist_ok=True)
    port = free_port()
    root = tk.Tk()
    root.withdraw()
    app = NovelReaderApp(root)
    root.update_idletasks()

    # 1) 默认关闭：不应自动开服
    check("默认未开启局域网同步", bool(app.settings.get("lan_enabled", False)) is False,
          app.settings.get("lan_enabled"))
    check("默认已有配对码（12 位）", len(str(app.settings.get("lan_token") or "")) == 12,
          app.settings.get("lan_token"))
    check("未开启时状态为已停止", app._lan_status().get("state") == ST_STOPPED,
          app._lan_status().get("state"))

    # 2) 开启后随软件启动
    app.settings["lan_enabled"] = True
    app.settings["lan_port"] = port
    app.storage.set_setting("lan_enabled", True)
    app.storage.set_setting("lan_port", port)
    app._lan_auto_start()
    st = app._lan_status()
    check("开启后服务运行中", st.get("state") == ST_RUNNING, st.get("state"))
    check("监听端口正确", st.get("port") == port, st.get("port"))
    check("端口真的在监听", port_open(port) is True)
    info = app._lan_srv.pairing_info()
    check("配对信息四项齐全",
          bool(info.get("urls")) and bool(info.get("user")) and bool(info.get("pass"))
          and bool(info.get("dir")), info)
    check("配对信息地址含端口", str(info.get("urls")[0]).endswith(":%d/" % port), info.get("urls"))

    # 3) 状态里有可显示的监控字段
    st = app._lan_status()
    for key in ("state", "port", "uptime", "requests", "active", "last_request", "last_error"):
        check("状态字段 %s 存在" % key, key in st, sorted(st))

    # 4) 书架「一键同步」按钮已加
    check("书架一键同步按钮存在", hasattr(app, "btn_shelf_sync"))
    check("按钮文字为一键同步", str(app.btn_shelf_sync.cget("text")) == "一键同步",
          app.btn_shelf_sync.cget("text"))

    # 5) 数据同步弹窗：两页签 + 局域网控件 + 副标题已修正
    dlg = _SyncDlg(app)
    root.update_idletasks()
    tabs = [dlg.nb.tab(i, "text") for i in range(dlg.nb.index("end"))]
    check("弹窗有两个页签", len(tabs) == 2, tabs)
    check("页签含云端与局域网",
          any("WebDAV" in t for t in tabs) and any("局域网" in t or "LAN" in t for t in tabs), tabs)
    check("弹窗有服务状态标签", hasattr(dlg, "lbl_lan"))
    check("弹窗有配对信息框", hasattr(dlg, "txt_pair"))
    pair_text = dlg.txt_pair.get("1.0", "end")
    check("配对信息含账号与目录", "duoduo" in pair_text and "DDNovelReader" in pair_text, pair_text[:80])
    check("配对信息含端口", str(port) in pair_text, pair_text[:120])
    dlg._collect()
    check("收集设置不报错且端口归一", int(app.settings.get("lan_port")) == port)
    dlg._refresh_lan()
    lan_text = dlg.lbl_lan.cget("text")
    check("状态文本已渲染", ("端口" in lan_text) and ("运行" in lan_text) and ("请求" in lan_text),
          lan_text[:80])
    dlg._on_close()
    root.update_idletasks()

    # 6) 看门狗：置脏后触发一次合并不崩、锁最终释放
    app._lan_srv.take_dirty()
    app._lan_srv.mark_dirty()
    app._lan_watch_tick()
    deadline = time.time() + 8
    released = False
    while time.time() < deadline:
        if not _RUN_LOCK.locked():
            released = True
            break
        time.sleep(0.2)
    check("脏标记触发合并后锁已释放", released, "lock still held")
    check("触发合并不影响服务运行", app._lan_status().get("state") == ST_RUNNING)

    # 7) 关软件即关服务器 + 端口释放
    app._on_close()
    check("关闭后服务已停止", app._lan_status().get("state") == ST_STOPPED,
          app._lan_status().get("state"))
    check("关闭后端口已释放", port_open(port) is False)

    print("\n==== %d FAIL ====" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
