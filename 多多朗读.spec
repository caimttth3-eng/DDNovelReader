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
# Python 3.14 用 Tcl9.0（tcl90.dll/tcl9tk90.dll，位于 base DLLs）；
# venv site-packages 可能遗留 Tcl8.6 DLL（tcl86t.dll/tk86t.dll），与 3.14 的 _tkinter.pyd 不匹配，必须优先 Tcl9。
_tkinter_pyd = None
_tcl_dll = None
_tk_dll = None
for _d in (_sp, os.path.join(_base_py, "DLLs")):
    if not _tkinter_pyd and os.path.isfile(os.path.join(_d, "_tkinter.pyd")):
        _tkinter_pyd = os.path.join(_d, "_tkinter.pyd")
# Tcl9（Python 3.14）优先，其次 Tcl8.6（Python 3.13 兼容）
for _d in (os.path.join(_base_py, "DLLs"), _sp):
    if not _tcl_dll:
        for _n in ("tcl90.dll", "tcl9.dll"):
            if os.path.isfile(os.path.join(_d, _n)):
                _tcl_dll = os.path.join(_d, _n)
                break
    if not _tk_dll:
        for _n in ("tcl9tk90.dll", "tk90.dll"):
            if os.path.isfile(os.path.join(_d, _n)):
                _tk_dll = os.path.join(_d, _n)
                break
if not _tcl_dll or not _tk_dll:
    for _d in (_sp, os.path.join(_base_py, "DLLs")):
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
_datas = [('assets/app.ico', 'assets'), ('assets/skins', 'assets/skins'), ('assets/icons', 'assets/icons')]
# tkinterdnd2 的 tkdnd 二进制（只打包 win-x64，减小包体）
_tkdnd_dir = os.path.join(_sp, 'tkinterdnd2', 'tkdnd', 'win-x64')
if os.path.isdir(_tkdnd_dir):
    _datas.append((_tkdnd_dir, 'tkinterdnd2/tkdnd/win-x64'))
if _tkinter_pkg:
    _datas.append((_tkinter_pkg, 'tkinter'))
# Tcl9（Python 3.14）脚本库内嵌于 tcl90.dll，无需外部脚本库；仅 Tcl8.6 场景打包
_is_tcl9 = _tcl_dll and os.path.basename(_tcl_dll).startswith("tcl9")
if _is_tcl9:
    _tcl_scripts = _tk_scripts = None
if _tcl_scripts:
    _datas.append((_tcl_scripts, 'tcl8.6'))
if _tk_scripts:
    _datas.append((_tk_scripts, 'tk8.6'))

# --- 组装 binaries（二进制/DLL）---
_binaries = []
for _b in (_tkinter_pyd, _tcl_dll, _tk_dll):
    if _b and os.path.isfile(_b):
        _binaries.append((_b, '.'))
# chmlib.dll：CHM 支持（方案 B 内置库，随包分发到 _MEIPASS 根目录）
# 注：PyInstaller exec spec 时无 __file__，用 os.getcwd()（spec 在项目根执行）
_chm_dll = os.path.join(os.getcwd(), "novelreader", "chmlib.dll")
if os.path.isfile(_chm_dll):
    _binaries.append((_chm_dll, '.'))
# Tcl9（Python 3.14）附加依赖：_tkinter.pyd/tcl90.dll 需要 libtommath.dll、zlib1.dll
# PyInstaller 不会自动收集，必须手动打包，否则运行时 DLL load failed
for _extra in ("libtommath.dll", "zlib1.dll", "libbz2.dll", "liblzma.dll", "libffi-8.dll"):
    _p = os.path.join(_base_py, "DLLs", _extra)
    if os.path.isfile(_p):
        _binaries.append((_p, '.'))

# --- hiddenimports（双保险）---
_hiddenimports = [
    'tkinter', 'tkinter.font', 'tkinter.ttk', 'tkinter.filedialog',
    'tkinter.messagebox', 'tkinter.scrolledtext', 'tkinter.colorchooser',
    'tkinter.simpledialog', 'tkinter.dnd', '_tkinter',
    'pyttsx3.drivers', 'pyttsx3.drivers.sapi5',
    'edge_tts', 'aiohttp',
    'gtts', 'gtts.tts',
    'pycaw', 'pycaw.pycaw', 'pycaw.api', 'comtypes', 'psutil',
    'tkinterdnd2',
    # python-docx 依赖 lxml（C 扩展），PyInstaller 偶发漏收集，
    # 显式强制收集，否则打包后 .docx 书籍解析功能不可用
    'docx', 'lxml', 'lxml.etree', 'lxml.objectify', 'lxml.html',
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
    [],
    exclude_binaries=True,
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

# onedir 模式（方案 B，2026-10-18）：运行库固定放在 exe 旁 _internal 目录，
# 启动时不再每次解压到 Temp/_MEIxxx，二次启动秒开，根治低配/机械硬盘用户「打开就卡」。
# 交付物为文件夹：dist\多多朗读\（多多朗读.exe + _internal\）。
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='多多朗读',
)
