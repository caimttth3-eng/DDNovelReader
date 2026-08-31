# -*- coding: utf-8 -*-
"""多多朗读（DDNovelReader）入口。

负责：
- 自动定位并设置 Tcl/Tk 库路径（源码运行 & exe 打包两种模式）
- 创建主窗口并进入主循环
"""
import os
import sys


def _setup_tcltk():
    """为 tkinter 定位 Tcl/Tk 脚本库。本机 Python 为精简发行版时必需。"""
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


def main():
    _enable_dpi_awareness()
    _setup_tcltk()
    if getattr(sys, "frozen", False):
        BASE = os.path.dirname(sys.executable)
    else:
        BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if BASE not in sys.path:
            sys.path.insert(0, BASE)

    import tkinter as tk

    from novelreader.gui import NovelReaderApp

    root = tk.Tk()
    NovelReaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
