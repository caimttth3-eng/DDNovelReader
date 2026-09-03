# -*- coding: utf-8 -*-
"""模块级常量与通用工具（从 gui.py 拆分，供各 Mixin 与基类共用，避免循环导入）。"""
import ctypes

THEMES = {
    "白天": {"bg": "#FFFFFF", "fg": "#222222", "hl": "#FFE28A"},
    "护眼": {"bg": "#F6EFE3", "fg": "#4A4A4A", "hl": "#F3C76A"},
    "夜间": {"bg": "#202124", "fg": "#C9C9C9", "hl": "#7A5C22"},
    "米黄": {"bg": "#DCD1B5", "fg": "#000000", "hl": "#E8C87A"},
}

# 控件主题（全控件UI风格，A-F六套）
UI_THEMES = {
    "A·米黄暖读": {
        "bg": "#FAF3E0", "btn": "#F5E6C8", "hover": "#EDD9A8", "pressed": "#E5CC8A",
        "fg": "#5C4A1F", "muted": "#8B7355", "accent": "#B8860B",
        "border": "#D4C4A0", "trough": "#E8DCC0", "field": "#FFFBF0",
        "selected": "#EDD9A8", "tab_bg": "#EDE0C4", "tab_active": "#FAF3E0",
        "slider": "#B8860B", "slider_active": "#9A6F0A",
    },
    "B·深色夜间": {
        "bg": "#2D2D30", "btn": "#3E3E42", "hover": "#4E4E52", "pressed": "#5A5A5E",
        "fg": "#E0E0E0", "muted": "#A0A0A0", "accent": "#007ACC",
        "border": "#4A4A4E", "trough": "#3A3A3D", "field": "#3E3E42",
        "selected": "#094771", "tab_bg": "#252526", "tab_active": "#2D2D30",
        "slider": "#007ACC", "slider_active": "#1A8AD5",
    },
    "C·清爽浅蓝": {
        "bg": "#F0F8FF", "btn": "#E8F4FD", "hover": "#D0E8F7", "pressed": "#B8DDF0",
        "fg": "#1A5276", "muted": "#5D8AA8", "accent": "#2980B9",
        "border": "#B8D4E8", "trough": "#D0E8F7", "field": "#FFFFFF",
        "selected": "#D0E8F7", "tab_bg": "#DCEEF8", "tab_active": "#F0F8FF",
        "slider": "#2980B9", "slider_active": "#1A6FA0",
    },
    "D·原生微调": {
        "bg": "#F5F6F8", "btn": "#F0F0F0", "hover": "#E5F1FB", "pressed": "#DCEBF7",
        "fg": "#1E1E1E", "muted": "#666666", "accent": "#4A90D9",
        "border": "#D0D0D0", "trough": "#E0E0E0", "field": "#FFFFFF",
        "selected": "#E5F1FB", "tab_bg": "#E8E8E8", "tab_active": "#FFFFFF",
        "slider": "#888888", "slider_active": "#666666",
    },
    "E·青绿科技": {
        "bg": "#F0FAF9", "btn": "#E0F2F1", "hover": "#B2DFDB", "pressed": "#80CBC4",
        "fg": "#004D40", "muted": "#4DB6AC", "accent": "#009688",
        "border": "#B2DFDB", "trough": "#C8E6C9", "field": "#FFFFFF",
        "selected": "#B2DFDB", "tab_bg": "#C8E6C9", "tab_active": "#F0FAF9",
        "slider": "#009688", "slider_active": "#00796B",
    },
    "F·豆沙暖粉": {
        "bg": "#FFF5F8", "btn": "#FCE4EC", "hover": "#F8BBD0", "pressed": "#F48FB1",
        "fg": "#880E4F", "muted": "#C2185B", "accent": "#E91E63",
        "border": "#F8BBD0", "trough": "#FCE4EC", "field": "#FFFFFF",
        "selected": "#F8BBD0", "tab_bg": "#F8BBD0", "tab_active": "#FFF5F8",
        "slider": "#E91E63", "slider_active": "#C2185B",
    },
}

FILE_TYPES = [
    ("支持的小说格式", "*.txt *.epub *.mobi *.azw3 *.pdf *.docx *.html *.htm *.zip"),
    ("文本文件", "*.txt"),
    ("电子书", "*.epub *.mobi *.azw3"),
    ("压缩包", "*.zip"),
    ("PDF 文档", "*.pdf"),
    ("Word 文档", "*.docx"),
    ("网页文件", "*.html *.htm"),
    ("所有文件", "*.*"),
]

_PREFERRED_FONTS = [
    "微软雅黑", "Microsoft YaHei UI", "宋体", "SimSun", "楷体", "KaiTi",
    "仿宋", "FangSong", "黑体", "SimHei", "Arial", "Consolas",
]

CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


def _copy_files_to_clipboard(paths):
    """把本地文件复制到 Windows 剪贴板（CF_HDROP），支持在资源管理器直接粘贴。

    返回是否成功。失败（如剪贴板被占用）时返回 False，不抛异常。
    """
    try:
        # DROPFILES 头：pFiles=20, pt=8, fNC=4, fWide=4
        header = (
            (20).to_bytes(4, "little")
            + (0).to_bytes(8, "little")
            + (0).to_bytes(4, "little")
            + (1).to_bytes(4, "little")
        )
        listing = ""
        for p in paths:
            listing += os.path.abspath(p) + "\0"
        listing += "\0"
        payload = header + listing.encode("utf-16-le")

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        # 64 位下句柄是指针，必须显式声明 restype/argtypes，否则会被截断
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(payload))
        if not h:
            return False
        try:
            ptr = kernel32.GlobalLock(h)
            ctypes.memmove(ptr, payload, len(payload))
            kernel32.GlobalUnlock(h)
            if not user32.OpenClipboard(None):
                return False
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(CF_HDROP, h):
                    return False
                h = None  # 所有权已移交系统，不再释放
            finally:
                user32.CloseClipboard()
        finally:
            if h:
                kernel32.GlobalFree(h)
        return True
    except Exception:
        return False
