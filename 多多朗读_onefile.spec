# -*- mode: python ; coding: utf-8 -*-
"""多多朗读 PyInstaller 打包配置 —— onefile（单文件 exe）版。

与 多多朗读.spec（onedir）并存：本文件打包产物为单文件 exe，
每次启动解压到 Temp/_MEIxxx（正常退出自删，强杀/崩溃会残留，
软件启动自清理会在下次启动时全量清掉）。老用户习惯单文件交付，
两版都发布（方案 C，2026-10-18 用户确认保留 exe 版）。
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
_datas = [('assets/app.ico', 'assets'), ('assets/skins', 'assets/skins'), ('assets/icons', 'assets/icons'), ('assets/donate_alipay.png', 'assets'), ('assets/donate_wechat.png', 'assets')]
_tkdnd_dir = os.path.join(_sp, 'tkinterdnd2', 'tkdnd', 'win-x64')
if os.path.isdir(_tkdnd_dir):
    _datas.append((_tkdnd_dir, 'tkinterdnd2/tkdnd/win-x64'))
if _tkinter_pkg:
    _datas.append((_tkinter_pkg, 'tkinter'))
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
_chm_dll = os.path.join(os.getcwd(), "novelreader", "chmlib.dll")
if os.path.isfile(_chm_dll):
    _binaries.append((_chm_dll, '.'))
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
    'docx', 'lxml', 'lxml.etree', 'lxml.objectify', 'lxml.html',
]

# --- excludes：排除用不到的标准库/测试模块 ---
# 注意：不能排除 pygame——_trim_edge_silence 用 pygame 解码 mp3 裁剪句首尾静音
#（2026-10-19 起 Windows 版也依赖，句间停顿修复）
_excludes = [
    'unittest', 'pydoc', 'doctest', 'idlelib', 'lib2to3',
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

# onefile 模式：EXE 直接收 binaries/datas，产物为单文件 exe
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
