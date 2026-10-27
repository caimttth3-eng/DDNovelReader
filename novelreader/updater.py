# -*- coding: utf-8 -*-
"""更新提示：启动时后台检查 GitHub 最新 Release，有新版本时弹窗提示（仅提示，不自动下载安装）。

用户自行决定是否更新：弹窗显示版本号与更新内容，提供「去下载 / 稍后 / 跳过此版本」。
跳过记入设置 skip_update_version，之后不再提示该版本。
三平台通用：检查走 urllib（标准库），打开链接 Windows 用 os.startfile、macOS 用 open、Linux 用 xdg-open。
"""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.request

from . import __version__ as _LOCAL_VERSION
from .i18n import T

_REPO = "caimttth3-eng/DDNovelReader"
_LATEST_URL = "https://api.github.com/repos/%s/releases/latest" % _REPO

# 当前更新弹窗实例（单例）：已存在时置顶复用，不重复新建
_dialog_win = None


def parse_version(v):
    """把版本串解析为可比较元组 (major, minor, patch)。

    兼容三段式（2.2.3 / v2.2.3）与历史格式（2.2v2 / 2.0V6c → 取数字前缀，(2,2,0)）。
    """
    nums = []
    for part in str(v).strip().lstrip("vV").split("."):
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def compare_ver(a, b):
    """a > b 返回 1；a == b 返回 0；a < b 返回 -1。"""
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)


def _fetch_latest():
    """拉取 GitHub 最新 Release 信息，返回 dict（tag_name/body/html_url）；失败抛异常。"""
    req = urllib.request.Request(_LATEST_URL, headers={"User-Agent": "DDNovelReader"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _open_url(url):
    """三平台通用打开浏览器链接。"""
    try:
        if os.name == "nt":
            os.startfile(url)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass


def start_check(root, storage):
    """后台线程检查更新；发现新版本且未被跳过时回主线程弹窗。网络失败静默，不影响启动。"""

    def _run():
        try:
            rel = _fetch_latest()
        except Exception:
            return
        try:
            latest_tag = rel.get("tag_name", "")
            if not latest_tag:
                return
            if compare_ver(latest_tag, _LOCAL_VERSION) <= 0:
                return  # 已是最新
            skip = storage.get_setting("skip_update_version", "") or ""
            if skip and parse_version(skip) == parse_version(latest_tag):
                return  # 用户跳过过该版本
            info = {
                "tag": latest_tag,
                "body": rel.get("body", "") or "",
                "url": rel.get("html_url", "") or ("https://github.com/%s/releases/latest" % _REPO),
            }
            try:
                root.after(0, lambda: _show_dialog(root, storage, info))
            except Exception:
                pass
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def check_update_manual(root, storage):
    """手动检查更新（关于窗口按钮）：有新版→弹更新窗；已是最新→提示；失败→提示原因。

    手动检查忽略 skip_update_version（用户主动点击就是要看结果）。
    """

    def _run():
        try:
            rel = _fetch_latest()
            latest_tag = rel.get("tag_name", "")
            if not latest_tag:
                raise RuntimeError("empty tag")
            if compare_ver(latest_tag, _LOCAL_VERSION) <= 0:
                root.after(0, lambda: _info(root, T("当前已是最新版本")))
                return
            info = {
                "tag": latest_tag,
                "body": rel.get("body", "") or "",
                "url": rel.get("html_url", "") or ("https://github.com/%s/releases/latest" % _REPO),
            }
            root.after(0, lambda: _show_dialog(root, storage, info))
        except Exception:
            root.after(0, lambda: _warn(root, T("无法连接 GitHub，请检查网络后重试")))

    threading.Thread(target=_run, daemon=True).start()


def _info(root, msg):
    try:
        from tkinter import messagebox
        messagebox.showinfo(T("提示"), msg, parent=root)
    except Exception:
        pass


def _warn(root, msg):
    try:
        from tkinter import messagebox
        messagebox.showwarning(T("检查更新失败"), msg, parent=root)
    except Exception:
        pass


def _show_dialog(root, storage, info):
    """更新提示弹窗：版本号 + 更新内容 + 去下载 / 稍后 / 跳过此版本。

    单例：若弹窗已存在且未关闭，则置顶聚焦已有窗口，不重复新建。
    """
    global _dialog_win
    if _dialog_win is not None:
        try:
            if _dialog_win.winfo_exists():
                try:
                    _dialog_win.lift()
                    _dialog_win.focus_set()
                except Exception:
                    pass
                return
        except Exception:
            pass
        _dialog_win = None

    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        return

    def _close_dialog():
        global _dialog_win
        _dialog_win = None
        try:
            win.destroy()
        except Exception:
            pass

    def _on_destroy(event):
        global _dialog_win
        # 仅当窗口本体被销毁时清引用（子控件销毁事件会级联触发，需按 widget 过滤）
        if event.widget is win and _dialog_win is win:
            _dialog_win = None

    win = tk.Toplevel(root)
    _dialog_win = win
    win.title(T("发现新版本"))
    win.resizable(True, True)
    try:
        win.transient(root)
    except Exception:
        pass
    try:
        win.protocol("WM_DELETE_WINDOW", _close_dialog)
    except Exception:
        pass
    try:
        win.bind("<Destroy>", _on_destroy)
    except Exception:
        pass

    pad = 14
    frame = tk.Frame(win, padx=pad, pady=pad)
    frame.pack(fill="both", expand=True)

    header = tk.Label(frame, text=T("发现新版本"), font=("微软雅黑", 13, "bold"))
    header.pack(anchor="w")

    info_txt = "%s  %s    |    %s  %s" % (
        T("最新版本"), info["tag"], T("当前版本"), _LOCAL_VERSION)
    tk.Label(frame, text=info_txt, font=("微软雅黑", 10), fg="#444444").pack(anchor="w", pady=(6, 2))

    tk.Label(frame, text=T("更新内容"), font=("微软雅黑", 10, "bold")).pack(anchor="w", pady=(8, 2))

    body = info["body"] or ""
    txt = scrolledtext.ScrolledText(frame, width=58, height=12, wrap="word", font=("微软雅黑", 10))
    txt.pack(fill="both", expand=True)
    txt.insert("1.0", body)
    txt.configure(state="disabled")

    btn_row = tk.Frame(frame)
    btn_row.pack(fill="x", pady=(12, 0))

    def _go():
        # 只打开下载页，弹窗保持打开，用户可继续点「稍后」或「跳过此版本」
        try:
            _open_url(info["url"])
        except Exception:
            pass

    def _skip():
        try:
            storage.set_setting("skip_update_version", info["tag"])
        except Exception:
            pass
        _close_dialog()

    tk.Button(btn_row, text=T("去下载"), width=12, command=_go).pack(side="left", padx=(0, 8))
    tk.Button(btn_row, text=T("稍后"), width=10, command=_close_dialog).pack(side="left", padx=(0, 8))
    tk.Button(btn_row, text=T("跳过此版本"), width=14, command=_skip).pack(side="left")

    # 居中于主窗口
    try:
        win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - win.winfo_width()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - win.winfo_height()) // 2
        win.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
    except Exception:
        pass
