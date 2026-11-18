# -*- coding: utf-8 -*-
"""GUI 接线冒烟（v2.2.9 局域网本地同步）：真 App（窗口 withdraw）。

覆盖：随软件启停 / 状态快照 / 书架「一键同步」按钮存在 / 数据同步弹窗两页签 /
脏标记看门狗触发合并不崩 / 关闭软件后服务停止且端口释放。

跑法： .venv\\Scripts\\python.exe tests\\test_lan_gui_wiring.py
"""
import io
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

# 先把界面语言写进设置：CI 机器是英文系统，App 启动时会按系统语言自动选英文，
# 而本测试用中文文案断言，所以必须在建 App 之前把 ui_lang 预置成 zh（App 会尊重已存设置）。
from novelreader.storage import Storage as _SeedStorage          # noqa: E402

_SeedStorage(path=os.path.join(TMP, "library.json")).set_setting("ui_lang", "zh")

import tkinter as tk                                              # noqa: E402

from novelreader.gui import NovelReaderApp
from novelreader.lan_server import ST_RUNNING, ST_STOPPED          # noqa: E402
from novelreader.sync_ui import _RUN_LOCK, _SyncDlg                # noqa: E402

# 统一 UTF-8 输出：Windows 控制台默认 cp1252，直接 print 中文会 UnicodeEncodeError
try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


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

    # 6) 缓存管理窗口：新增「同步数据（局域网）」卡片 + 迁移按钮 + 路径跟随缓存目录
    app._show_cache_manager_dialog()
    root.update_idletasks()
    cache_dlg = None
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            try:
                if w.winfo_exists() and "缓存管理" in w.title():
                    cache_dlg = w
                    break
            except Exception:
                pass
    check("缓存管理窗口已打开", cache_dlg is not None)

    def _walk(widget, acc):
        try:
            acc.append(widget)
            for c in widget.winfo_children():
                _walk(c, acc)
        except Exception:
            pass
        return acc

    texts = []
    if cache_dlg is not None:
        for w in _walk(cache_dlg, []):
            try:
                t = w.cget("text")
                if t:
                    texts.append(str(t))
            except Exception:
                pass
    check("缓存管理有「同步数据（局域网）」卡片", any("同步数据（局域网）" in t for t in texts),
          texts[:12])
    check("卡片显示路径在缓存目录下",
          any(("cache" + os.sep + "lan") in t for t in texts), [t for t in texts if "lan" in t][:3])
    check("卡片有「迁移旧数据」按钮", any(t == "迁移旧数据" for t in texts))
    check("卡片标注位置跟随缓存目录", any("跟随缓存目录" in t for t in texts))
    check("有「一键清理全部缓存」按钮", any(t == "一键清理全部缓存" for t in texts))
    check("三张卡片齐全",
          all(any(k in t for t in texts) for k in ("正文解析缓存", "音频缓存", "同步数据（局域网）")),
          [t for t in texts if "缓存" in t][:8])
    if cache_dlg is not None:
        try:
            cache_dlg.destroy()
        except Exception:
            pass
    root.update_idletasks()

    # 6b) 旧位置还有待迁移文件时：卡片要提示；迁移后数字要能自己变对（回归"占用显示 0"）
    #     注意：应用启动后 after(2600) 会自动迁移，这里先把自动迁移临时挡住，保证"待迁移"状态可观测。
    real_migrate = app._lan_migrate_legacy
    app._lan_migrate_legacy = lambda *a, **k: {"moved": False, "reason": "test-disabled"}
    legacy_dir = os.path.join(TMP, "lan", "DDNovelReader", "v1", "books")
    os.makedirs(legacy_dir, exist_ok=True)
    with open(os.path.join(legacy_dir, "ck_pending.json"), "w", encoding="utf-8") as f:
        f.write('{"title":"pending"}')

    def _pump(seconds=4.0):
        end = time.time() + seconds
        while time.time() < end:
            try:
                root.update()
            except Exception:
                break
            time.sleep(0.05)

    def _lan_size_texts(dlg):
        out = []
        for w in _walk(dlg, []):
            try:
                t = str(w.cget("text") or "")
            except Exception:
                continue
            if "占用" in t or "待迁移" in t or "正在统计" in t:
                out.append(t)
        return out

    app._show_cache_manager_dialog()
    root.update_idletasks()
    dlg2 = None
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            try:
                if w.winfo_exists() and "缓存管理" in w.title():
                    dlg2 = w
                    break
            except Exception:
                pass
    _pump(4.0)
    texts2 = _lan_size_texts(dlg2) if dlg2 else []
    check("旧位置有待迁移文件时给出提示", any("待迁移" in t for t in texts2), texts2)

    app._lan_migrate_legacy = real_migrate
    app._lan_migrate_legacy(quiet=True)
    if dlg2 is not None:
        try:
            dlg2.destroy()
        except Exception:
            pass
    app._show_cache_manager_dialog()
    root.update_idletasks()
    dlg3 = None
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            try:
                if w.winfo_exists() and "缓存管理" in w.title():
                    dlg3 = w
                    break
            except Exception:
                pass
    _pump(4.0)
    texts3 = _lan_size_texts(dlg3) if dlg3 else []
    check("迁移后占用不再是 0（显示实际体积）",
          any(("占用" in t) and ("0 B" not in t) for t in texts3), texts3)
    if dlg3 is not None:
        try:
            dlg3.destroy()
        except Exception:
            pass
    root.update_idletasks()

    # 7) 看门狗：置脏后触发一次合并不崩、锁最终释放
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
