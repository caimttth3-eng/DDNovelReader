# -*- coding: utf-8 -*-
"""PyInstaller 运行时钩子：确保 tkinter 与 tcl/tk 在打包后可用。

本项目 venv 环境下 PyInstaller 模块分析器可能漏检 tkinter，
tkinter 整包及 tcl/tk 运行时由 spec 手动打包到 _MEIPASS 下，
此处确保 sys.path 与 TCL_LIBRARY / TK_LIBRARY 环境变量正确。

Python 3.14（Tcl/Tk 9.0）：脚本库内嵌于 tcl90.dll（zipfs），
绝不能把 TCL_LIBRARY 指向外部 8.6 脚本库，否则版本冲突。
仅当打包为 Tcl8.6（Python 3.13 及更早）时才设置外部脚本库路径。
"""
import os
import sys

_base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

# 确保 _MEIPASS 在 sys.path 最前（tkinter 包作为 data 打包在此）
if _base not in sys.path:
    sys.path.insert(0, _base)

# Tcl9（Python 3.14）：脚本库内嵌在 tcl90.dll，不设置外部脚本库路径
_tcl9 = os.path.isfile(os.path.join(_base, "tcl90.dll")) or os.path.isfile(
    os.path.join(os.path.dirname(sys.executable), "tcl90.dll"))
if _tcl9:
    os.environ.pop("TCL_LIBRARY", None)
    os.environ.pop("TK_LIBRARY", None)
else:
    # Tcl8.6（Python 3.13 及更早）：设置外部脚本库路径
    for _name, _env, _marker in (
        ("tcl8.6", "TCL_LIBRARY", "init.tcl"),
        ("tk8.6", "TK_LIBRARY", "tk.tcl"),
    ):
        _cand = os.path.join(_base, _name)
        if os.path.isfile(os.path.join(_cand, _marker)):
            os.environ[_env] = _cand
