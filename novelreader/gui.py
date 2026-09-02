# -*- coding: utf-8 -*-
"""多多朗读（DDNovelReader）主界面：书架 / 阅读区 / 字体调节 / 总进度 / TTS 朗读。"""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

from . import __version__, book_loader
from .storage import (
    Storage,
    cache_dir,
    tts_cache_dir,
    resolve_tts_cache_dir,
    resolve_cache_dir,
    dir_size,
    audio_cache_dirs,
)
from .tts_engine import SpeechController

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


class NovelReaderApp:
    def __init__(self, root):
        self.root = root
        self.storage = Storage()
        self.tts = SpeechController()
        self.tts.set_tts_cache_dir(self._effective_tts_cache_root())

        self.current_bid = None
        self.book = None          # BookContent
        self.chapter_idx = 0
        self.char_offset = 0
        self._title_char_len = 0       # 章节标题+换行的字符数，用于位置换算
        self._body_start_line = 1      # 正文在显示文本中的起始行号
        self.settings = dict(self.storage.settings())
        self._save_timer = None
        self._cache = {}
        self._chapter_sel_busy = False
        self._scrollbars = []
        self._highlight_index = None

        self._build_ui()
        self._apply_settings_to_ui()
        self._refresh_bookshelf()
        self._bind_shortcuts()

        # 定时轮询 TTS 事件（工作线程 → UI）
        self.root.after(100, self._poll_tts)
        # 全屏悬浮条每秒刷新
        self.root.after(1000, self._tick_overlay)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 启动时自动打开上次阅读的书
        if self.settings.get("auto_open_last", True):
            last = self.storage.get_setting("last_book")
            if last and last in self.storage.all_books():
                self.open_book(last)
        else:
            self.root.title(f"多多朗读 v{__version__}")
        self._refresh_bookshelf()

    # ================= UI 构建 =================
    def _icon_path(self):
        """解析程序图标路径（优先用户自定义皮肤图标，其次默认 app.ico）。"""
        try:
            # 用户自定义图标（exe 模式下可写）
            custom = os.path.join(os.environ.get("APPDATA", ""), "DDNovelReader", "custom.ico")
            if os.path.exists(custom):
                return custom
            if getattr(sys, "frozen", False):
                base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(base, "assets", "app.ico")
            return p if os.path.exists(p) else None
        except Exception:
            return None

    def _skins_dir(self):
        """皮肤图标目录（源码 / exe 打包两种模式）。"""
        try:
            if getattr(sys, "frozen", False):
                base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            d = os.path.join(base, "assets", "skins")
            return d if os.path.isdir(d) else None
        except Exception:
            return None

    def _current_skin_png(self):
        """当前图标对应的 PNG 路径（用于关于界面显示）。"""
        d = self._skins_dir()
        if d:
            p = os.path.join(d, "icon5_1.png")
            if os.path.exists(p):
                return p
        return None

    def _default_geometry(self):
        """按当前 DPI 缩放默认窗口尺寸，兼容 Windows 百分比缩放（125%/150%…）。"""
        try:
            dpi = self.root.winfo_fpixels("1i")
            k = max(1.0, min(2.0, dpi / 96.0))
            return f"{int(1180 * k)}x{int(760 * k)}"
        except Exception:
            return "1180x760"

    # ---------- 缓存目录 / 通用工具 ----------
    def _effective_tts_cache_root(self):
        """当前生效的整本语音缓存根目录（优先自定义，空则默认）。

        直接读 storage 设置，避免依赖 self.settings 的初始化顺序。
        """
        custom = self.storage.get_setting("tts_cache_dir") or ""
        return resolve_tts_cache_dir(custom)

    def _audio_cache_size(self, bid):
        """某本书的音频缓存总字节数（跨语音/语速目录）。"""
        try:
            root = self._effective_tts_cache_root()
            return sum(dir_size(d) for d in audio_cache_dirs(root, bid))
        except Exception:
            return 0

    def _book_total_cache_size(self, bid):
        """文本解析缓存 + 音频缓存的总字节数（书架显示用）。"""
        try:
            total = 0
            cp = self.storage.cache_path(bid)
            if cp and os.path.exists(cp):
                total += os.path.getsize(cp)
            total += self._audio_cache_size(bid)
            return total
        except Exception:
            return 0

    # ---------- 书架缓存大小异步计算（避免大量书籍/音频缓存时UI卡死） ----------
    def _init_shelf_size_cache(self):
        if not hasattr(self, "_shelf_size_cache"):
            self._shelf_size_cache = {}  # bid -> size_bytes
            self._shelf_size_worker = None
            self._shelf_size_stop = threading.Event()

    def _refresh_shelf_sizes_async(self):
        """后台线程计算书架中所有书籍的缓存大小，完成后逐行更新 Treeview。"""
        self._init_shelf_size_cache()
        # 如果已有 worker 在运行，先停止
        if self._shelf_size_worker and self._shelf_size_worker.is_alive():
            self._shelf_size_stop.set()
            self._shelf_size_worker.join(timeout=1)
        self._shelf_size_stop.clear()

        books = self.storage.all_books()
        # 仅对缺失持久化缓存大小的书（老数据迁移）做一次后台校准；其余零扫描
        bids = [k for k in books if not self.storage.has_cache_size(k)]
        if not bids:
            return

        def worker():
            for bid in bids:
                if self._shelf_size_stop.is_set():
                    return
                try:
                    sz = self._book_total_cache_size(bid)
                    self.storage.set_book_cache_size(bid, sz)
                    self._shelf_size_cache[bid] = sz
                    # 通过 root.after 回到主线程更新 UI
                    self.root.after(0, lambda b=bid, s=sz: self._update_shelf_size_cell(b, s))
                except Exception:
                    pass

        self._shelf_size_worker = threading.Thread(target=worker, daemon=True)
        self._shelf_size_worker.start()

    def _update_shelf_size_cell(self, bid, size):
        """更新 Treeview 中某本书的大小列。"""
        try:
            if not self.shelf_tree.exists(bid):
                return
            vals = list(self.shelf_tree.item(bid, "values"))
            if len(vals) >= 3:
                vals[2] = self._format_bytes(size) if size else "-"
                self.shelf_tree.item(bid, values=vals)
        except Exception:
            pass

    def _delete_audio_cache(self, bid):
        """删除某本书的全部音频缓存目录。"""
        try:
            root = self._effective_tts_cache_root()
            import shutil
            for d in audio_cache_dirs(root, bid):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.storage.set_book_cache_size(bid, 0)
            self._shelf_size_cache[bid] = 0
        except Exception:
            pass

    def _apply_ui_theme(self, name):
        """应用控件主题（A-F六套），统一全控件风格。name 为 UI_THEMES 的键。"""
        c = UI_THEMES.get(name, UI_THEMES["D·原生微调"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        df = ("微软雅黑", 9)

        # TButton
        style.configure("TButton", font=df, padding=(8, 5),
                        background=c["btn"], foreground=c["fg"],
                        bordercolor=c["border"], relief="flat", borderwidth=1)
        style.map("TButton",
                  background=[("active", c["hover"]), ("pressed", c["pressed"])],
                  bordercolor=[("active", c["accent"])],
                  foreground=[("disabled", c["muted"])])

        # TCombobox
        style.configure("TCombobox", font=df, fieldbackground=c["field"],
                        background=c["btn"], arrowcolor=c["fg"],
                        bordercolor=c["border"], padding=(4, 3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["field"]), ("focus", c["field"])],
                  background=[("active", c["hover"])])

        # TProgressbar
        style.configure("TProgressbar", troughcolor=c["trough"], background=c["accent"],
                        bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"],
                        thickness=14)

        # Treeview
        style.configure("Treeview", font=df, rowheight=26,
                        background=c["field"], foreground=c["fg"],
                        fieldbackground=c["field"], bordercolor=c["border"])
        style.configure("Treeview.Heading", font=("微软雅黑", 9, "bold"),
                        background=c["btn"], foreground=c["fg"],
                        bordercolor=c["border"], padding=(4, 4))
        style.map("Treeview",
                  background=[("selected", c["selected"])],
                  foreground=[("selected", c["fg"])])
        style.map("Treeview.Heading",
                  background=[("active", c["hover"])])

        # TNotebook
        style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
        style.configure("TNotebook.Tab", font=df, padding=(12, 5),
                        background=c["tab_bg"], foreground=c["fg"])
        style.map("TNotebook.Tab",
                  background=[("selected", c["tab_active"]), ("active", c["hover"])],
                  foreground=[("selected", c["fg"])])

        # TPanedwindow
        style.configure("TPanedwindow", background=c["border"], sashwidth=4, sashrelief="flat")

        # TSeparator
        style.configure("TSeparator", background=c["border"])

        # TScale（阅读进度滑块）
        style.configure("Seek.Horizontal.TScale", troughcolor=c["trough"],
                        background=c["slider"], bordercolor=c["border"],
                        lightcolor=c["slider"], darkcolor=c["slider_active"])
        style.map("Seek.Horizontal.TScale",
                  background=[("active", c["slider_active"])])
        style.configure("TScale", troughcolor=c["trough"], background=c["slider"])

        # TLabel/TFrame/TEntry
        style.configure("TLabel", font=df, foreground=c["fg"])
        style.configure("TFrame", background=c["bg"])
        style.configure("TEntry", font=df, fieldbackground=c["field"],
                        bordercolor=c["border"], padding=(4, 3))

        # TCheckbutton/TRadiobutton
        style.configure("TCheckbutton", font=df, background=c["field"], foreground=c["fg"])
        style.map("TCheckbutton", background=[("active", c["hover"])])
        style.configure("TRadiobutton", font=df, background=c["field"], foreground=c["fg"])

        # tk 原生控件
        self.root.option_add("*Menu.Font", df)
        self.root.option_add("*Menu.activeBackground", c["hover"])
        self.root.option_add("*Menu.activeForeground", c["fg"])
        self.root.option_add("*Menu.background", c["field"])
        self.root.option_add("*Menu.foreground", c["fg"])
        self.root.option_add("*Menu.relief", "flat")
        self.root.option_add("*Listbox.font", df)
        self.root.option_add("*Listbox.selectBackground", c["selected"])
        self.root.option_add("*Listbox.selectForeground", c["fg"])
        self.root.option_add("*Listbox.background", c["field"])
        self.root.option_add("*Listbox.foreground", c["fg"])
        self.root.option_add("*Listbox.selectForeground", c["fg"])
        self.root.option_add("*Button.foreground", c["fg"])
        self.root.option_add("*Checkbutton.font", df)
        self.root.option_add("*Checkbutton.activeBackground", c["hover"])
        self.root.option_add("*Checkbutton.background", c["field"])
        self.root.option_add("*Scrollbar.troughColor", c["trough"])
        self.root.option_add("*Scrollbar.background", c["btn"])
        self.root.option_add("*Scrollbar.activeBackground", c["hover"])
        # 全局默认背景（对之后创建的弹窗生效）
        self.root.option_add("*Frame.background", c["bg"])
        self.root.option_add("*Label.background", c["bg"])
        self.root.option_add("*Button.background", c["bg"])
        self.root.option_add("*Toplevel.background", c["bg"])

        # 主窗口背景
        try:
            self.root.configure(bg=c["bg"])
        except Exception:
            pass

        # 递归设置所有已创建的 tk 容器背景（Frame/Label/Button/Toplevel）
        _exclude = set()
        for _attr in ("_overlay",):
            _w = getattr(self, _attr, None)
            if _w is not None:
                try:
                    _exclude.add(str(_w))
                except Exception:
                    pass

        def _set_bg(w, color):
            try:
                if str(w) in _exclude:
                    return  # 跳过全屏 overlay 及其子控件
                wt = w.winfo_class()
                if wt in ("Frame", "Label", "Button", "Toplevel"):
                    w.configure(bg=color)
                if wt == "Button":
                    # tk.Button（非 ttk）文字色跟随主题
                    try:
                        w.configure(fg=c["fg"])
                    except Exception:
                        pass
                if wt == "Listbox":
                    # 目录列表：背景+文字色+选中色
                    try:
                        w.configure(bg=c["field"], fg=c["fg"],
                                    selectbackground=c["selected"], selectforeground=c["fg"])
                    except Exception:
                        pass
            except Exception:
                pass
            for ch in w.winfo_children():
                _set_bg(ch, color)
        try:
            _set_bg(self.root, c["bg"])
        except Exception:
            pass

        self.settings["ui_theme"] = name
        self.storage.set_setting("ui_theme", name)

    def _center_window(self, win):
        """把弹窗移动到屏幕正中间（不改变窗口大小）。"""
        try:
            import re as _re
            win.update_idletasks()
            geo = win.geometry()
            m = _re.match(r"(\d+)x(\d+)", geo)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
            else:
                w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        self.root.title(f"多多朗读 v{__version__}")
        self.root.geometry(self.settings.get("window_geometry") or self._default_geometry())
        icon = self._icon_path()
        if icon:
            try:
                self.root.iconbitmap(icon)
            except Exception:
                pass
        self._apply_ui_theme(self.settings.get("ui_theme", "A·米黄暖读"))

        # ---- 主体直接占满（书架下移到内层 PanedWindow，与目录/阅读区同层，避免挤占工具条） ----
        main = tk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=6, pady=6)
        # 用 grid 布局：工具条(0,0) / 阅读+书架+目录(1,0) / 状态栏(2,0)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        self._main = main

        # 工具条
        self._build_toolbar(main)

        # 阅读区 + 书架 + 目录 用内层 PanedWindow（可拖拽调节宽度）
        self._inner_paned = ttk.Panedwindow(main, orient="horizontal")
        self._inner_paned.grid(row=1, column=0, sticky="nsew", pady=(6, 2))

        # 左侧：书架
        left = tk.Frame(self._inner_paned, width=280)
        self._left = left

        shelf_header = tk.Button(left, text="📚 我的书架", font=("微软雅黑", 11, "bold"),
                                  relief="flat", cursor="hand2", anchor="w",
                                  command=self._open_cache_folder)
        shelf_header.pack(fill="x")

        shelf_frame = tk.Frame(left)
        shelf_frame.pack(fill="both", expand=True, pady=(4, 4))
        # 书架用 Treeview（详细信息视图）
        cols = ("progress", "title", "size", "time")
        self.shelf_tree = ttk.Treeview(shelf_frame, columns=cols, show="headings", selectmode="extended")
        self.shelf_tree.heading("progress", text="进度", command=lambda: self._shelf_sort("progress"))
        self.shelf_tree.heading("title", text="书名", command=lambda: self._shelf_sort("title"))
        self.shelf_tree.heading("size", text="大小", command=lambda: self._shelf_sort("size"))
        self.shelf_tree.heading("time", text="时间", command=lambda: self._shelf_sort("time"))
        self.shelf_tree.column("progress", width=55, anchor="center", stretch=False)
        self.shelf_tree.column("title", width=160, anchor="w", stretch=True)
        self.shelf_tree.column("size", width=65, anchor="e", stretch=False)
        self.shelf_tree.column("time", width=80, anchor="center", stretch=False)
        sb = self._make_scrollbar(shelf_frame, self.shelf_tree.yview)
        self.shelf_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.shelf_tree.pack(side="left", fill="both", expand=True)
        self.shelf_tree.bind("<Double-Button-1>", lambda e: self._open_selected())
        self.shelf_tree.bind("<Return>", lambda e: self._open_selected())
        self.shelf_tree.bind("<Button-3>", self._popup_shelf_menu)
        # 拖拽导入：拖文件到书架区或主窗口任意位置即可导入
        self._setup_drag_drop()
        self._shelf_sort_col = "time"
        self._shelf_sort_rev = True

        # 书架右键菜单
        self.shelf_menu = tk.Menu(self.root, tearoff=0)
        self.shelf_menu.add_command(label="打开书籍", command=self._open_selected)
        self.shelf_menu.add_separator()
        self.shelf_menu.add_command(label="添加书籍", command=self._add_book)
        self.shelf_menu.add_separator()
        self.shelf_menu.add_command(label="复制原文件", command=self._copy_book_file)
        self.shelf_menu.add_command(label="复制书名", command=self._copy_book_title)
        self.shelf_menu.add_separator()
        self.shelf_menu.add_command(label="删除（保留缓存）", command=lambda: self._remove_book(False))
        self.shelf_menu.add_command(label="删除文件（清空缓存）", command=lambda: self._remove_book(True))

        btn_row = tk.Frame(left)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="＋ 添加书籍", command=self._add_book).pack(fill="x", pady=2)
        self._shelf_visible = False

        read_frame = tk.Frame(self._inner_paned)
        self._inner_paned.add(read_frame, weight=1)

        self.text = tk.Text(
            read_frame,
            wrap="word",
            undo=False,
            padx=30,
            pady=24,
            relief="flat",
            cursor="arrow",
        )
        tscroll = self._make_scrollbar(read_frame, self.text.yview)
        self.text.configure(yscrollcommand=tscroll.set)
        tscroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(state="disabled")
        self.text.tag_configure("tts", background="#F3C76A", foreground="#000000")

        self.text.bind("<MouseWheel>", self._on_scroll)
        self.text.bind("<ButtonRelease-1>", self._on_scroll)
        self.text.bind("<KeyRelease>", self._on_scroll)
        self.text.bind("<Configure>", self._on_text_resize)
        self.text.bind("<Button-4>", self._on_scroll)   # 部分滚轮
        self.text.bind("<Button-5>", self._on_scroll)
        self.text.bind("<Button-3>", self._popup_text_menu)

        # 阅读区右键菜单
        self.text_menu = tk.Menu(self.root, tearoff=0)
        self.text_menu.add_command(label="复制", command=self._copy_selection)
        self.text_menu.add_command(label="从该段开始朗读", command=self._read_from_paragraph)
        self.text_menu.add_separator()
        self.text_menu.add_command(label="百度搜索", command=lambda: self._search_selection("baidu"))
        self.text_menu.add_command(label="谷歌搜索", command=lambda: self._search_selection("google"))
        self.text_menu.add_command(label="必应搜索", command=lambda: self._search_selection("bing"))
        self.text_menu.add_separator()
        self.text_menu.add_command(label="翻译", command=lambda: self._search_selection("translate"))

        # 目录（内层 PanedWindow 的第二个窗格，可开关）
        self.chapter_panel = tk.Frame(self._inner_paned, width=220)
        self.chapter_list = tk.Listbox(self.chapter_panel, font=("微软雅黑", 10))
        csb = self._make_scrollbar(self.chapter_panel, self.chapter_list.yview)
        self.chapter_list.configure(yscrollcommand=csb.set)
        csb.pack(side="right", fill="y")
        self.chapter_list.pack(side="left", fill="both", expand=True)
        self.chapter_list.bind("<<ListboxSelect>>", self._on_chapter_pick)
        self._chapter_panel_visible = False
        # 目录默认不加入 PanedWindow（隐藏），由 _toggle_toc 控制 add/remove

        # 底部状态栏：可拖动进度滑块（跳转）+ 百分比 + 章节位置 + 总字数
        status = tk.Frame(main)
        status.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._seek_style = "Seek.Horizontal.TScale"
        self._ttk_style = ttk.Style(self.root)
        self._ttk_style.configure(self._seek_style, troughcolor="#d8d8d8", background="#000000")
        self.progress_var = tk.DoubleVar(value=0)
        self._seeking = False
        self.progress_scale = ttk.Scale(
            status, from_=0, to=100, variable=self.progress_var,
            command=self._on_seek, style=self._seek_style,
        )
        self.progress_scale.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress_scale.bind("<ButtonPress-1>", self._on_seek_press)
        self.progress_scale.bind("<ButtonRelease-1>", self._on_seek_release)
        self.percent_label = tk.Label(status, text="0.0%", width=8, cursor="hand2")
        self.percent_label.pack(side="left")
        self.percent_label.bind("<Button-1>", lambda e: self._open_percent_dialog())
        self.pos_label = tk.Label(status, text="", width=20, anchor="e")
        self.pos_label.pack(side="left", padx=(6, 0))
        # 右下角：TTS 语音缓存进度（如 30/30）
        self.tts_cache_label = tk.Label(status, text="", fg="#3366aa", font=("微软雅黑", 9))
        self.tts_cache_label.pack(side="right", padx=(0, 12))
        # 右下角：整本语音缓存进度（如 整本缓存 120/500，可点击暂停/继续）
        self.book_cache_label = tk.Label(status, text="", fg="#8a5a00", font=("微软雅黑", 9), cursor="hand2")
        self.book_cache_label.pack(side="right", padx=(0, 12))
        self.book_cache_label.bind("<Button-1>", self._on_status_cache_click)
        self.total_label = tk.Label(status, text="", anchor="e")
        self.total_label.pack(side="right")

        # ---- 全屏模式顶部信息条（占用工具条那一行，正文从下方开始，不与文字重叠） ----
        self._fullscreen = False
        self._read_seconds = 0
        # 阅读时间改为按「本次软件启动」后台累计（每次开启软件从 0 开始）
        self._app_start = time.time()
        self._last_second = int(time.time())
        # 定时停止朗读（分钟）
        self._timer_minutes = 0
        self._timer_deadline = None
        self._timer_running = False
        self._overlay = tk.Frame(main, bg="#202124", highlightthickness=1, highlightbackground="#444444")
        self._ov_chap = tk.Label(self._overlay, text="", fg="#d0d0d0", bg="#202124",
                                 font=("微软雅黑", 10))
        self._ov_chap.pack(side="left", padx=14, pady=6)
        self._ov_prog = tk.Label(self._overlay, text="", fg="#f0c36d", bg="#202124",
                                 font=("微软雅黑", 10, "bold"))
        self._ov_prog.pack(side="right", padx=14, pady=6)
        self._ov_read = tk.Label(self._overlay, text="", fg="#9fc5ff", bg="#202124",
                                 font=("微软雅黑", 10))
        self._ov_read.pack(side="right", padx=(0, 14), pady=6)
        self._ov_time = tk.Label(self._overlay, text="", fg="#e8e8e8", bg="#202124",
                                 font=("微软雅黑", 10, "bold"))
        self._ov_time.pack(side="right", padx=(0, 8), pady=6)

    def _build_toolbar(self, parent):
        bar = tk.Frame(parent)
        bar.grid(row=0, column=0, sticky="ew")
        self._toolbar = bar

        # 统一小字灰色标签，避免与正文主题混淆
        def _lbl(owner, text, fg="#6a6a6a"):
            tk.Label(owner, text=text, font=("微软雅黑", 9), fg=fg).pack(side="left")

        # ---- 第 1 行：书名 + 章节导航（左） / 书架、目录、关于（右） ----
        row1 = tk.Frame(bar)
        row1.pack(fill="x", pady=(3, 1))
        self.title_label = tk.Label(row1, text="未打开书籍", font=("微软雅黑", 13, "bold"))
        self.title_label.pack(side="left", padx=(0, 4))
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=4)
        ttk.Button(row1, text="◀ 上一章", width=8,
                   command=lambda: self._goto_chapter(self.chapter_idx - 1)).pack(side="left", padx=1)
        self.chapter_cb = ttk.Combobox(row1, state="readonly", width=24)
        self.chapter_cb.pack(side="left", padx=1)
        self.chapter_cb.bind("<<ComboboxSelected>>", self._on_chapter_cb)
        ttk.Button(row1, text="下一章 ▶", width=8,
                   command=lambda: self._goto_chapter(self.chapter_idx + 1)).pack(side="left", padx=1)
        right1 = tk.Frame(row1)
        right1.pack(side="right")
        ttk.Button(right1, text="书架", command=self._toggle_shelf).pack(side="left", padx=2)
        self.toc_btn = ttk.Button(right1, text="目录", command=self._toggle_toc)
        self.toc_btn.pack(side="left", padx=2)
        ttk.Separator(right1, orient="vertical").pack(side="left", fill="y", padx=4)
        ttk.Button(right1, text="关于", command=self._show_about).pack(side="left", padx=2)

        # ---- 第 2 行：排版外观 ----
        row2 = tk.Frame(bar)
        row2.pack(fill="x", pady=1)
        _lbl(row2, "排版", fg="#999999")
        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=6)
        _lbl(row2, "字体")
        self.font_cb = ttk.Combobox(row2, state="readonly", width=14)
        self.font_cb.pack(side="left", padx=(2, 8))
        self.font_cb.bind("<<ComboboxSelected>>", self._on_font_change)

        _lbl(row2, "字号")
        ttk.Button(row2, text="－", width=2, command=lambda: self._change_font_size(-1)).pack(side="left", padx=1)
        self.size_label = tk.Label(row2, text="17", width=3, font=("微软雅黑", 10))
        self.size_label.pack(side="left")
        ttk.Button(row2, text="＋", width=2, command=lambda: self._change_font_size(1)).pack(side="left", padx=(1, 8))

        _lbl(row2, "行距")
        ttk.Button(row2, text="－", width=2, command=lambda: self._change_line_spacing(-0.1)).pack(side="left", padx=1)
        self.spacing_label = tk.Label(row2, text="1.5", width=3, font=("微软雅黑", 10))
        self.spacing_label.pack(side="left")
        ttk.Button(row2, text="＋", width=2, command=lambda: self._change_line_spacing(0.1)).pack(side="left", padx=(1, 8))

        _lbl(row2, "空行")
        self.paragraph_cb = ttk.Combobox(
            row2, state="readonly", width=9,
            values=["不压缩", "合并为一行", "清理所有行"],
        )
        self.paragraph_cb.pack(side="left", padx=(2, 8))
        self.paragraph_cb.bind("<<ComboboxSelected>>", self._on_paragraph_mode)

        _lbl(row2, "书页")
        self.theme_cb = ttk.Combobox(row2, state="readonly", values=list(THEMES.keys()), width=5)
        self.theme_cb.pack(side="left", padx=(2, 0))
        self.theme_cb.bind("<<ComboboxSelected>>", self._on_theme_change)

        # ---- 第 3 行：朗读控制 ----
        row3 = tk.Frame(bar)
        row3.pack(fill="x", pady=(1, 3))
        _lbl(row3, "朗读", fg="#999999")
        ttk.Separator(row3, orient="vertical").pack(side="left", fill="y", padx=6)
        self.tts_toggle_btn = ttk.Button(row3, text="▶ 开始朗读", command=self._tts_toggle)
        self.tts_toggle_btn.pack(side="left")
        self.tts_stop_btn = ttk.Button(row3, text="⏹ 结束", command=self._tts_stop, state="disabled")
        self.tts_stop_btn.pack(side="left", padx=4)
        self.tts_status_label = tk.Label(row3, text="", fg="#c0392b")
        self.tts_status_label.pack(side="left", padx=(6, 0))
        self._status_timer = None

        ttk.Separator(row3, orient="vertical").pack(side="left", fill="y", padx=8)
        _lbl(row3, "语速")
        ttk.Button(row3, text="－", width=2, command=lambda: self._change_rate(-10)).pack(side="left", padx=1)
        self.rate_label = tk.Label(row3, text="200", width=3, font=("微软雅黑", 10))
        self.rate_label.pack(side="left")
        ttk.Button(row3, text="＋", width=2, command=lambda: self._change_rate(10)).pack(side="left", padx=(1, 8))

        _lbl(row3, "停顿")
        ttk.Button(row3, text="－", width=2, command=lambda: self._change_sentence_gap(-0.05)).pack(side="left", padx=1)
        self.gap_label = tk.Label(row3, text="0.10", width=4, font=("微软雅黑", 10))
        self.gap_label.pack(side="left")
        ttk.Button(row3, text="＋", width=2, command=lambda: self._change_sentence_gap(0.05)).pack(side="left", padx=(1, 8))

        # ---- 第 4 行：音量/语音/缓存/定时 ----
        row4 = tk.Frame(bar)
        row4.pack(fill="x", pady=(0, 3))
        _lbl(row4, "音量")
        self.volume_var = tk.DoubleVar(value=100)
        self.volume_scale = ttk.Scale(row4, from_=0, to=100, variable=self.volume_var,
                                       command=self._on_volume_change, length=110)
        self.volume_scale.pack(side="left", padx=(2, 4))
        self.volume_label = tk.Label(row4, text="100", width=4, font=("微软雅黑", 10))
        self.volume_label.pack(side="left", padx=(0, 8))

        _lbl(row4, "语音")
        self.voice_cb = ttk.Combobox(row4, state="readonly", width=24)
        self.voice_cb.pack(side="left", padx=(2, 0))
        self.voice_cb.bind("<<ComboboxSelected>>", self._on_voice_change)
        self.voice_cb["values"] = self._friendly_voices()

        # 整本语音缓存（Edge 神经语音）
        self.tts_cache_btn = ttk.Button(row4, text="整本缓存", width=8, command=self._open_cache_dialog)
        self.tts_cache_btn.pack(side="left", padx=(10, 0))

        # 定时停止朗读（分钟）
        self.timer_btn = ttk.Button(row4, text="定时", width=8, command=self._open_timer_dialog)
        self.timer_btn.pack(side="left", padx=(4, 0))

        # 工具条与阅读区之间的分隔线
        ttk.Separator(bar, orient="horizontal").pack(fill="x", pady=(1, 0))

    # ================= 设置 =================
    def _apply_settings_to_ui(self):
        self.font_cb["values"] = self._sorted_fonts()
        fam = self.settings.get("font_family")
        if fam not in self.font_cb["values"]:
            fam = "微软雅黑" if "微软雅黑" in self.font_cb["values"] else self.font_cb["values"][0]
        self.font_cb.set(fam)
        self.settings["font_family"] = fam
        self.size_label.configure(text=str(self.settings.get("font_size", 17)))
        self.spacing_label.configure(text=f"{self.settings.get('line_spacing', 1.5):.1f}")
        theme = self.settings.get("theme", "护眼")
        if theme not in THEMES:
            theme = "护眼"
        self.theme_cb.set(theme)
        self.rate_label.configure(text=str(self.settings.get("tts_rate", 200)))
        self.tts.set_rate(self.settings.get("tts_rate", 200))
        self.tts.set_sentence_gap(self.settings.get("tts_sentence_gap", 0.10))
        self.gap_label.configure(text=f"{float(self.settings.get('tts_sentence_gap', 0.10)):.2f}")
        _vol = int(self.settings.get("volume", 100))
        self.volume_var.set(_vol)
        self.volume_label.configure(text=str(_vol))
        self.tts.set_volume(_vol)
        pm = int(self.settings.get("paragraph_mode", 1))
        if 1 <= pm <= 3:
            self.paragraph_cb.current(pm - 1)
        voice = self.settings.get("tts_voice")
        voices = list(self.voice_cb["values"])
        idx = -1
        if voice and getattr(self, "_voice_ids", None):
            try:
                idx = self._voice_ids.index(voice)
            except ValueError:
                idx = -1
        if idx >= 0:
            self.voice_cb.current(idx)
            self.tts.set_voice(self._voice_ids[idx])
        elif voices:
            # 默认使用第一个本地（系统）语音
            idx = getattr(self, "_first_sapi_idx", 0)
            self.voice_cb.current(idx)
            self.tts.set_voice(self._voice_ids[idx])
        self._apply_theme(theme)

    def _sorted_fonts(self):
        fams = list(tkfont.families(self.root))
        ordered = [f for f in _PREFERRED_FONTS if f in fams]
        rest = [f for f in fams if f not in _PREFERRED_FONTS]
        return ordered + rest

    def _friendly_voices(self):
        # 默认本地（系统语音，离线可用）；Edge 语音作为可选（需联网）
        edge = SpeechController.list_edge_voices()
        sapi = SpeechController.list_voices()
        self._voice_ids = []
        names = []
        for short, label in edge:
            self._voice_ids.append(short)
            names.append(f"Edge·{label} | {short}")
        self._first_sapi_idx = len(self._voice_ids)
        for v in sapi:
            self._voice_ids.append(v)
            name = v.split("\\")[-1] if "\\" in v else v
            names.append(f"本地·{name} | {v}")
        return names

    def _apply_theme(self, theme):
        t = THEMES[theme]
        self.text.configure(bg=t["bg"], fg=t["fg"], insertbackground=t["fg"])
        self.text.tag_configure("tts", background=t["hl"])
        self._apply_scrollbar_theme(theme)
        self._apply_font()

    # ---- 黑色滚动条滑块 ----
    def _make_scrollbar(self, parent, command, orient="vertical"):
        sb = tk.Scrollbar(
            parent, orient=orient, command=command,
            highlightthickness=0, borderwidth=0, width=14, relief="flat",
        )
        self._scrollbars.append(sb)
        return sb

    def _apply_scrollbar_theme(self, theme):
        dark = theme == "夜间"
        # 滑块黑色（夜间用近黑色，保证在深色轨道上可见）
        thumb = "#1f1f1f" if dark else "#000000"
        trough = "#4a4a4a" if dark else "#d8d8d8"
        for sb in getattr(self, "_scrollbars", []):
            try:
                sb.configure(bg=thumb, activebackground=thumb, troughcolor=trough)
            except Exception:
                pass
        # 底部进度滑块同步配色
        try:
            self._ttk_style.configure(
                self._seek_style, troughcolor=trough, background=thumb
            )
        except Exception:
            pass

    def _apply_font(self):
        fam = self.settings.get("font_family", "微软雅黑")
        size = int(self.settings.get("font_size", 17))
        ls = float(self.settings.get("line_spacing", 1.5))
        indent = round(size * 2.0) if self.settings.get("first_line_indent", True) else 0
        self.text.configure(font=(fam, size))
        self.text.tag_configure(
            "body",
            spacing1=0,
            spacing2=max(2, round(size * 0.32)),
            spacing3=max(3, round(size * 0.45 * ls)),
            lmargin1=indent,
            lmargin2=0,
        )
        # 章节标题样式：比正文大4号、加粗、居中、段后间距大
        self.text.tag_configure(
            "chapter_title",
            font=(fam, size + 4, "bold"),
            justify="center",
            spacing1=round(size * 0.5),
            spacing3=round(size * 1.2),
            lmargin1=0,
            lmargin2=0,
        )
        self._tag_body()
        self._repin_reading()

    def _apply_paragraph_mode(self, content):
        """压缩空行：1 不压缩（每段一行）/ 2 合并为一行（段间一个空行）/ 3 清理所有行（全文一行）。"""
        mode = int(self.settings.get("paragraph_mode", 1))
        if mode == 3:
            return content.replace("\n", "")
        paras = content.split("\n")
        if mode == 2:
            return "\n\n".join(paras)
        return content

    def _tag_body(self):
        try:
            # 只给正文部分加 body 标签，标题保留 chapter_title 样式
            body_start = f"{self._body_start_line}.0"
            self.text.tag_remove("body", "1.0", "end")
            self.text.tag_add("body", body_start, "end")
        except Exception:
            pass

    # ================= 书架 =================
    def _refresh_bookshelf(self):
        self._init_shelf_size_cache()
        # 保存当前选中
        sel_bids = set()
        try:
            for iid in self.shelf_tree.selection():
                sel_bids.add(iid)
        except Exception:
            pass
        self.shelf_tree.delete(*self.shelf_tree.get_children())
        books = self.storage.all_books()
        rows = []
        for b in books.values():
            prog = b.get("progress") or {}
            pct = prog.get("percent", 0)
            # 优先内存实时值，其次持久化值；缺失（老数据）显示…，由后台一次性校准
            bid_i = b["id"]
            csize = self._shelf_size_cache.get(bid_i)
            if csize is None:
                csize = self.storage.book_cache_size(bid_i)
            if csize is None:
                csize = 0
                size_s = "…"
            else:
                size_s = self._format_bytes(csize) if csize else "-"
            ts = b.get("added_at", b.get("last_read_at", 0))
            try:
                time_s = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "-"
            except Exception:
                time_s = "-"
            rows.append({
                "id": b["id"],
                "progress": pct,
                "title": b.get("title", "未命名"),
                "size": csize,
                "size_s": size_s,
                "time": ts,
                "time_s": time_s,
            })
        # 排序
        col = getattr(self, "_shelf_sort_col", "time")
        rev = getattr(self, "_shelf_sort_rev", True)
        key_map = {"progress": "progress", "title": "title", "size": "size", "time": "time"}
        rows.sort(key=lambda r: r.get(key_map.get(col, "time"), 0), reverse=rev)
        # 更新表头箭头
        arrows = {"progress": "进度", "title": "书名", "size": "大小", "time": "时间"}
        for c, label in arrows.items():
            txt = label
            if c == col:
                txt = ("▼ " if rev else "▲ ") + label
            self.shelf_tree.heading(c, text=txt)
        # 插入
        for r in rows:
            prog_s = f"{r['progress']:.0f}%"
            iid = r["id"]
            self.shelf_tree.insert("", "end", iid=iid,
                                    values=(prog_s, r["title"], r["size_s"], r["time_s"]))
        # 恢复选中
        for bid in sel_bids:
            try:
                self.shelf_tree.selection_add(bid)
            except Exception:
                pass
        if not rows:
            self.shelf_tree.insert("", "end", iid="__empty__",
                                    values=("", "（书架为空，点击『添加书籍』导入）", "", ""))
        # 后台异步计算缺失的缓存大小
        self._refresh_shelf_sizes_async()

    def _shelf_sort(self, col):
        if getattr(self, "_shelf_sort_col", "") == col:
            self._shelf_sort_rev = not getattr(self, "_shelf_sort_rev", True)
        else:
            self._shelf_sort_col = col
            self._shelf_sort_rev = (col == "time")  # 时间默认倒序
        self._refresh_bookshelf()

    def _selected_bid(self):
        try:
            sel = self.shelf_tree.selection()
        except Exception:
            return None
        if not sel:
            return None
        # 多选时返回第一个
        return sel[0] if sel[0] != "__empty__" else None

    def _selected_bids(self):
        """返回所有选中的书籍 id 列表（多选支持）。"""
        try:
            sel = list(self.shelf_tree.selection())
        except Exception:
            return []
        return [s for s in sel if s != "__empty__"]

    def _popup_shelf_menu(self, event):
        # 右键点击的行
        iid = self.shelf_tree.identify_row(event.y)
        if not iid or iid == "__empty__":
            return
        sel = self._selected_bids()
        multi = len(sel) > 1
        # 如果右键的行不在选中中，则只选中该行
        if iid not in self.shelf_tree.selection():
            self.shelf_tree.selection_set(iid)
            multi = False
        # 多选时：打开书籍、复制原文件、复制书名 变灰
        self.shelf_menu.entryconfigure("打开书籍", state="disabled" if multi else "normal")
        self.shelf_menu.entryconfigure("复制原文件", state="disabled" if multi else "normal")
        self.shelf_menu.entryconfigure("复制书名", state="disabled" if multi else "normal")
        try:
            self.shelf_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.shelf_menu.grab_release()

    def _copy_book_file(self):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        path = meta.get("path", "")
        if not path or not os.path.exists(path):
            messagebox.showinfo("提示", "原文件不存在或已被移动。")
            return
        if _copy_files_to_clipboard([path]):
            messagebox.showinfo("已复制", "原文件已复制到剪贴板，可在文件管理器中直接粘贴（Ctrl+V）。")
        else:
            messagebox.showerror("失败", "复制到剪贴板失败。")

    def _copy_book_title(self):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(meta.get("title", ""))
        messagebox.showinfo("已复制", f"已复制书名：{meta.get('title', '')}")

    # ================= 阅读区右键菜单 =================
    def _popup_text_menu(self, event):
        try:
            self._ctx_index = self.text.index(f"@{event.x},{event.y}")
        except Exception:
            self._ctx_index = None
        try:
            self.text_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.text_menu.grab_release()

    def _selected_text(self):
        try:
            if self.text.tag_ranges("sel"):
                return self.text.get("sel.first", "sel.last").strip()
        except Exception:
            pass
        return ""

    def _snippet_under_cursor(self):
        """右键位置没有选中文字时，取光标所在行（过长则截取光标附近）作为搜索词。"""
        idx = getattr(self, "_ctx_index", None)
        if not idx:
            return ""
        try:
            line = int(idx.split(".")[0])
            col = int(idx.split(".")[1])
            txt = self.text.get(f"{line}.0", f"{line}.end").strip()
            if not txt:
                return ""
            if len(txt) <= 120:
                return txt
            lo = max(0, col - 40)
            hi = min(len(txt), col + 80)
            return txt[lo:hi]
        except Exception:
            return ""

    def _copy_selection(self):
        t = self._selected_text()
        if not t:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(t)

    def _search_selection(self, engine):
        t = self._selected_text()
        if not t:
            t = self._snippet_under_cursor()
        if not t:
            messagebox.showinfo("提示", "请先在阅读区选中文字，或右键点击某一行。")
            return
        q = urllib.parse.quote(t)
        urls = {
            "baidu": f"https://www.baidu.com/s?wd={q}",
            "google": f"https://www.google.com/search?q={q}",
            "bing": f"https://www.bing.com/search?q={q}",
            "translate": f"https://translate.google.com/?sl=auto&tl=zh-CN&text={q}",
        }
        webbrowser.open(urls[engine])

    def _open_selected(self):
        bid = self._selected_bid()
        if bid:
            self.open_book(bid)

    def _setup_drag_drop(self):
        """绑定文件拖拽（tkinterdnd2 原生方案，事件在主线程，稳定不闪退）。

        只绑定主窗口，整个窗口区域都能拖入文件。
        若 tkinterdnd2 不可用（降级为普通 Tk），则拖拽功能不可用，不影响其他功能。
        """
        try:
            if not hasattr(self.root, 'drop_target_register'):
                return  # 普通 Tk，不支持拖拽
            from tkinterdnd2 import DND_FILES
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop_files)
        except Exception:
            pass  # 拖拽不可用时静默降级

    def _on_drop_files(self, event):
        """拖拽释放回调（tkinterdnd2 在主线程调用）。"""
        try:
            # event.data 是空格分隔的文件路径字符串，含空格的路径用 {} 包裹
            paths = list(self.root.tk.splitlist(event.data))
            self._handle_dropped_files(paths)
        except Exception as e:
            try:
                if self.root.winfo_exists():
                    messagebox.showerror("拖拽导入失败", str(e))
            except Exception:
                pass

    def _handle_dropped_files(self, paths):
        """处理拖拽进来的文件列表（在主线程调用）。任何异常都不闪退。"""
        try:
            if not self.root.winfo_exists():
                return
            paths = [p for p in paths if p and os.path.isfile(p)]
            if not paths:
                return
            try:
                paths = self._filter_large_files(paths)
            except Exception:
                return
            if not paths:
                return
            self._import_many(paths)
        except Exception as e:
            try:
                if self.root.winfo_exists():
                    messagebox.showerror("拖拽导入失败", str(e))
            except Exception:
                pass

    def _add_book(self):
        paths = filedialog.askopenfilenames(title="选择要加入书架的小说（可多选）", filetypes=FILE_TYPES)
        if not paths:
            return
        paths = [p for p in paths if p]
        if not paths:
            return
        # 大文件确认：超过 25MB 弹确认，防止误选
        paths = self._filter_large_files(paths)
        if not paths:
            return
        # v1.93：单本 / 多本统一走后台线程 + 进度条窗口，解析分章不卡 UI
        self._import_many(paths)

    def _filter_large_files(self, paths, threshold_mb=25):
        """检查文件大小，超过阈值的弹确认窗口询问是否选中正确文件。返回确认后的路径列表。"""
        large = []
        for p in paths:
            try:
                size_mb = os.path.getsize(p) / (1024 * 1024)
                if size_mb > threshold_mb:
                    large.append((p, size_mb))
            except OSError:
                continue
        if not large:
            return paths
        # 弹确认窗口
        dlg = tk.Toplevel(self.root)
        dlg.title("文件较大")
        dlg.geometry("480x220")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        names = "\n".join(f"  · {os.path.basename(p)} ({sz:.1f}MB)" for p, sz in large)
        tk.Label(dlg, text=f"以下 {len(large)} 个文件超过 {threshold_mb}MB：",
                 font=("微软雅黑", 10, "bold")).pack(pady=(14, 6), anchor="w", padx=16)
        tk.Label(dlg, text=names, fg="#444444", font=("微软雅黑", 9),
                 justify="left", anchor="w").pack(padx=20, anchor="w")
        tk.Label(dlg, text="确认选中的是小说文件？",
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(8, 4))
        result = [None]
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text="确认导入", width=10,
                  command=lambda: (result.__setitem__(0, True), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="取消", width=8,
                  command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        if result[0]:
            return paths
        return []

    def _import_single(self, path):
        bid = self.storage.book_id(path)
        existing = self.storage.get_book(bid)
        if existing:
            # 重复书籍：弹确认，避免直接覆盖/重解析百万字大书造成假死
            choice = self._ask_duplicate(existing.get("title", os.path.basename(path)))
            if choice is None:
                return
            if choice == "reparse":
                self._reprocess_book(path, bid)
                return
            # 覆盖：沿用旧解析结果，仅更新书架记录（读缓存，不重新分章）
            try:
                content = self._load_book(path)
            except Exception as e:
                messagebox.showerror("无法打开", f"读取缓存失败：\n{e}")
                return
            self._save_import(content, path)
            self._refresh_bookshelf()
            self.open_book(bid)
            self._flash_status(f"已覆盖「{content.title}」（沿用已有解析）")
            return
        # 首次导入：后台线程 + 进度条窗口（v1.93：单本也弹进度窗，解析分章不卡 UI）
        self._import_many([path])

    def _ask_duplicate(self, title):
        """重复书籍确认框。返回 None=取消 / "overwrite"=覆盖 / "reparse"=重新预处理。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("重复书籍")
        dlg.geometry("460x200")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=f"「{title}」已在书架中", font=("微软雅黑", 10, "bold")).pack(pady=(16, 4))
        tk.Label(dlg, text="覆盖：沿用已有分章解析，立即完成；\n重新预处理：丢弃旧解析重新分章（长篇耗时较久）。",
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(2, 8))
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text="覆盖", width=10,
                  command=lambda: (result.__setitem__(0, "overwrite"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="重新预处理文本", width=14,
                  command=lambda: (result.__setitem__(0, "reparse"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="取消", width=8, command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]

    def _reprocess_book(self, path, bid):
        """重新预处理：后台线程强制重新解析分章，带进度窗，避免百万字假死。"""
        win = tk.Toplevel(self.root)
        win.title("重新预处理")
        win.geometry("380x130")
        win.resizable(False, False)
        win.transient(self.root)
        self._center_window(win)
        try:
            win.grab_set()
        except Exception:
            pass
        tk.Label(win, text="正在重新解析并分章，请稍候…", font=("微软雅黑", 11)).pack(pady=(18, 8))
        bar = ttk.Progressbar(win, mode="indeterminate")
        bar.pack(fill="x", padx=28)
        bar.start(12)
        done = queue.Queue()

        def worker():
            try:
                content = book_loader.parse_book(path)  # 强制重新解析
                self.storage.write_cache(bid, content)
                self._cache[bid] = content
                done.put(("ok", content))
            except Exception as e:
                done.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                msg = done.get_nowait()
            except queue.Empty:
                win.after(100, poll)
                return
            bar.stop()
            win.destroy()
            if msg[0] == "ok":
                content = msg[1]
                self._save_import(content, path)
                self._refresh_bookshelf()
                self.open_book(bid)
                self._flash_status(f"已重新预处理「{content.title}」")
            else:
                messagebox.showerror("解析失败", msg[1])

        win.after(100, poll)

    def _save_import(self, content, path):
        """把解析结果写入书架与缓存，返回书籍 id。"""
        bid = self.storage.book_id(path)
        meta = {
            "id": bid,
            "title": content.title,
            "author": content.author,
            "format": content.format,
            "path": path,
            "added_at": time.time(),
            "last_read_at": time.time(),
            "total_chars": content.total_chars,
            "chapter_titles": [c.title for c in content.chapters],
            "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
        }
        self.storage.add_book(meta)
        self.storage.write_cache(bid, content)
        self._cache[bid] = content
        return bid

    def _import_many(self, paths):
        """批量导入：弹出进度条窗口，后台线程逐本解析分章，全部完成后刷新书架。"""
        total = len(paths)
        self._first_imported_bid = None
        # 重复书籍处理：弹一次确认，避免直接覆盖/重解析百万字大书造成假死
        force_reparse = set()
        dups = []
        for p in paths:
            b = self.storage.book_id(p)
            if self.storage.get_book(b):
                dups.append((p, b))
        if dups:
            choice = self._ask_duplicate_batch(len(dups))
            if choice is None:
                return
            if choice == "reparse":
                force_reparse = {b for _, b in dups}
        win = tk.Toplevel(self.root)
        win.title("正在导入")
        win.geometry("400x140")
        win.resizable(False, False)
        win.transient(self.root)
        self._center_window(win)
        try:
            win.grab_set()  # 模态，防止导入期间误操作
        except Exception:
            pass
        tk.Label(win, text="正在解析并分章，请稍候…", font=("微软雅黑", 11)).pack(pady=(16, 6))
        prog_var = tk.DoubleVar(value=0)
        bar = ttk.Progressbar(win, maximum=total, variable=prog_var)
        bar.pack(fill="x", padx=28)
        info_lbl = tk.Label(win, text=f"0 / {total}", font=("微软雅黑", 10))
        info_lbl.pack(pady=(8, 0))
        name_lbl = tk.Label(win, text="", fg="#666666", font=("微软雅黑", 9), wraplength=360)
        name_lbl.pack(pady=(2, 0))

        done = queue.Queue()  # ("ok", bid) / ("err", path, msg) / ("done",)
        self._import_worker_stop = threading.Event()

        def worker():
            for p in paths:
                if self._import_worker_stop.is_set():
                    break
                try:
                    bid = self.storage.book_id(p)
                    content = self._load_book(p, force_reparse=(bid in force_reparse))
                    bid = self._save_import(content, p)
                    done.put(("ok", bid, os.path.basename(p)))
                except Exception as e:
                    done.put(("err", os.path.basename(p), str(e)))
            done.put(("done",))

        threading.Thread(target=worker, daemon=True).start()
        self._import_win = win
        self._import_queue = done
        self._poll_import(win, prog_var, info_lbl, name_lbl, total)

    def _ask_duplicate_batch(self, count):
        """批量导入中重复书籍的确认框。返回 None=取消 / "overwrite" / "reparse"。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("重复书籍")
        dlg.geometry("460x190")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=f"所选文件中有 {count} 本已在书架中", font=("微软雅黑", 10, "bold")).pack(pady=(16, 4))
        tk.Label(dlg, text="全部覆盖：沿用已有分章解析，立即完成；\n全部重新预处理：丢弃旧解析重新分章（长篇耗时较久）。",
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(2, 8))
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text="全部覆盖", width=10,
                  command=lambda: (result.__setitem__(0, "overwrite"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="全部重新预处理", width=14,
                  command=lambda: (result.__setitem__(0, "reparse"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="取消导入", width=10, command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]

    def _poll_import(self, win, prog_var, info_lbl, name_lbl, total):
        if not win.winfo_exists():
            return
        try:
            while True:
                msg = self._import_queue.get_nowait()
                if msg[0] == "done":
                    win.grab_release()
                    win.destroy()
                    self._refresh_bookshelf()
                    # 打开第一本成功导入的书
                    if getattr(self, "_first_imported_bid", None):
                        self.open_book(self._first_imported_bid)
                    return
                if msg[0] == "ok":
                    if getattr(self, "_first_imported_bid", None) is None:
                        self._first_imported_bid = msg[1]
                prog_var.set(prog_var.get() + 1)
                info_lbl.configure(text=f"{int(prog_var.get())} / {total}")
                name_lbl.configure(text=msg[1] if msg[0] == "ok" else f"失败：{msg[1]}：{msg[2]}")
        except queue.Empty:
            pass
        self.root.after(80, lambda: self._poll_import(win, prog_var, info_lbl, name_lbl, total))

    def _remove_book(self, clear_cache=False):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        label = "删除文件" if clear_cache else "删除书籍"
        tip = ("（从书架移除并清空缓存，不删除原文件）" if clear_cache
               else "（从书架移除，保留缓存，不删除原文件）")
        if not messagebox.askyesno(label, f"确定{label}《{meta.get('title','')}》？\n{tip}"):
            return
        self.tts.stop()
        self.storage.remove_book(bid)
        self._cache.pop(bid, None)
        if clear_cache:
            # 删除文本解析缓存
            cp = self.storage.cache_path(bid)
            if cp and os.path.exists(cp):
                try:
                    os.remove(cp)
                except Exception:
                    pass
            # 同步删除该书音频缓存（整本语音缓存）
            self._delete_audio_cache(bid)
        if self.current_bid == bid:
            self.current_bid = None
            self.book = None
            self._render_empty()
        self._refresh_bookshelf()

    # ================= 书籍加载 =================
    def _load_book(self, path, force_reparse=False):
        bid = self.storage.book_id(path)
        if not force_reparse:
            if bid in self._cache:
                return self._cache[bid]
            cached = self.storage.read_cache(bid)
            if cached:
                content = book_loader.BookContent.from_dict(cached)
                self._cache[bid] = content
                return content
        content = book_loader.parse_book(path)
        self.storage.write_cache(bid, content)
        self._cache[bid] = content
        return content

    def open_book(self, bid):
        meta = self.storage.get_book(bid)
        if not meta:
            return
        self.tts.stop()
        self.current_bid = bid
        self.tts.set_book_id(bid)
        try:
            self.book = self._load_book(meta["path"])
        except Exception:
            cached = self.storage.read_cache(bid)
            if cached:
                self.book = book_loader.BookContent.from_dict(cached)
            else:
                messagebox.showerror("打开失败", f"无法读取书籍文件：\n{meta['path']}")
                return
        prog = meta.get("progress") or {}
        self.chapter_idx = max(0, min(int(prog.get("chapter_idx", 0)), len(self.book.chapters) - 1))
        self.char_offset = int(prog.get("char_offset", 0))
        self.char_offset = max(0, min(self.char_offset, len(self.book.chapters[self.chapter_idx].content)))
        self.root.title(f"{self.book.title} - 多多朗读 v{__version__}")
        self.title_label.configure(text=self.book.title)
        self._populate_chapters()
        self._render_chapter()
        self._refresh_bookshelf()

    def _populate_chapters(self):
        titles = [c.title for c in self.book.chapters]
        self.chapter_cb["values"] = titles
        self.chapter_cb.set(titles[self.chapter_idx])
        self.chapter_list.delete(0, "end")
        for i, t in enumerate(titles):
            self.chapter_list.insert("end", t)
        self.chapter_list.see(self.chapter_idx)
        self.chapter_list.selection_clear(0, "end")
        self.chapter_list.selection_set(self.chapter_idx)

    def _render_chapter(self):
        if not self.book:
            return
        ch = self.book.chapters[self.chapter_idx]
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        # 插入章节标题（用户翻章时能明显看到当前章节）
        title_text = ch.title.strip() if ch.title else f"第 {self.chapter_idx + 1} 章"
        self.text.insert("1.0", title_text + "\n\n", "chapter_title")
        # 记录标题偏移量（标题+两个换行）
        self._title_char_len = len(title_text) + 2
        # 正文起始行号：注意 Tk 的 index("end") 在文本以 \n\n 结尾时会多返回 1 行
        # （隐含末尾空行），而正文实际插入后落在 end-1c 所在行，故用 end-1c。
        self._body_start_line = int(self.text.index("end-1c").split(".")[0])
        # 插入正文
        self.text.insert("end", self._apply_paragraph_mode(ch.content), "body")
        self.text.configure(state="disabled")
        self._apply_font()
        self._tag_body()
        self._scroll_to_offset(self.char_offset)
        self._populate_chapters()
        self._update_status()
        self._repin_reading()

    def _render_empty(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.title_label.configure(text="未打开书籍")
        self.percent_label.configure(text="0.0%")
        self.pos_label.configure(text="")
        self.total_label.configure(text="")
        self.tts_cache_label.configure(text="")
        self.progress_var.set(0)
        self.root.title(f"多多朗读 v{__version__}")

    # ================= 进度 =================
    def _compute_percent(self, ci, off):
        if not self.book or self.book.total_chars <= 0:
            return 0.0
        cum = self.book.cum[ci] + off
        return min(100.0, max(0.0, cum / self.book.total_chars * 100.0))

    def _update_status(self):
        if not self.book:
            return
        pct = self._compute_percent(self.chapter_idx, self.char_offset)
        self.progress_var.set(pct)
        self.percent_label.configure(text=f"{pct:.1f}%")
        self.pos_label.configure(text=f"第 {self.chapter_idx + 1} 章 / 共 {len(self.book.chapters)} 章")
        self.total_label.configure(text=f"总字数 {self.book.total_chars:,}")

    # ---- 进度滑块（可拖动跳转） ----
    def _on_seek_press(self, event=None):
        self._seeking = True

    def _on_seek_release(self, event=None):
        self._seeking = False

    def _on_seek(self, value):
        # 仅响应用户拖动；程序自动更新进度时不触发跳转
        if not self.book or not self._seeking:
            return
        self._seek_to_percent(float(value))

    def _seek_to_percent(self, pct):
        target = max(0.0, min(100.0, pct)) / 100.0 * self.book.total_chars
        ci = 0
        for i, c in enumerate(self.book.cum):
            if c <= target:
                ci = i
            else:
                break
        ci = max(0, min(ci, len(self.book.chapters) - 1))
        off = int(target - self.book.cum[ci])
        off = max(0, min(off, len(self.book.chapters[ci].content)))
        was = self._seeking
        self._seeking = False
        try:
            self._goto_chapter(ci, off)
        finally:
            self._seeking = was

    def _on_scroll(self, event=None):
        if not self.book:
            return
        self.char_offset = self._current_offset()
        self._update_status()
        self._schedule_save()

    def _on_text_resize(self, event=None):
        """窗口缩放/移动/初始化等触发视口变化：朗读中把高亮重新钉到顶部。"""
        if self.book and self.tts.is_playing():
            self._repin_reading()
        self._on_scroll(event)

    def _current_offset(self):
        try:
            top = self.text.index("@0,0")
            cnt = self.text.count("1.0", top, "chars")
            raw = cnt[0] if cnt else 0
            # 减去章节标题的字符数，得到正文中的偏移
            return max(0, raw - self._title_char_len)
        except Exception:
            return 0

    def _display_line_to_offset(self, line):
        """把渲染文本的显示行号映射回原文字符偏移（该段落起点的原文偏移）。

        空行模式会改变显示行号：模式1 原始第 L 行→显示第 L 行；
        模式2 原始第 L 行→显示第 2L-1 行（偶数为空行）；模式3 全文仅 1 行。
        """
        content = self.book.chapters[self.chapter_idx].content
        mode = int(self.settings.get("paragraph_mode", 1))
        line = max(1, int(line))
        # 减去章节标题占的行数，得到正文中的行号
        line = max(1, line - self._body_start_line + 1)
        if mode == 3:
            return 0
        if mode == 2:
            orig_line = (line + 1) // 2
        else:
            orig_line = line
        if orig_line <= 1:
            return 0
        idx = -1
        for _ in range(orig_line - 1):
            idx = content.find("\n", idx + 1)
            if idx < 0:
                return len(content)
        return idx + 1

    def _read_from_paragraph(self):
        """阅读区右键：从光标所在段落起点开始朗读。"""
        if not self.book:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        idx = getattr(self, "_ctx_index", None)
        if not idx:
            return
        try:
            line = int(idx.split(".")[0])
        except Exception:
            return
        # 向上找到段落起始显示行（空行之前的一行）
        start_line = line
        try:
            while start_line > 1:
                prev = self.text.get(f"{start_line - 1}.0", f"{start_line - 1}.end")
                if not prev.strip():
                    break
                start_line -= 1
        except Exception:
            pass
        off = self._display_line_to_offset(start_line)
        self._goto_chapter(self.chapter_idx, off)  # 渲染/保存；正在朗读则自动继续
        if not self.tts.is_active():
            self.tts.start(self.book, self.chapter_idx, off)
            self._set_tts_ui("playing")
        self._flash_status("已从该段开始朗读")

    def _scroll_to_offset(self, offset):
        if not self.book:
            return
        content = self.book.chapters[self.chapter_idx].content
        line, col = self._transformed_pos(content, offset)
        self.text.see(f"{line}.{col}")

    def _schedule_save(self):
        if self._save_timer:
            self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(700, self._save_now)

    def _save_now(self):
        self._save_timer = None
        if not self.current_bid or not self.book:
            return
        pct = self._compute_percent(self.chapter_idx, self.char_offset)
        self.storage.update_progress(
            self.current_bid,
            {
                "chapter_idx": self.chapter_idx,
                "char_offset": self.char_offset,
                "percent": round(pct, 3),
            },
        )
        self.storage.set_setting("last_book", self.current_bid)
        self._refresh_bookshelf()

    # ================= 章节导航 =================
    def _goto_chapter(self, ci, offset=0):
        if not self.book:
            return
        ci = max(0, min(ci, len(self.book.chapters) - 1))
        was_active = self.tts.is_active()
        if was_active:
            self.tts.stop()  # 结束旧会话，避免旧章节状态冲突
        self.chapter_idx = ci
        self.char_offset = offset
        self._render_chapter()
        self._save_now()
        if was_active:
            # 换章后继续朗读：从新章节当前位置接着读
            self.tts.start(self.book, ci, offset)
            self._set_tts_ui("playing")

    def _on_chapter_cb(self, event):
        try:
            idx = self.chapter_cb.current()
        except Exception:
            return
        if idx >= 0:
            self._goto_chapter(idx)

    def _on_chapter_pick(self, event):
        sel = self.chapter_list.curselection()
        if not sel:
            return
        self._goto_chapter(sel[0])

    def _toggle_toc(self):
        try:
            pane_paths = [str(p) for p in self._inner_paned.panes()]
        except Exception:
            pane_paths = []
        if str(self.chapter_panel) in pane_paths:
            self._inner_paned.remove(self.chapter_panel)
            self._chapter_panel_visible = False
        else:
            self._inner_paned.add(self.chapter_panel, weight=0)
            self._chapter_panel_visible = True

    # ================= 外观设置 =================
    def _on_font_change(self, event):
        self.settings["font_family"] = self.font_cb.get()
        self._apply_font()
        self.storage.set_setting("font_family", self.settings["font_family"])

    def _change_font_size(self, delta):
        size = int(self.settings.get("font_size", 17)) + delta
        size = max(10, min(48, size))
        self.settings["font_size"] = size
        self.size_label.configure(text=str(size))
        self._apply_font()
        self.storage.set_setting("font_size", size)

    def _change_line_spacing(self, delta):
        ls = round(float(self.settings.get("line_spacing", 1.5)) + delta, 1)
        ls = max(1.0, min(3.0, ls))
        self.settings["line_spacing"] = ls
        self.spacing_label.configure(text=f"{ls:.1f}")
        self._apply_font()
        self.storage.set_setting("line_spacing", ls)

    def _on_paragraph_mode(self, event=None):
        idx = self.paragraph_cb.current()
        if idx < 0:
            return
        mode = idx + 1
        self.settings["paragraph_mode"] = mode
        self.storage.set_setting("paragraph_mode", mode)
        self._render_chapter()

    def _change_sentence_gap(self, delta):
        gap = round(float(self.settings.get("tts_sentence_gap", 0.10)) + delta, 2)
        gap = max(0.0, min(1.0, gap))
        self.settings["tts_sentence_gap"] = gap
        self.gap_label.configure(text=f"{gap:.2f}")
        self.tts.set_sentence_gap(gap)
        self.storage.set_setting("tts_sentence_gap", gap)

    def _on_volume_change(self, val):
        v = int(float(val))
        self.volume_label.configure(text=str(v))
        self.tts.set_volume(v)
        self.settings["volume"] = v
        self.storage.set_setting("volume", v)

    def _on_theme_change(self, event):
        theme = self.theme_cb.get()
        self.settings["theme"] = theme
        self._apply_theme(theme)
        self.storage.set_setting("theme", theme)

    # ================= 关于 =================
    def _show_about(self):
        from . import version_info

        top = tk.Toplevel(self.root)
        top.title(f"关于 {version_info.APP_NAME}")
        top.geometry("700x800")
        top.minsize(560, 620)
        top.transient(self.root)
        self._center_window(top)
        _about_bg = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        top.configure(bg=_about_bg)

        head = tk.Frame(top, bg=_about_bg)
        head.pack(fill="x", padx=18, pady=(16, 6))
        # 左侧：当前图标（彩蛋：点击进入主题选择）
        icon_col = tk.Frame(head, bg=_about_bg)
        icon_col.pack(side="left", padx=(0, 14))
        try:
            png = self._current_skin_png()
            if png:
                self._about_icon_img = tk.PhotoImage(file=png)
                self._about_icon_img = self._about_icon_img.subsample(4, 4)
                icon_lbl = tk.Label(icon_col, image=self._about_icon_img, bg=_about_bg, cursor="hand2")
                icon_lbl.pack()
                icon_lbl.bind("<Button-1>", lambda e: self._open_skin_picker())
                tk.Label(icon_col, text="点击换主题", fg="#999999", bg=_about_bg,
                         font=("微软雅黑", 8)).pack(pady=(2, 0))
        except Exception:
            pass
        # 右侧：文字信息
        info = tk.Frame(head, bg=_about_bg)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=f"{version_info.APP_NAME}  v{__version__}",
                 font=("微软雅黑", 17, "bold"), bg=_about_bg).pack(anchor="w")
        tk.Label(info, text=f"Windows 桌面有声小说阅读器（多多朗读）  ·  当前版本 v{__version__}",
                 fg="#777777", bg=_about_bg, font=("微软雅黑", 10)).pack(anchor="w", pady=(2, 0))
        email_row = tk.Frame(info, bg=_about_bg)
        email_row.pack(anchor="w", pady=(6, 0))
        tk.Label(email_row, text="作者联系方式：", fg="#555555", bg=_about_bg,
                 font=("微软雅黑", 10)).pack(side="left")
        self._email_label = tk.Label(
            email_row, text="230468896@qq.com", fg="#2b6cb0", bg=_about_bg,
            font=("微软雅黑", 10, "underline"), cursor="hand2")
        self._email_label.pack(side="left")
        self._email_label.bind("<Button-1>", lambda e: self._copy_email())
        tk.Label(email_row, text="（点击复制）", fg="#999999", bg=_about_bg,
                 font=("微软雅黑", 9)).pack(side="left", padx=(6, 0))

        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        tab_update = tk.Frame(nb)
        tab_keys = tk.Frame(nb)
        tab_cache = tk.Frame(nb)
        nb.add(tab_update, text="更新记录")
        nb.add(tab_keys, text="快捷键说明")
        nb.add(tab_cache, text="缓存管理")

        txt = tk.Text(tab_update, wrap="word", padx=12, pady=10, relief="flat", font=("微软雅黑", 10))
        tsb = tk.Scrollbar(tab_update, command=txt.yview)
        txt.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("end", version_info.format_history())
        txt.configure(state="disabled")

        keys = tk.Text(tab_keys, wrap="word", padx=12, pady=10, relief="flat", font=("微软雅黑", 10))
        ksb = tk.Scrollbar(tab_keys, command=keys.yview)
        keys.configure(yscrollcommand=ksb.set)
        ksb.pack(side="right", fill="y")
        keys.pack(side="left", fill="both", expand=True)
        for k, desc in version_info.SHORTCUTS:
            keys.insert("end", f"{k}\n    {desc}\n\n")
        keys.configure(state="disabled")

        # —— 缓存管理（可滚动卡片式布局） ——
        tab_cache.configure(bg=_about_bg)
        cache_canvas = tk.Canvas(tab_cache, bg=_about_bg, highlightthickness=0, bd=0)
        cache_scroll = ttk.Scrollbar(tab_cache, orient="vertical", command=cache_canvas.yview)
        cache_inner = tk.Frame(cache_canvas, bg=_about_bg)
        cache_inner.bind("<Configure>", lambda e: cache_canvas.configure(scrollregion=cache_canvas.bbox("all")))
        cache_canvas.create_window((0, 0), window=cache_inner, anchor="nw")
        cache_canvas.configure(yscrollcommand=cache_scroll.set)
        cache_scroll.pack(side="right", fill="y")
        cache_canvas.pack(side="left", fill="both", expand=True)

        def _cache_mwheel(event):
            cache_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        cache_canvas.bind("<Enter>", lambda e: cache_canvas.bind_all("<MouseWheel>", _cache_mwheel))
        cache_canvas.bind("<Leave>", lambda e: cache_canvas.unbind_all("<MouseWheel>"))

        def _make_cache_card(parent, title, subtitle, icon, accent, kind):
            """创建一个缓存管理卡片。kind: 'text' or 'audio'."""
            _card_bg = UI_THEMES.get(self.settings.get("ui_theme", "A·米黄暖读"), UI_THEMES["A·米黄暖读"])["field"]
            card = tk.Frame(parent, bg=_card_bg, highlightbackground="#e2e5ea", highlightthickness=1)
            card.pack(fill="x", padx=14, pady=(12, 0))

            # 标题行
            head = tk.Frame(card, bg=_card_bg)
            head.pack(fill="x", padx=14, pady=(12, 4))
            tk.Label(head, text=icon, font=("微软雅黑", 14), bg=_card_bg).pack(side="left")
            tk.Label(head, text=title, font=("微软雅黑", 11, "bold"), fg=accent, bg=_card_bg).pack(side="left", padx=(6, 0))
            loc_lbl = tk.Label(head, text="", font=("微软雅黑", 9), fg="#999999", bg=_card_bg)
            loc_lbl.pack(side="right")

            # 副标题（精简说明）
            if subtitle:
                tk.Label(card, text=subtitle, font=("微软雅黑", 9), fg="#888888", bg=_card_bg,
                         wraplength=520, justify="left").pack(anchor="w", padx=14, pady=(0, 4))

            # 路径
            path_text = self._effective_text_cache_root() if kind == "text" else self._effective_tts_cache_root()
            path_lbl = tk.Label(card, text=path_text, font=("微软雅黑", 9), fg="#2b6cb0",
                                bg=_card_bg, wraplength=520, justify="left", cursor="hand2")
            path_lbl.pack(anchor="w", padx=14, pady=(2, 2))
            open_cmd = self._open_cache_folder if kind == "text" else self._open_tts_cache_folder
            path_lbl.bind("<Button-1>", lambda e: open_cmd())

            # 大小
            size_lbl = tk.Label(card, text="正在统计…", font=("微软雅黑", 10), fg="#444444", bg=_card_bg)
            size_lbl.pack(anchor="w", padx=14, pady=(2, 6))

            # 按钮行
            btn_row = tk.Frame(card, bg=_card_bg)
            btn_row.pack(anchor="w", padx=14, pady=(0, 12))
            if kind == "text":
                ttk.Button(btn_row, text="自定义位置",
                           command=lambda: self._choose_text_cache_folder(path_lbl, size_lbl)).pack(side="left")
                ttk.Button(btn_row, text="一键转移",
                           command=lambda: self._transfer_text_cache(path_lbl, size_lbl)).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text="打开文件夹",
                           command=self._open_cache_folder).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text="清除",
                           command=lambda: self._clear_cache(size_lbl)).pack(side="left", padx=(8, 0))
            else:
                ttk.Button(btn_row, text="自定义位置",
                           command=lambda: self._choose_tts_cache_folder(path_lbl, tts_size_lbl)).pack(side="left")
                ttk.Button(btn_row, text="一键转移",
                           command=lambda: self._transfer_tts_cache(path_lbl, tts_size_lbl)).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text="打开文件夹",
                           command=self._open_tts_cache_folder).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text="清除",
                           command=lambda: self._clear_audio_cache(tts_size_lbl)).pack(side="left", padx=(8, 0))

            return path_lbl, size_lbl, loc_lbl

        # 正文解析缓存卡片
        path_lbl, size_lbl, loc_lbl1 = _make_cache_card(
            cache_inner,
            title="正文解析缓存",
            subtitle="书籍分章解析结果，删除后下次打开需重新解析（不影响原文件）。",
            icon="📄",
            accent="#2b6cb0",
            kind="text",
        )

        # 音频缓存卡片
        tts_path_lbl, tts_size_lbl, loc_lbl2 = _make_cache_card(
            cache_inner,
            title="音频缓存（整本语音）",
            subtitle="整本语音合成缓存，体积较大，建议放到非 C 盘。",
            icon="🔊",
            accent="#b00020",
            kind="audio",
        )

        # 底部留白
        tk.Frame(cache_inner, bg=_about_bg, height=14).pack()

        # 统计大小（延迟到窗口显示后）
        def _refresh_sizes():
            self._update_cache_size_label(size_lbl)
            self._update_tts_cache_size_label(tts_size_lbl)
            loc_lbl1.configure(text="自定义位置" if self.settings.get("cache_dir") else "默认位置")
            loc_lbl2.configure(text="自定义位置" if self.settings.get("tts_cache_dir") else "默认位置")
        top.after(120, _refresh_sizes)

        self._about_win = top
        top.focus_set()
        # 注：不使用 grab_set()，避免模态窗口内 filedialog/messagebox 被遮挡或无法交互

    def _open_skin_picker(self):
        """主题选择：六套控件风格（A-F），点击即切换，即时生效。"""
        top = tk.Toplevel(self.root)
        top.title("主题选择")
        top.geometry("560x440")
        top.minsize(480, 380)
        top.transient(self.root)
        self._center_window(top)
        cur = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])
        top.configure(bg=cur["bg"])

        tk.Label(top, text="选择控件主题风格（点击即切换，即时生效）",
                 bg=cur["bg"], fg=cur["muted"], font=("微软雅黑", 10)).pack(pady=(12, 8))

        container = tk.Frame(top, bg=cur["bg"])
        container.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for i in range(3):
            container.grid_columnconfigure(i, weight=1)
        for i in range(2):
            container.grid_rowconfigure(i, weight=1)

        current_name = self.settings.get("ui_theme", "D·原生微调")

        def _make_switch(name, win):
            def _sw(event=None):
                self._apply_ui_theme(name)
                try:
                    win.destroy()
                except Exception:
                    pass
            return _sw

        for idx, (name, c) in enumerate(UI_THEMES.items()):
            r, col = divmod(idx, 3)
            is_cur = (name == current_name)
            card = tk.Frame(container, bg=c["field"], relief="solid",
                            bd=2 if is_cur else 1, cursor="hand2")
            card.grid(row=r, column=col, padx=8, pady=8, sticky="nsew")

            # 配色预览条
            prev = tk.Frame(card, bg=c["bg"], height=36, cursor="hand2")
            prev.pack(fill="x")
            btn_prev = tk.Frame(prev, bg=c["btn"], width=56, height=22, cursor="hand2")
            btn_prev.pack(side="left", padx=8, pady=7)
            tk.Label(prev, text="Aa", bg=c["bg"], fg=c["fg"],
                     font=("微软雅黑", 11, "bold"), cursor="hand2").pack(side="left", padx=(4, 0))

            tk.Label(card, text=name, bg=c["field"], fg=c["fg"],
                     font=("微软雅黑", 10, "bold"), cursor="hand2").pack(pady=(6, 2))
            tk.Label(card, text=("✓ 当前" if is_cur else f"按钮 {c['btn']}"),
                     bg=c["field"], fg=(c["accent"] if is_cur else c["muted"]),
                     font=("微软雅黑", 8), cursor="hand2").pack(pady=(0, 6))

            # 绑定点击切换（卡片及所有子控件）
            _sw = _make_switch(name, top)
            for w in (card, prev, btn_prev):
                w.bind("<Button-1>", _sw)
            for w in card.winfo_children():
                w.bind("<Button-1>", _sw)

    def _copy_email(self):
        """复制作者邮箱到剪贴板。"""
        email = "230468896@qq.com"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(email)
            lbl = self._email_label
            try:
                lbl.configure(text="已复制 ✓")

                def _restore():
                    try:
                        if lbl.winfo_exists():
                            lbl.configure(text=email)
                    except Exception:
                        pass

                self.root.after(1500, _restore)
            except Exception:
                pass
        except Exception:
            pass

    def _cache_size_bytes(self):
        total = 0
        d = cache_dir()
        try:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p):
                    try:
                        total += os.path.getsize(p)
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    def _format_bytes(self, n):
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / 1024 / 1024:.2f} MB"

    def _update_cache_size_label(self, lbl):
        try:
            root = self._effective_text_cache_root()
            siz = dir_size(root)
            loc = "默认位置" if not (self.settings.get("cache_dir") or "") else "自定义位置"
            lbl.configure(text=f"缓存总大小：{self._format_bytes(siz)}（{loc}）")
        except Exception:
            pass

    # ---------- 正文解析缓存目录管理 ----------
    def _effective_text_cache_root(self):
        """当前生效的正文解析缓存根目录（优先自定义，空则默认）。"""
        custom = self.storage.get_setting("cache_dir") or ""
        return resolve_cache_dir(custom)

    def _open_cache_folder(self):
        d = self._effective_text_cache_root()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("无法打开", f"打开缓存文件夹失败：{e}")

    def _choose_text_cache_folder(self, path_lbl=None, size_lbl=None):
        """自定义正文解析缓存文件夹：可顺带一键转移。"""
        cur = self._effective_text_cache_root()
        d = filedialog.askdirectory(
            title="选择正文解析缓存文件夹",
            initialdir=os.path.dirname(cur),
        )
        if not d:
            return
        move = messagebox.askyesno(
            "转移缓存",
            f"已选择新缓存文件夹：\n{d}\n\n"
            "是否现在把现有缓存转移到新位置？\n"
            "（选择「否」则仅切换位置，不移动现有文件）",
        )
        if move:
            self._do_transfer_cache(cur, d, kind="text")
        else:
            self.settings["cache_dir"] = d
            self.storage.set_setting("cache_dir", d)
        if path_lbl is not None:
            path_lbl.configure(text=self._effective_text_cache_root())
        if size_lbl is not None:
            self._update_cache_size_label(size_lbl)

    def _transfer_text_cache(self, path_lbl=None, size_lbl=None):
        """一键转移正文解析缓存到新位置。"""
        cur = self._effective_text_cache_root()
        d = filedialog.askdirectory(title="选择正文缓存新位置", initialdir=os.path.dirname(cur))
        if not d:
            return
        if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(cur)):
            messagebox.showinfo("提示", "新位置与当前缓存位置相同，无需转移。")
            return
        if not messagebox.askyesno(
            "一键转移缓存",
            f"将正文解析缓存从：\n{cur}\n转移到：\n{d}\n\n转移后立即生效，确定？",
        ):
            return
        self._do_transfer_cache(cur, d, kind="text")
        if path_lbl is not None:
            path_lbl.configure(text=self._effective_text_cache_root())
        if size_lbl is not None:
            self._update_cache_size_label(size_lbl)

    def _clear_cache(self, size_lbl=None):
        if not messagebox.askyesno("清除缓存", "确定清除全部正文解析缓存？\n（下次打开书籍需重新解析，不影响书籍原文件）"):
            return
        d = self._effective_text_cache_root()
        n = 0
        try:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                        n += 1
                    except Exception:
                        pass
        except Exception:
            pass
        if size_lbl is not None:
            self._update_cache_size_label(size_lbl)
        messagebox.showinfo("已清除", f"已清除 {n} 个缓存文件。")

    # ---------- 通用缓存转移 ----------
    def _do_transfer_cache(self, old_dir, new_dir, kind="text"):
        """把 old_dir 的内容移动到 new_dir，并切换对应设置。

        kind: "text" → 切换 cache_dir 设置；"audio" → 切换 tts_cache_dir 设置 + tts 实例。
        """
        import shutil
        old = os.path.abspath(old_dir)
        new = os.path.abspath(new_dir)
        try:
            os.makedirs(new, exist_ok=True)
            if os.path.normcase(new) != os.path.normcase(old):
                for name in os.listdir(old):
                    src = os.path.join(old, name)
                    dst = os.path.join(new, name)
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        if os.path.isdir(src):
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src, dst)
                        try:
                            if os.path.isdir(src):
                                shutil.rmtree(src, ignore_errors=True)
                            else:
                                os.remove(src)
                        except Exception:
                            pass
        except Exception as e:
            messagebox.showerror("转移失败", f"转移缓存时出错：\n{e}")
            return
        if kind == "text":
            self.settings["cache_dir"] = new
            self.storage.set_setting("cache_dir", new)
        elif kind == "audio":
            self.settings["tts_cache_dir"] = new
            self.storage.set_setting("tts_cache_dir", new)
            self.tts.set_tts_cache_dir(self._effective_tts_cache_root())
        label = "正文解析缓存" if kind == "text" else "音频缓存"
        messagebox.showinfo("转移完成", f"{label}已转移到：\n{new}")

    # ---------- 音频缓存目录管理 ----------
    def _update_tts_cache_size_label(self, lbl):
        try:
            root = self._effective_tts_cache_root()
            siz = dir_size(root)
            loc = "默认位置" if not (self.settings.get("tts_cache_dir") or "") else "自定义位置"
            lbl.configure(text=f"音频缓存总大小：{self._format_bytes(siz)}（{loc}）")
        except Exception:
            pass

    def _open_tts_cache_folder(self):
        d = self._effective_tts_cache_root()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("无法打开", f"打开音频缓存文件夹失败：{e}")

    def _choose_tts_cache_folder(self, path_lbl=None, size_lbl=None):
        """自定义音频缓存文件夹：可顺带一键转移。"""
        cur = self._effective_tts_cache_root()
        d = filedialog.askdirectory(
            title="选择音频缓存文件夹（建议放在非 C 盘）",
            initialdir=os.path.dirname(cur),
        )
        if not d:
            return
        move = messagebox.askyesno(
            "转移缓存",
            f"已选择新缓存文件夹：\n{d}\n\n"
            "是否现在把现有音频缓存转移到新位置？\n"
            "（选择「否」则仅切换位置，不移动现有文件）",
        )
        if move:
            self._do_transfer_cache(cur, d, kind="audio")
        else:
            self.settings["tts_cache_dir"] = d
            self.storage.set_setting("tts_cache_dir", d)
            self.tts.set_tts_cache_dir(self._effective_tts_cache_root())
        if path_lbl is not None:
            path_lbl.configure(text=self._effective_tts_cache_root())
        if size_lbl is not None:
            self._update_tts_cache_size_label(size_lbl)
        self._refresh_bookshelf()

    def _transfer_tts_cache(self, path_lbl=None, size_lbl=None):
        """一键转移音频缓存到新位置。"""
        cur = self._effective_tts_cache_root()
        d = filedialog.askdirectory(title="选择音频缓存新位置（建议放在非 C 盘）", initialdir=os.path.dirname(cur))
        if not d:
            return
        if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(cur)):
            messagebox.showinfo("提示", "新位置与当前缓存位置相同，无需转移。")
            return
        if not messagebox.askyesno(
            "一键转移缓存",
            f"将音频缓存从：\n{cur}\n转移到：\n{d}\n\n转移后立即生效，确定？",
        ):
            return
        self._do_transfer_cache(cur, d, kind="audio")
        if path_lbl is not None:
            path_lbl.configure(text=self._effective_tts_cache_root())
        if size_lbl is not None:
            self._update_tts_cache_size_label(size_lbl)
        self._refresh_bookshelf()

    def _clear_audio_cache(self, size_lbl=None):
        if not messagebox.askyesno(
            "清除音频缓存",
            "确定清除全部音频缓存？\n（整本语音缓存文件将被删除，朗读需重新联网合成）",
        ):
            return
        import shutil
        root = self._effective_tts_cache_root()
        n = 0
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                    n += 1
                except Exception:
                    pass
        except Exception:
            pass
        if size_lbl is not None:
            self._update_tts_cache_size_label(size_lbl)
        self._refresh_bookshelf()
        messagebox.showinfo("已清除", f"已清除 {n} 个音频缓存目录。")

    # ================= TTS =================
    def _tts_toggle(self):
        """开始 / 暂停 / 继续 切换。"""
        if not self.book:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        if self.tts.is_playing():
            self.tts.pause()
            self._set_tts_ui("paused")
        elif self.tts.is_paused():
            self.tts.resume()
            self._set_tts_ui("playing")
        else:
            self.tts.start(self.book, self.chapter_idx, self.char_offset)
            self._set_tts_ui("playing")

    def _tts_stop(self):
        self.tts.stop()
        self._set_tts_ui("stopped")
        self._clear_highlight()

    def _flash_status(self, msg):
        if self._status_timer:
            self.root.after_cancel(self._status_timer)
        self.tts_status_label.configure(text=msg)
        self._status_timer = self.root.after(4000, lambda: self.tts_status_label.configure(text=""))

    def _set_tts_ui(self, state):
        if state == "playing":
            self.tts_toggle_btn.configure(text="⏸ 暂停", state="normal")
            self.tts_stop_btn.configure(state="normal")
        elif state == "paused":
            self.tts_toggle_btn.configure(text="▶ 继续", state="normal")
            self.tts_stop_btn.configure(state="normal")
        else:
            self.tts_toggle_btn.configure(text="▶ 开始朗读", state="normal")
            self.tts_stop_btn.configure(state="disabled")

    # ================= 定时停止朗读 =================
    def _open_timer_dialog(self):
        """定时停止播放：以分钟为单位自填数字，到点自动停止。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("定时停止朗读")
        dlg.geometry("340x180")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        tk.Label(dlg, text="设置定时时长（分钟）：", font=("微软雅黑", 10)).pack(pady=(16, 6))
        var = tk.IntVar(value=30)
        sp = tk.Spinbox(dlg, from_=1, to=600, textvariable=var, width=8, font=("微软雅黑", 11))
        sp.pack()
        if self._timer_running:
            tk.Label(dlg, text="（当前已有定时，重新设置将覆盖）", fg="#cc6600",
                     font=("微软雅黑", 8)).pack(pady=(6, 0))

        def do_start():
            mins = max(1, var.get())
            self._timer_minutes = mins
            self._timer_deadline = time.time() + mins * 60
            self._timer_running = True
            self._flash_status(f"已设定 {mins} 分钟定时，到点自动停止朗读")
            dlg.destroy()

        def do_cancel():
            self._timer_running = False
            self._timer_deadline = None
            self.timer_btn.configure(text="定时")
            self._flash_status("已取消定时")
            dlg.destroy()

        ops = tk.Frame(dlg)
        ops.pack(pady=12)
        tk.Button(ops, text="开始", width=8, command=do_start).pack(side="left", padx=8)
        tk.Button(ops, text="取消定时", width=10, command=do_cancel).pack(side="left", padx=8)

    def _tick_timer(self):
        """每秒轮询：到点停止朗读，并刷新定时按钮倒计时。"""
        try:
            if self._timer_running and self._timer_deadline:
                remain = self._timer_deadline - time.time()
                if remain <= 0:
                    mins = self._timer_minutes
                    self._timer_running = False
                    self._timer_deadline = None
                    self.timer_btn.configure(text="定时")
                    self._tts_stop()
                    self._flash_status(f"定时时间到，已停止朗读（本次定时 {mins} 分钟）")
                else:
                    m, s = divmod(int(remain), 60)
                    self.timer_btn.configure(text=f"定时 {m:02d}:{s:02d}")
            else:
                self.timer_btn.configure(text="定时")
        except Exception:
            pass
        try:
            self.root.after(1000, self._tick_timer)
        except Exception:
            pass

    def _poll_tts(self):
        try:
            for evt in self.tts.drain():
                self._handle_tts_event(evt)
        except Exception:
            pass
        self._update_tts_cache_label()
        self._update_book_cache_ui()
        self.root.after(100, self._poll_tts)

    def _update_book_cache_ui(self):
        """更新「整本缓存」按钮与状态栏进度，并实时持久化缓存大小。"""
        try:
            st = self.tts.book_cache_status()
        except Exception:
            st = None
        if st and self.current_bid:
            try:
                bid = self.current_bid
                total = getattr(self, "_book_cache_base_size", 0) + int(
                    st.get("bytes_written", 0)
                )
                self._shelf_size_cache[bid] = total
                state = st["state"]
                now = time.time()
                last = getattr(self, "_book_cache_last_persist", 0)
                if state in ("paused", "done", "cancelled"):
                    self.storage.set_book_cache_size(bid, total)
                    self._book_cache_last_persist = now
                elif state == "caching" and now - last > 5:
                    # 缓存进行中每 5 秒持久化一次，避免频繁写 library.json
                    self.storage.set_book_cache_size(bid, total)
                    self._book_cache_last_persist = now
            except Exception:
                pass
        if not st:
            self.tts_cache_btn.configure(text="整本缓存")
            self.book_cache_label.configure(text="")
            return
        state, done, total = st["state"], st["done"], st["total"]
        if state == "caching":
            pct = (done / total * 100) if total else 0
            self.tts_cache_btn.configure(text=f"缓存中 {pct:.0f}%")
            self.book_cache_label.configure(
                text=f"整本缓存 {done}/{total}（点击暂停）",
                fg="#8a5a00",
                font=("微软雅黑", 9),
            )
        elif state == "paused":
            pct = (done / total * 100) if total else 0
            self.tts_cache_btn.configure(text=f"已暂停 {pct:.0f}%")
            # 暂停：醒目加粗 + 高对比色提示
            self.book_cache_label.configure(
                text=f"⏸ 缓存已暂停 {done}/{total}（点击继续）",
                fg="#c0392b",
                font=("微软雅黑", 9, "bold"),
            )
        elif state == "done":
            self.tts_cache_btn.configure(text="整本缓存")
            self.book_cache_label.configure(
                text=f"整本缓存完成 {total} 句",
                fg="#8a5a00",
                font=("微软雅黑", 9),
            )
            if not getattr(self, "_book_cache_done_flashed", False):
                self._book_cache_done_flashed = True
                self._flash_status("整本语音缓存完成，朗读将零网络延迟")
        elif state == "cancelled":
            self.tts_cache_btn.configure(text="整本缓存")
            self.book_cache_label.configure(text="", fg="#8a5a00", font=("微软雅黑", 9))
        else:
            self.tts_cache_btn.configure(text="整本缓存")
            self.book_cache_label.configure(text="", fg="#8a5a00", font=("微软雅黑", 9))

    def _open_cache_dialog(self):
        """整本语音缓存管理窗口：开始/暂停/继续/章节选择/容量/自动关机。"""
        if not self.book:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        if getattr(self, "_cache_dlg", None) and self._cache_dlg.winfo_exists():
            try:
                self._cache_dlg.lift()
            except Exception:
                pass
            return

        win = tk.Toplevel(self.root)
        win.title("整本语音缓存")
        win.geometry("660x740")
        win.minsize(600, 620)
        win.transient(self.root)
        self._center_window(win)
        self._cache_dlg = win
        self._cache_sel = {}
        self._cache_busy = True

        # 顶部信息
        info = f"书籍：{self.book.title}    章节：{len(self.book.chapters)}"
        tk.Label(win, text=info, anchor="w", font=("微软雅黑", 10, "bold")).pack(
            fill="x", padx=12, pady=(10, 2)
        )
        self._cache_info2 = tk.Label(
            win,
            text="",
            anchor="w",
            fg="#666666",
            font=("微软雅黑", 9),
        )
        self._cache_info2.pack(fill="x", padx=12)

        # 进度条上方的使用警示说明
        tk.Label(
            win,
            text="整本缓存功能用于网络不稳定时提前缓存减少卡顿；缓存音频体积较大，请慎用。",
            fg="#b00020",
            font=("微软雅黑", 9),
            anchor="w",
            justify="left",
            wraplength=600,
        ).pack(fill="x", padx=12, pady=(8, 0))
        # 进度条
        self._cache_bar = ttk.Progressbar(win, maximum=100, value=0)
        self._cache_bar.pack(fill="x", padx=12, pady=(6, 4))
        self._cache_prog = tk.Label(win, text="尚未开始", anchor="w", font=("微软雅黑", 9))
        self._cache_prog.pack(fill="x", padx=12)
        self._cache_disk = tk.Label(win, text="缓存已占容量：0 MB", anchor="w", fg="#1a6bbd", font=("微软雅黑", 9))
        self._cache_disk.pack(fill="x", padx=12, pady=(2, 4))

        # 章节选择
        tk.Label(win, text="选择要缓存的章节（Ctrl/Shift 可多选）：", anchor="w").pack(
            fill="x", padx=12, pady=(8, 2)
        )
        sel_frame = tk.Frame(win)
        sel_frame.pack(fill="both", expand=True, padx=12)
        lb = tk.Listbox(sel_frame, selectmode="extended", activestyle="dotbox", font=("微软雅黑", 9))
        sb = tk.Scrollbar(sel_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for ch in self.book.chapters:
            lb.insert("end", ch.title)
        self._cache_lb = lb

        # 章节选择快捷按钮
        btns = tk.Frame(win)
        btns.pack(fill="x", padx=12, pady=6)
        cur = getattr(self, "chapter_idx", 0)
        tk.Button(btns, text="全选", width=8, command=lambda: self._cache_select_all()).pack(side="left")
        tk.Button(btns, text="反选", width=8, command=lambda: self._cache_select_invert()).pack(side="left", padx=4)
        tk.Button(btns, text="从本章起", width=8, command=lambda: self._cache_select_from(cur)).pack(side="left", padx=4)
        tk.Label(btns, text="续传：已缓存过的句子自动跳过，无需重复下载", fg="#888888", font=("微软雅黑", 8)).pack(side="right")

        # 自动关机
        self._cache_shutdown_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            win,
            text="缓存完成后自动关机（60 秒倒计时，可运行 shutdown /a 取消）",
            variable=self._cache_shutdown_var,
            command=self._cache_sync_shutdown,
        ).pack(anchor="w", padx=12, pady=(2, 2))

        # 操作按钮
        ops = tk.Frame(win)
        ops.pack(fill="x", padx=12, pady=(6, 10))
        self._cache_start_btn = tk.Button(ops, text="开始缓存", width=10, command=self._cache_start)
        self._cache_start_btn.pack(side="left")
        self._cache_continue_btn = tk.Button(ops, text="继续上次下载", width=12, command=self._cache_continue)
        self._cache_continue_btn.pack(side="left", padx=6)
        self._cache_pause_btn = tk.Button(ops, text="暂停", width=8, command=self._cache_pause, state="disabled")
        self._cache_pause_btn.pack(side="left", padx=6)
        self._cache_resume_btn = tk.Button(ops, text="继续", width=8, command=self._cache_resume, state="disabled")
        self._cache_resume_btn.pack(side="left")
        tk.Button(ops, text="关闭", width=8, command=win.destroy).pack(side="right")

        # 初始默认全选当前书
        self._cache_select_all()
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.after(300, self._cache_tick, win)
        self._cache_sync_state()

    # ---------- 缓存窗口辅助 ----------
    def _cache_selected(self):
        """返回选中的章节索引集合；全选时返回 None（表示全部）。"""
        n = len(self.book.chapters)
        try:
            sel = set(map(int, self._cache_lb.curselection()))
        except Exception:
            sel = set()
        if len(sel) >= n:
            return None
        return sel

    def _cache_select_all(self):
        self._cache_lb.selection_clear(0, "end")
        self._cache_lb.selection_set(0, "end")

    def _cache_select_none(self):
        self._cache_lb.selection_clear(0, "end")

    def _cache_select_invert(self):
        """反选：翻转当前章节选中状态。"""
        n = self._cache_lb.size()
        cur = set(self._cache_lb.curselection())
        self._cache_lb.selection_clear(0, "end")
        for i in range(n):
            if i not in cur:
                self._cache_lb.selection_set(i)

    def _cache_select_from(self, ci):
        self._cache_lb.selection_clear(0, "end")
        if ci < self._cache_lb.size():
            self._cache_lb.selection_set(ci, "end")

    def _cache_sync_shutdown(self):
        try:
            self.tts.set_book_cache_auto_shutdown(self._cache_shutdown_var.get())
        except Exception:
            pass

    def _cache_start(self):
        if not self.book:
            return
        idx = self._cache_selected()
        if idx == set() or (idx is not None and len(idx) == 0):
            messagebox.showinfo("提示", "请先选择至少一个要缓存的章节", parent=self._cache_dlg)
            return
        st = self.tts.start_book_cache(self.book, self.current_bid, idx)
        if st is None:
            return
        self._book_cache_base_size = self.storage.book_cache_size(self.current_bid)
        if st["state"] == "unsupported":
            messagebox.showinfo(
                "提示",
                "整本缓存仅支持 Edge 神经语音（如晓晓/云希等）。\n"
                "请在「语音」下拉框选择一个 Edge 音色后再试。",
                parent=self._cache_dlg,
            )
            return
        if st["state"] == "unavailable":
            messagebox.showinfo("提示", "当前书籍暂无可缓存的章节", parent=self._cache_dlg)
            return
        self._book_cache_done_flashed = False
        self._cache_sync_shutdown()
        self._cache_sync_state()

    def _cache_pause(self):
        self.tts.pause_book_cache()
        self._cache_sync_state()

    def _cache_resume(self):
        self.tts.resume_book_cache()
        self._cache_sync_state()

    def _cache_continue(self):
        """继续上次下载：若有暂停中的任务则恢复；否则从持久化进度精确续传。"""
        try:
            st = self.tts.book_cache_status()
        except Exception:
            st = None
        if st and st["state"] == "paused":
            self._cache_resume()
            self._cache_sync_state()
            return
        # 无进行中任务：从持久化进度继续（progress.json 记录已完成任务索引）
        if not self.book:
            return
        # 全选所有章节（resume 模式会自动跳过已完成的任务）
        self._cache_select_all()
        st = self.tts.start_book_cache(self.book, self.current_bid, None, resume=True)
        if st is None:
            return
        self._book_cache_base_size = self.storage.book_cache_size(self.current_bid)
        if st.get("state") == "unsupported":
            messagebox.showinfo(
                "提示",
                "整本缓存仅支持 Edge 神经语音（如晓晓/云希等）。\n"
                "请在「语音」下拉框选择一个 Edge 音色后再试。",
                parent=self._cache_dlg,
            )
            return
        if st.get("state") == "unavailable":
            messagebox.showinfo("提示", "当前书籍暂无可缓存的章节", parent=self._cache_dlg)
            return
        if st.get("state") == "done":
            messagebox.showinfo("提示", "所有章节均已缓存完成。", parent=self._cache_dlg)
            return
        self._book_cache_done_flashed = False
        self._cache_sync_shutdown()
        self._cache_sync_state()

    def _cache_sync_state(self):
        try:
            st = self.tts.book_cache_status()
        except Exception:
            st = None
        state = st["state"] if st else None
        if state == "caching":
            self._cache_start_btn.configure(state="disabled")
            self._cache_continue_btn.configure(state="disabled")
            self._cache_pause_btn.configure(state="normal")
            self._cache_resume_btn.configure(state="disabled")
        elif state == "paused":
            self._cache_start_btn.configure(state="disabled")
            self._cache_continue_btn.configure(state="normal")
            self._cache_pause_btn.configure(state="disabled")
            self._cache_resume_btn.configure(state="normal")
        else:
            self._cache_start_btn.configure(state="normal")
            self._cache_continue_btn.configure(state="normal")
            self._cache_pause_btn.configure(state="disabled")
            self._cache_resume_btn.configure(state="disabled")

    def _cache_tick(self, win):
        """缓存窗口周期刷新进度/容量/按钮状态。"""
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        try:
            st = self.tts.book_cache_status()
        except Exception:
            st = None
        if st:
            state, done, total = st["state"], st["done"], st["total"]
            if total:
                pct = done / total * 100
                self._cache_bar.configure(value=pct)
            else:
                pct = 0
            if state == "caching":
                self._cache_prog.configure(text=f"正在缓存 {done}/{total}（{pct:.1f}%）…")
            elif state == "paused":
                self._cache_prog.configure(text=f"已暂停 {done}/{total}（{pct:.1f}%）")
            elif state == "done":
                self._cache_prog.configure(text=f"全部完成：共 {done}/{total} 句")
            elif state == "cancelled":
                self._cache_prog.configure(text="已取消")
            else:
                self._cache_prog.configure(text=f"已就绪（现有 {done}/{total} 句，可续传）")
            disk = self.tts.book_cache_disk_used()
            self._cache_disk.configure(text=f"缓存已占容量：{disk / 1048576:.1f} MB")
        else:
            self._cache_prog.configure(text="尚未开始")
        self._cache_sync_state()
        try:
            if win.winfo_exists():
                win.after(300, self._cache_tick, win)
        except Exception:
            pass

    def _on_status_cache_click(self, event=None):
        """点击右下角整本缓存状态：在 暂停/继续 之间切换下载。"""
        try:
            st = self.tts.book_cache_status()
        except Exception:
            st = None
        if not st:
            return
        if st["state"] == "caching":
            self.tts.pause_book_cache()
            self._flash_status(f"已暂停整本缓存（{st['done']}/{st['total']}），点击状态可继续")
        elif st["state"] == "paused":
            self.tts.resume_book_cache()
            self._flash_status("已继续整本缓存下载")

    def _open_percent_dialog(self):
        """点击右下角百分比：弹窗手动输入百分比并跳转。"""
        if not self.book:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("跳转到进度")
        dlg.geometry("300x150")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        cur = self._compute_percent(self.chapter_idx, self.char_offset)
        tk.Label(dlg, text=f"当前进度 {cur:.1f}%，输入目标百分比（0~100）：",
                 font=("微软雅黑", 10)).pack(pady=(16, 6))
        var = tk.DoubleVar(value=round(cur, 1))
        sp = tk.Spinbox(dlg, from_=0.0, to=100.0, increment=0.1, textvariable=var, width=10,
                        font=("微软雅黑", 11))
        sp.pack()
        ops = tk.Frame(dlg)
        ops.pack(pady=12)

        def ok():
            try:
                pct = float(var.get())
            except Exception:
                pct = cur
            pct = max(0.0, min(100.0, pct))
            dlg.destroy()
            self._seek_percent(pct)

        tk.Button(ops, text="跳转", width=8, command=ok).pack(side="left", padx=8)
        tk.Button(ops, text="取消", width=8, command=dlg.destroy).pack(side="left", padx=8)

    def _seek_percent(self, pct):
        """按百分比跳转到对应章节/位置。"""
        if not self.book or self.book.total_chars <= 0:
            return
        pct = max(0.0, min(100.0, float(pct)))
        target = self.book.total_chars * pct / 100.0
        import bisect
        ci = bisect.bisect_right(self.book.cum, target) - 1
        ci = max(0, min(ci, len(self.book.chapters) - 1))
        off = int(target - self.book.cum[ci])
        off = max(0, min(off, len(self.book.chapters[ci].content)))
        self._goto_chapter(ci, off)
        self._flash_status(f"已跳转到全书 {pct:.1f}%")

    def _update_tts_cache_label(self):
        """右下角显示 Edge 语音预取缓存进度（如 30/30）；本地语音/未朗读时留空。"""
        try:
            pr = self.tts.prefetch_progress()
        except Exception:
            pr = None
        if pr:
            self.tts_cache_label.configure(text=f"语音缓存 {pr[0]}/{pr[1]}")
        else:
            self.tts_cache_label.configure(text="")

    def _handle_tts_event(self, evt):
        t = evt["type"]
        if t == "sentence_start":
            ci, off, text = evt["chapter_idx"], evt["char_offset"], evt["text"]
            if ci != self.chapter_idx or self.book is None:
                self.chapter_idx = ci
                self.char_offset = off
                self._render_chapter()
            self._highlight_sentence(ci, off, text)
        elif t == "sentence_done":
            ci, off = evt["chapter_idx"], evt["char_offset"]
            self.chapter_idx = ci
            self.char_offset = off
            self._update_status()
            self._schedule_save()
        elif t == "finished":
            self._set_tts_ui("stopped")
            self._clear_highlight()
            if self.book:
                self.char_offset = len(self.book.chapters[self.chapter_idx].content)
                self._update_status()
                self._save_now()
        elif t == "stopped":
            if not self.tts.is_active():
                self._set_tts_ui("stopped")
                self._clear_highlight()
        elif t == "error":
            # 非阻塞提示。注意：Edge 联网失败会回退本地语音并继续朗读，
            # 此时不能把按钮重置为“开始朗读”，真正的结束由 “stopped” 事件统一处理。
            self._flash_status(evt.get("message", "朗读出错"))

    def _transformed_pos(self, content, off):
        """把原始正文的字符偏移映射到渲染文本（压缩空行后）中的 (行, 列)。

        空行模式会改变换行数量，导致按原文计算的 行/列 在渲染文本中错位；
        朗读高亮、滚动定位都必须经过此映射才能落在正确位置。
        """
        before = content[:off]
        nl_before = before.count("\n")
        mode = int(self.settings.get("paragraph_mode", 1))
        if mode == 3:
            # 清理所有行：所有换行被删除，正文只有第 1 行；加上标题占的行数
            return self._body_start_line, off - nl_before
        line = nl_before + 1
        last_nl = before.rfind("\n")
        col = off - (last_nl + 1) if last_nl >= 0 else off
        if mode == 2:
            # 合并为一行：每个段落间多一个空行，原始第 L 段 → 渲染第 2L-1 行
            line = line * 2 - 1
        # 加上章节标题占的行数，得到显示文本中的行号
        return line + self._body_start_line - 1, col

    def _highlight_sentence(self, ci, off, text):
        content = self.book.chapters[ci].content
        line, col = self._transformed_pos(content, off)
        start = f"{line}.{col}"
        # 结束位置：直接从 start 在显示文本中向后读取，遇到句末标点即停。
        # （显示文本保留标点/空白，纯净朗读文本去除了它们，长度不一致，
        #   不能直接用 len(text) 定位结束。）
        end = start
        try:
            total_len = len(text) + 30  # 显示文本可能多出标点，留足余量
            line_end = self.text.index(f"{start} lineend")
            window_end = self.text.index(f"{start}+{total_len}c")
            # 取 start 到（行尾与 start+len 的较小者）之间的字符
            probe_end = self.text.index(f"{start} lineend")
            probe_end = self.text.index(f"{start}+{total_len}c") \
                if self.text.compare(f"{start}+{total_len}c", "<", line_end) \
                else line_end
            window = self.text.get(start, probe_end)
            for i, ch in enumerate(window):
                if ch in "。！？.!?；;\n":
                    end = self.text.index(f"{start}+{i+1}c")
                    break
        except Exception:
            end = start
        self.text.tag_remove("tts", "1.0", "end")
        self.text.tag_add("tts", start, end)
        self._highlight_index = (ci, start)
        self._pin_highlight_top(start)

    def _pin_highlight_top(self, start):
        """把高亮所在显示行钉到窗口第一行（兼容所有空行模式）。

        模式1/2（每段独占一行）直接用 Tk 原生 yview(index) 整行滚动，天然精确；
        模式3（清理所有行）全文只有一行，按行号滚动永远停在文首、高亮无法跟随，
        改用「先 see 保证可见 → dlineinfo 测该行像素偏移 → yview fraction 换算
        滚动量」，把高亮所在显示行钉到窗口第一行。
        """
        try:
            mode = int(self.settings.get("paragraph_mode", 1))
            if mode != 3:
                top = self.text.index(f"{start} linestart")
                self.text.yview(top)
                return
            self.text.see(start)
            dline = self.text.dlineinfo(start)
            if not dline:
                return
            pady = 0
            try:
                pady = int(float(self.text.cget("pady")))
            except Exception:
                pady = 0
            delta = dline[1] - pady  # 该显示行距视口顶部还需上滚的像素
            if delta <= 0:
                return
            first, last = self.text.yview()
            span = last - first
            viewport_px = self.text.winfo_height()
            if span <= 0 or viewport_px <= 0:
                return
            total_px = viewport_px / span  # 全文像素总高度
            self.text.yview_moveto(max(0.0, min(1.0, first + delta / total_px)))
        except Exception:
            try:
                self.text.see(start)
            except Exception:
                pass

    def _repin_reading(self):
        """朗读中窗口/字号变化后，重新把当前高亮钉回窗口第一行。"""
        if not self.book or not self.tts.is_playing():
            return
        hi = getattr(self, "_highlight_index", None)
        if hi and hi[0] == self.chapter_idx and hi[1]:
            self._pin_highlight_top(hi[1])

    def _clear_highlight(self):
        self._highlight_index = None
        try:
            self.text.tag_remove("tts", "1.0", "end")
        except Exception:
            pass

    def _change_rate(self, delta):
        rate = max(80, min(400, int(self.settings.get("tts_rate", 200)) + delta))
        self.settings["tts_rate"] = rate
        self.rate_label.configure(text=str(rate))
        self.tts.set_rate(rate)
        self.storage.set_setting("tts_rate", rate)

    def _on_voice_change(self, event):
        idx = self.voice_cb.current()
        if idx < 0 or not getattr(self, "_voice_ids", None):
            return
        voice_id = self._voice_ids[idx]
        self.settings["tts_voice"] = voice_id
        self.tts.set_voice(voice_id)
        self.storage.set_setting("tts_voice", voice_id)

    # ================= 关闭 =================
    # ================= 快捷键 =================
    def _bind_shortcuts(self):
        r = self.root
        r.bind("<Control-o>", lambda e: self._add_book())
        r.bind("<Control-O>", lambda e: self._add_book())
        r.bind("<Control-b>", lambda e: self._toggle_shelf())
        r.bind("<Control-B>", lambda e: self._toggle_shelf())
        r.bind("<Control-plus>", lambda e: self._change_font_size(1))
        r.bind("<Control-equal>", lambda e: self._change_font_size(1))
        r.bind("<Control-minus>", lambda e: self._change_font_size(-1))
        r.bind("<Control-0>", lambda e: self._reset_font())
        r.bind("<Control-1>", lambda e: self._set_theme(list(THEMES.keys())[0]))
        r.bind("<Control-2>", lambda e: self._set_theme(list(THEMES.keys())[1]))
        r.bind("<Control-3>", lambda e: self._set_theme(list(THEMES.keys())[2]))
        r.bind("<F11>", lambda e: self._toggle_fullscreen())
        r.bind("<Alt-Return>", lambda e: self._toggle_fullscreen())
        r.bind("<Escape>", lambda e: self._exit_fullscreen())
        # Space 在阅读区触发朗读切换，并阻止输入空格
        self.text.bind("<space>", self._shortcut_tts_toggle)
        # 定时停止朗读的秒级轮询（常驻）
        self.root.after(1000, self._tick_timer)

    def _shortcut_tts_toggle(self, event=None):
        self._tts_toggle()
        return "break"

    def _reset_font(self):
        size = 17
        self.settings["font_size"] = size
        self.size_label.configure(text=str(size))
        self._apply_font()
        self.storage.set_setting("font_size", size)

    def _set_theme(self, theme):
        self.settings["theme"] = theme
        self._apply_theme(theme)
        self.storage.set_setting("theme", theme)
        try:
            self.theme_cb.set(theme)
        except Exception:
            pass

    # ================= 书架收起/展开 =================
    def _toggle_shelf(self):
        try:
            pane_paths = [str(p) for p in self._inner_paned.panes()]
        except Exception:
            pane_paths = []
        if str(self._left) in pane_paths:
            self._inner_paned.remove(self._left)
            self._shelf_visible = False
        else:
            self._inner_paned.insert(0, self._left, weight=0)
            self._shelf_visible = True

    # ================= 全屏模式 =================
    def _toggle_fullscreen(self, event=None):
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self):
        self._fullscreen = True
        self.root.attributes("-fullscreen", True)
        self._toolbar.grid_remove()
        self._shelf_was_visible = getattr(self, '_shelf_visible', False)
        try:
            pane_paths = [str(p) for p in self._inner_paned.panes()]
            if str(self._left) in pane_paths:
                self._inner_paned.remove(self._left)
                self._shelf_visible = False
        except Exception:
            pass
        self._overlay.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._tick_overlay()

    def _exit_fullscreen(self, event=None):
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.root.attributes("-fullscreen", False)
        self._overlay.grid_remove()
        self._toolbar.grid()
        if getattr(self, '_shelf_was_visible', False):
            try:
                pane_paths = [str(p) for p in self._inner_paned.panes()]
                if str(self._left) not in pane_paths:
                    self._inner_paned.insert(0, self._left, weight=0)
                    self._shelf_visible = True
            except Exception:
                pass

    def _tick_overlay(self):
        try:
            if self._fullscreen:
                # 阅读时间 = 自本次软件启动起的后台累计（与是否打开书/全屏无关）
                read_seconds = int(time.time() - self._app_start)
                now = time.strftime("%H:%M:%S")
                m, s = divmod(read_seconds, 60)
                hh, mm = divmod(m, 60)
                read_s = f"{hh}:{mm:02d}:{s:02d}" if hh else f"{mm:02d}:{s:02d}"
                chap = "-"
                pct = 0.0
                if self.book:
                    chap = self.book.chapters[self.chapter_idx].title
                    pct = self._compute_percent(self.chapter_idx, self.char_offset)
                self._ov_time.configure(text=now)
                self._ov_read.configure(text=f"阅读时间  {read_s}")
                self._ov_chap.configure(text=chap[:30])
                self._ov_prog.configure(text=f"总进度  {pct:.1f}%")
        except Exception:
            pass
        self.root.after(1000, self._tick_overlay)

    def _on_close(self):
        self.tts.stop()
        try:
            # 关闭时自动暂停音频缓存：保留进度，下次可「继续上次下载」，避免无用功
            self.tts.pause_book_cache()
        except Exception:
            pass
        # 关闭正在进行的批量导入
        try:
            self._import_worker_stop.set()
        except Exception:
            pass
        try:
            if self._fullscreen:
                self._exit_fullscreen()
        except Exception:
            pass
        try:
            self._save_now()
        except Exception:
            pass
        try:
            self.storage.set_setting("window_geometry", self.root.geometry())
        except Exception:
            pass
        self.root.destroy()
