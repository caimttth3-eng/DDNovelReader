# -*- coding: utf-8 -*-
"""多多朗读（DDNovelReader）入口。

负责：
- 自动定位并设置 Tcl/Tk 库路径（源码运行 & exe 打包两种模式）
- 创建主窗口并进入主循环
"""
import os
import sys


def _setup_tcltk():
    """为 tkinter 定位 Tcl/Tk 脚本库。本机 Python 为精简发行版时必需。

    Tcl9（Python 3.14）：脚本库内嵌于 tcl90.dll（zipfs），无需外部脚本库，
    若把 TCL_LIBRARY 指向 8.6 脚本库会造成版本冲突导致窗口无法创建。
    """
    # 检测 Tcl 版本：Tcl9 则清除并跳过外部脚本库（此判断必须在环境变量之前）
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        _tcl9 = os.path.isfile(os.path.join(base, "tcl90.dll")) or os.path.isfile(
            os.path.join(os.path.dirname(sys.executable), "tcl90.dll"))
    else:
        py = os.path.dirname(sys.executable)
        _tcl9 = os.path.isfile(os.path.join(py, "DLLs", "tcl90.dll")) or os.path.isfile(
            os.path.join(py, "tcl90.dll"))
    if _tcl9:
        os.environ.pop("TCL_LIBRARY", None)
        os.environ.pop("TK_LIBRARY", None)
        return
    if os.environ.get("TCL_LIBRARY") and os.path.exists(os.environ["TCL_LIBRARY"]):
        return
    candidates = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates = [
            os.path.join(base, "_tcl_data"),   # PyInstaller 运行时钩子收集的目录
            os.path.join(base, "tcl", "tcl8.6"),
            os.path.join(base, "tcl8.6"),
            os.path.join(base, "lib", "tcl8.6"),
        ]
    else:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(here, ".venv", "Lib", "tcl8.6"),
            os.path.join(here, "Lib", "tcl8.6"),
            os.path.join(here, "tcl", "tcl8.6"),
        ]
        py = os.path.dirname(sys.executable)
        candidates.append(os.path.join(py, "tcl", "tcl8.6"))
        candidates.append(os.path.join(py, "..", "tcl", "tcl8.6"))
    for c in candidates:
        if os.path.exists(os.path.join(c, "init.tcl")):
            os.environ["TCL_LIBRARY"] = c
            parent = os.path.dirname(c)
            for tkname in ("tk8.6", "tk8.6", "tk8.6"):
                tkdir = os.path.join(parent, tkname)
                if os.path.exists(os.path.join(tkdir, "tk.tcl")):
                    os.environ["TK_LIBRARY"] = tkdir
                    break
            return


def _enable_dpi_awareness():
    """启用 Windows 高 DPI 感知，保证在不同百分比缩放下文字清晰、控件布局正确。"""
    try:
        import ctypes
        try:
            # PROCESS_SYSTEM_DPI_AWARE：按系统缩放比渲染，tkinter 字号/布局随之缩放
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass


def _cleanup_stale_mei_dirs(max_age=86400):
    """清理 PyInstaller onefile 解压残留（_MEI* 目录）。

    单文件 exe 每次启动会把运行库解压到系统临时目录（_MEI 随机后缀）再运行；
    正常退出会自动删除，但强杀 / 崩溃 / 断电会残留。本函数在每次启动时静默清理
    超过 max_age 秒的旧残留，防止临时目录只增不减占用磁盘（方案 A，2026-10-15）。
    - 当前进程自己的解压目录（sys._MEIPASS）不删；
    - 正在被占用的目录（其他实例 / 其他软件仍在用）删除失败自动跳过；
    - 三平台通用（tempfile.gettempdir() 自动定位系统临时目录）；
    - 纯后台逻辑，无 UI 文案，不影响启动速度。
    """
    try:
        import shutil
        import tempfile
        import time
        tmp = tempfile.gettempdir()
        cur = getattr(sys, "_MEIPASS", "") or ""
        now = time.time()
        try:
            names = os.listdir(tmp)
        except Exception:
            return
        for name in names:
            if not name.startswith("_MEI"):
                continue
            p = os.path.join(tmp, name)
            try:
                if not os.path.isdir(p):
                    continue
                if cur and os.path.abspath(p) == os.path.abspath(cur):
                    continue  # 当前进程自己的解压目录
                if now - os.stat(p).st_mtime > max_age:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass  # 占用 / 权限问题一律跳过，不影响启动
    except Exception:
        pass


def main():
    def _dbg(*a):
        if os.environ.get("DD_DEBUG"):
            print("[main]", *a, flush=True)
    _dbg("m1 dpi")
    _enable_dpi_awareness()
    _dbg("m2 tcltk")
    _setup_tcltk()
    if getattr(sys, "frozen", False):
        BASE = os.path.dirname(sys.executable)
    else:
        BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if BASE not in sys.path:
            sys.path.insert(0, BASE)

    # 单实例：已有主程序在运行时，通知其还原窗口后本实例直接退出
    import queue as _queue

    from novelreader.single_instance import SingleInstance

    _dbg("m3 single")
    _si = SingleInstance()
    if not _si.acquired:
        if not _si.notify_existing():
            sys.stderr.write("DDNovelReader: single-instance port busy; cannot start a second instance.\n")
        return

    # 静默清理 PyInstaller 解压残留（后台线程，不阻塞启动）
    try:
        import threading
        threading.Thread(target=_cleanup_stale_mei_dirs, daemon=True).start()
    except Exception:
        pass

    _dbg("m4 import tk")
    import tkinter as tk

    from novelreader.gui import NovelReaderApp

    # 优先用 tkinterdnd2（原生拖拽，事件在主线程，稳定不闪退）
    # 不可用时降级为普通 Tk，拖拽功能不可用但不影响其他功能
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    _dbg("m5 root made")
    app = NovelReaderApp(root)
    _dbg("m6 app made")

    # 轮询单实例激活队列：收到请求就在主线程还原窗口（线程安全）
    _q = _queue.Queue()
    _si.start_listener(_q)

    def _poll_activate():
        try:
            while True:
                _q.get_nowait()
                app.restore_window()
        except _queue.Empty:
            pass
        try:
            root.after(200, _poll_activate)
        except Exception:
            pass

    root.after(200, _poll_activate)
    _dbg("m7 mainloop")
    root.mainloop()
    _dbg("m8 exited")


if __name__ == "__main__":
    main()
