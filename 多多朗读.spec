# -*- mode: python ; coding: utf-8 -*-
"""多多朗读 PyInstaller 打包配置。

本项目 venv 环境下 PyInstaller 模块分析器可能漏检 tkinter，因此：
1. 手动把 tkinter 整包作为 data 打包到 _MEIPASS/tkinter/
2. 手动打包 _tkinter.pyd / tcl86t.dll / tk86t.dll
3. 手动打包 tcl8.6 / tk8.6 脚本库
4. 运行时钩子 runtime_hook_tkinter.py 确保 sys.path 与 TCL_LIBRARY/TK_LIBRARY 正确
"""
import os
import sys

# --- 动态定位各资源 ---
_base_py = getattr(sys, "base_prefix", sys.prefix)
_venv = sys.prefix
_sp = os.path.join(_venv, "Lib", "site-packages")

# tkinter 包目录（优先 base Python 的 Lib）
_tkinter_pkg = None
for _cand in (
    os.path.join(_base_py, "Lib", "tkinter"),
    os.path.join(_sp, "tkinter"),
):
    if os.path.isfile(os.path.join(_cand, "__init__.py")):
        _tkinter_pkg = _cand
        break

# _tkinter.pyd + tcl/tk DLL（优先 site-packages，其次 base DLLs）
_tkinter_pyd = None
_tcl_dll = None
_tk_dll = None
for _d in (_sp, os.path.join(_base_py, "DLLs")):
    if not _tkinter_pyd and os.path.isfile(os.path.join(_d, "_tkinter.pyd")):
        _tkinter_pyd = os.path.join(_d, "_tkinter.pyd")
    if not _tcl_dll and os.path.isfile(os.path.join(_d, "tcl86t.dll")):
        _tcl_dll = os.path.join(_d, "tcl86t.dll")
    if not _tk_dll and os.path.isfile(os.path.join(_d, "tk86t.dll")):
        _tk_dll = os.path.join(_d, "tk86t.dll")

# tcl8.6 / tk8.6 脚本库
_tcl_scripts = None
_tk_scripts = None
for _root in (_venv, _base_py):
    for _sub in ("Lib", "lib", ""):
        _ct = os.path.join(_root, _sub, "tcl8.6")
        _ck = os.path.join(_root, _sub, "tk8.6")
        if not _tcl_scripts and os.path.isfile(os.path.join(_ct, "init.tcl")):
            _tcl_scripts = _ct
        if not _tk_scripts and os.path.isfile(os.path.join(_ck, "tk.tcl")):
            _tk_scripts = _ck

# --- 组装 datas（数据文件）---
_datas = [('assets/app.ico', 'assets'), ('assets/skins', 'assets/skins')]
# tkinterdnd2 的 tkdnd 二进制（只打包 win-x64，减小包体）
_tkdnd_dir = os.path.join(_sp, 'tkinterdnd2', 'tkdnd', 'win-x64')
if os.path.isdir(_tkdnd_dir):
    _datas.append((_tkdnd_dir, 'tkinterdnd2/tkdnd/win-x64'))
if _tkinter_pkg:
    _datas.append((_tkinter_pkg, 'tkinter'))
if _tcl_scripts:
    _datas.append((_tcl_scripts, 'tcl8.6'))
if _tk_scripts:
    _datas.append((_tk_scripts, 'tk8.6'))

# --- 组装 binaries（二进制/DLL）---
_binaries = []
for _b in (_tkinter_pyd, _tcl_dll, _tk_dll):
    if _b and os.path.isfile(_b):
        _binaries.append((_b, '.'))

# --- hiddenimports（双保险）---
_hiddenimports = [
    'tkinter', 'tkinter.font', 'tkinter.ttk', 'tkinter.filedialog',
    'tkinter.messagebox', 'tkinter.scrolledtext', 'tkinter.colorchooser',
    'tkinter.simpledialog', 'tkinter.dnd', '_tkinter',
    'pyttsx3.drivers', 'pyttsx3.drivers.sapi5',
    'edge_tts', 'aiohttp',
    'pycaw', 'pycaw.pycaw', 'pycaw.api', 'comtypes', 'psutil',
    'tkinterdnd2',
]

# --- excludes：排除用不到的标准库/测试模块，进一步瘦身 ---
_excludes = [
    'pygame', 'unittest', 'pydoc', 'doctest', 'idlelib', 'lib2to3',
    'sqlite3', 'ensurepip', 'venv', 'wsgiref', 'xmlrpc',
    'tkinter.test', 'ctypes.test', 'test',
]

a = Analysis(
    ['novelreader/main.py'],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_tkinter.py'],
    excludes=_excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='多多朗读',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
