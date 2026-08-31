# -*- coding: utf-8 -*-
"""PyInstaller 运行时钩子：确保 tkinter 与 tcl/tk 在打包后可用。

本项目 venv 环境下 PyInstaller 模块分析器可能漏检 tkinter，
tkinter 整包及 tcl/tk 运行时由 spec 手动打包到 _MEIPASS 下，
此处确保 sys.path 与 TCL_LIBRARY / TK_LIBRARY 环境变量正确。
"""
import os
import sys

_base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

# 确保 _MEIPASS 在 sys.path 最前（tkinter 包作为 data 打包在此）
if _base not in sys.path:
    sys.path.insert(0, _base)

# 设置 Tcl/Tk 脚本库路径
for _name, _env, _marker in (
    ("tcl8.6", "TCL_LIBRARY", "init.tcl"),
    ("tk8.6", "TK_LIBRARY", "tk.tcl"),
):
    _cand = os.path.join(_base, _name)
    if os.path.isfile(os.path.join(_cand, _marker)):
        os.environ[_env] = _cand
