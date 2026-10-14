# -*- coding: utf-8 -*-
"""CHM 文件读取模块：内置 chmlib（LGPL），纯 ctypes 封装，零第三方依赖。

三平台库文件（随发布包内置，位于软件根目录 / _MEIPASS）：
    Windows : chmlib.dll
    macOS   : libchmlib.dylib
    Linux   : libchmlib.so
"""
import ctypes
import os
import sys

CHM_MAX_PARAMLEN = 512
CHM_ENUMERATE_ALL = 31
CHM_ENUMERATOR_CONTINUE = 1
_CHUNK = 1 << 20  # 1MB 分块读取


def _library_candidates():
    """按平台返回库文件名候选（打包后与开发时的查找路径）。"""
    if sys.platform.startswith("win"):
        names = ["chmlib.dll"]
    elif sys.platform == "darwin":
        # 打包时以 .bin 名发布（PyInstaller 不把 .bin 当二进制处理，规避 mac 签名/校验失败）；开发时允许 dylib
        names = ["libchmlib.bin", "libchmlib.dylib"]
    else:
        names = ["libchmlib.so"]
    bases = []
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        bases.append(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
        bases.append(os.path.dirname(sys.executable))
    bases.append(os.path.dirname(os.path.abspath(__file__)))  # 开发时 novelreader/
    for base in bases:
        for n in names:
            p = os.path.join(base, n)
            if os.path.exists(p):
                return [p]
    return [os.path.join(b, n) for b in bases for n in names]


class _ChmUnitInfo(ctypes.Structure):
    _fields_ = [
        ("start", ctypes.c_int64),
        ("length", ctypes.c_int64),
        ("space", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("path", ctypes.c_char * (CHM_MAX_PARAMLEN + 1)),
    ]


class CHMError(Exception):
    pass


class CHMReader:
    """极简 CHM 读取器：仅提供 open / list_files / read_file / close。"""

    def __init__(self, path):
        self._lib = None
        self._h = None
        self._tmp_path = None
        self._load_library()
        h = self._lib.chm_open(self._open_bytes(path))
        if not h:
            raise CHMError("无法打开 CHM 文件（文件损坏或格式不受支持）")
        self._h = h
        self._path = path

    def _open_bytes(self, path):
        """chmlib 内部用 POSIX open()（ANSI 代码页路径）。

        Windows 下必须把路径编码为 ANSI 代码页（mbcs），否则中文路径
        会因 UTF-8 字节与代码页不符而打不开；无法表示时复制到 ASCII 临时路径兜底。
        """
        if os.name == "nt":
            try:
                return path.encode("mbcs")
            except UnicodeEncodeError:
                pass
            # 兜底：复制到 ASCII 临时路径（如生僻字/emoji 无法用 ANSI 表示）
            import shutil
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".chm", prefix="ddchm_")
            os.close(fd)
            shutil.copyfile(path, tmp)
            self._tmp_path = tmp
            return tmp.encode("ascii")
        return path.encode("utf-8", "replace")

    def _load_library(self):
        cands = _library_candidates()
        last_err = None
        for p in cands:
            try:
                lib = ctypes.CDLL(p)
                self._setup(lib)
                self._lib = lib
                return
            except OSError as e:
                last_err = e
        raise CHMError(f"未找到 chmlib 库文件（{cands[0]}）：{last_err}")

    def _setup(self, lib):
        lib.chm_open.restype = ctypes.c_void_p
        lib.chm_open.argtypes = [ctypes.c_char_p]
        lib.chm_close.restype = None
        lib.chm_close.argtypes = [ctypes.c_void_p]
        lib.chm_enumerate.restype = ctypes.c_int
        lib.chm_enumerate.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ]
        lib.chm_resolve_object.restype = ctypes.c_int
        lib.chm_resolve_object.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(_ChmUnitInfo),
        ]
        lib.chm_retrieve_object.restype = ctypes.c_size_t
        lib.chm_retrieve_object.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_ChmUnitInfo),
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int64,
        ]

    def close(self):
        if self._h:
            try:
                self._lib.chm_close(self._h)
            except Exception:
                pass
            self._h = None
        if self._tmp_path:
            try:
                os.remove(self._tmp_path)
            except OSError:
                pass
            self._tmp_path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def list_files(self):
        """枚举 CHM 内全部对象路径。"""
        if not self._h:
            raise CHMError("CHM 已关闭")
        out = []

        @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                          ctypes.POINTER(_ChmUnitInfo), ctypes.c_void_p)
        def _cb(_h, ui, _ctx):
            if ui:
                out.append(ui.contents.path.decode("utf-8", "replace"))
            return CHM_ENUMERATOR_CONTINUE

        self._lib.chm_enumerate(self._h, CHM_ENUMERATE_ALL, _cb, None)
        return out

    def read_file(self, obj_path):
        """读取 CHM 内一个对象（解压）的完整内容，返回 bytes。"""
        if not self._h:
            raise CHMError("CHM 已关闭")
        ui = _ChmUnitInfo()
        if self._lib.chm_resolve_object(self._h,
                                        obj_path.encode("utf-8", "replace"),
                                        ctypes.byref(ui)) != 0:
            raise CHMError(f"CHM 内找不到对象：{obj_path}")
        length = ui.length
        if length <= 0:
            return b""
        out = bytearray()
        pos = 0
        while pos < length:
            want = min(_CHUNK, length - pos)
            buf = ctypes.create_string_buffer(want)
            got = self._lib.chm_retrieve_object(self._h, ctypes.byref(ui),
                                                buf, pos, want)
            if got == 0 or got > want:
                break
            out += buf.raw[:got]
            pos += got
        return bytes(out)
