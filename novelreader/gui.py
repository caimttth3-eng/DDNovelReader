# -*- coding: utf-8 -*-
"""主界面基类：启动加载 + UI 构建 + 公共工具（从 gui.py 拆分的 Mixin 之一）。"""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

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
from .constants import (
    THEMES,
    UI_THEMES,
    FILE_TYPES,
    _PREFERRED_FONTS,
    CF_HDROP,
    GMEM_MOVEABLE,
    GMEM_ZEROINIT,
    _copy_files_to_clipboard,
)


class NovelReaderBase:
    """基类：启动加载、UI 构建、公共缓存统计与工具方法。"""
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
        self._resize_timer = None
        self._pending_seek_pct = None
        self._status_scroll_timer = None

        self._build_ui()
        self._apply_settings_to_ui()
        self._refresh_bookshelf()
        self._bind_shortcuts()

        # 定时轮询 TTS 事件（工作线程 → UI）
        self.root.after(100, self._poll_tts)
        # 全屏悬浮条每秒刷新
        self.root.after(1000, self._tick_overlay)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 先让窗口显示：阅读区放加载占位并立即布局。若在窗口未映射时调用
        # Text.see() 会触发全量布局重算（实测可达 9-11 秒），造成启动白屏卡顿；
        # 窗口先显示、再加载，see() 即恢复正常（毫秒级），从根本上解决启动慢。
        self._show_loading_placeholder()
        try:
            self.root.update_idletasks()
            self.root.update()
        except Exception:
            pass

        # 异步启动加载：窗口已显示（Text 已布局）后再打开上次书/刷新书架，秒开不白屏
        self._loading_done = False
        self.root.after(30, self._startup_load)
    def _show_loading_placeholder(self):
        """阅读区显示加载占位文字（启动动画底稿），窗口先显示不白屏。"""
        try:
            self.text.tag_configure("loading_text",
                                    font=("微软雅黑", 20, "bold"),
                                    foreground="#9a9a9a", justify="center")
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "\n\n\n\n\n正在加载书籍", "loading_text")
            self.text.configure(state="disabled")
        except Exception:
            pass
    def _animate_loading(self, n=0):
        """启动加载动画：省略号循环刷新阅读区占位文字。"""
        if getattr(self, "_loading_done", False):
            return
        try:
            if not self.text.winfo_exists():
                return
            dots = "." * (n % 4)
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", f"\n\n\n\n\n正在加载书籍{dots}", "loading_text")
            self.text.configure(state="disabled")
        except Exception:
            pass
        self.root.after(180, lambda: self._animate_loading(n + 1))
    def _startup_load(self):
        """窗口显示后的后台启动加载：打开上次书 + 刷新书架（Text 已布局，see() 快）。"""
        try:
            self._animate_loading()
            if self.settings.get("auto_open_last", True):
                last = self.storage.get_setting("last_book")
                if last and last in self.storage.all_books():
                    self.open_book(last)   # 内部会 _refresh_bookshelf
            else:
                self.root.title(f"多多朗读 v{__version__}")
                self._refresh_bookshelf()
        finally:
            self._loading_done = True
        # 启动后台：一次性迁移旧音频缓存目录结构 + 校准大小索引（不阻塞 UI）
        self._migrate_tts_cache_bg()
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
        """默认窗口 1280x720 并居中显示（每次启动固定）。"""
        try:
            w, h = 1280, 720
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            return f"{w}x{h}+{x}+{y}"
        except Exception:
            return "1280x720+0+0"
    def _effective_tts_cache_root(self):
        """当前生效的整本语音缓存根目录（优先自定义，空则默认）。

        直接读 storage 设置，避免依赖 self.settings 的初始化顺序。
        """
        custom = self.storage.get_setting("tts_cache_dir") or ""
        return resolve_tts_cache_dir(custom)
    def _audio_cache_size(self, bid):
        """某本书的音频缓存总字节数（跨语音/语速目录）。

        读大小索引，不遍历数万细碎 mp3 文件。
        """
        try:
            return self.tts.tts_cache_size(bid)
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
    def _init_shelf_size_cache(self):
        if not hasattr(self, "_shelf_size_cache"):
            self._shelf_size_cache = {}  # bid -> size_bytes
            self._shelf_size_worker = None
            self._shelf_size_stop = threading.Event()
    def _migrate_tts_cache_bg(self):
        """启动后台一次性任务：迁移旧音频缓存目录结构 + 校准大小索引。

        旧结构 `tts_cache/<语音>/<语速>/<book_id>/` → 新结构 `tts_cache/<book_id>/<语音>/<语速>/`。

        仅当大小索引文件（.tts_sizes.json）不存在时才执行迁移 + 校准；索引文件已存在
        说明结构与索引均已就位（缓存中由增量 bump / invalidate 维护），直接跳过，
        避免每次启动遍历数万细碎 mp3 造成硬盘持续读取。
        """
        def worker():
            try:
                from .storage import tts_size_index_path, migrate_old_tts_layout
                root = self._effective_tts_cache_root()
                if os.path.isfile(tts_size_index_path(root)):
                    return  # 索引已就位，无需迁移 / 校准
                moved = migrate_old_tts_layout(root)
                if moved:
                    self.root.after(0, lambda: self._flash_status(
                        f"音频缓存目录已迁移 {moved} 个到新版（按书分目录）结构"))
                self.tts.tts_cache_calibrate()
                # 校准完成后刷新书架大小列（读索引，快）
                self.root.after(0, lambda: self._refresh_shelf_sizes_async())
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()
    def _refresh_shelf_sizes_async(self):
        """后台线程计算书架中所有书籍的缓存大小，完成后逐行更新 Treeview。"""
        self._init_shelf_size_cache()
        # 如果已有 worker 在运行，标记停止但只短暂等待（旧 worker 是 daemon，
        # 由 stop 标记自然退出），避免每次刷新书架阻塞主线程等它
        if self._shelf_size_worker and self._shelf_size_worker.is_alive():
            self._shelf_size_stop.set()
            self._shelf_size_worker.join(timeout=0.05)
        self._shelf_size_stop.clear()

        books = self.storage.all_books()
        # 全部书快速刷新：音频大小读索引、文本大小单文件 getsize，零扫盘，因此每次刷新都精确
        bids = list(books.keys())
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
        # 每次启动固定 1280x720 并居中
        self.root.geometry(self._default_geometry())
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
        back_row = tk.Frame(left)
        back_row.pack(fill="x")
        ttk.Button(back_row, text="备份书架", command=self._backup_library).pack(
            side="left", fill="x", expand=True, pady=2, padx=(0, 2))
        ttk.Button(back_row, text="还原书架", command=self._restore_library).pack(
            side="left", fill="x", expand=True, pady=2, padx=(2, 0))
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
        self.text.tag_configure("bm", background="#C8E6C9", foreground="#000000")

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
        self.text_menu.add_command(label="🔖 添加书签/划线", command=self._add_bookmark_from_selection)
        self.text_menu.add_command(label="从该段开始朗读", command=self._read_from_paragraph)
        self.text_menu.add_separator()
        self.text_menu.add_command(label="百度搜索", command=lambda: self._search_selection("baidu"))
        self.text_menu.add_command(label="谷歌搜索", command=lambda: self._search_selection("google"))
        self.text_menu.add_command(label="必应搜索", command=lambda: self._search_selection("bing"))
        self.text_menu.add_separator()
        self.text_menu.add_command(label="翻译", command=lambda: self._search_selection("translate"))

        # 目录（内层 PanedWindow 的第二个窗格，可开关）
        self.chapter_panel = tk.Frame(self._inner_paned, width=220)
        # 顶部：目录 / 书签 切换按钮（与目录共用同一面板区域）
        panel_bar = tk.Frame(self.chapter_panel)
        panel_bar.pack(fill="x")
        self._panel_toc_btn = tk.Button(panel_bar, text="目录", relief="sunken",
                                        command=lambda: self._set_panel_mode("toc"))
        self._panel_toc_btn.pack(side="left", fill="x", expand=True)
        self._panel_bm_btn = tk.Button(panel_bar, text="书签", relief="raised",
                                       command=lambda: self._set_panel_mode("bookmark"))
        self._panel_bm_btn.pack(side="left", fill="x", expand=True)
        self._panel_mode = "toc"
        self.chapter_list = tk.Listbox(self.chapter_panel, font=("微软雅黑", 10))
        csb = self._make_scrollbar(self.chapter_panel, self.chapter_list.yview)
        self.chapter_list.configure(yscrollcommand=csb.set)
        csb.pack(side="right", fill="y")
        self.chapter_list.pack(side="left", fill="both", expand=True)
        self.chapter_list.bind("<<ListboxSelect>>", self._on_panel_select)
        self.chapter_list.bind("<Button-3>", self._popup_bookmark_menu)
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
        ttk.Button(row2, text="🔍 搜索", command=self._open_search_dialog).pack(side="left", padx=(8, 0))

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
    def _make_scrollbar(self, parent, command, orient="vertical"):
        from .constants import make_scrollbar as _ms
        dark = (self.settings.get("page_theme", "白天") == "夜间")
        sb = _ms(parent, command, orient=orient, dark=dark)
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
    def _format_bytes(self, n):
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / 1024 / 1024:.2f} MB"
    def _on_close(self):
        self.tts.stop()
        try:
            # 关闭时自动暂停所有书的音频缓存：保留进度，下次可「继续上次下载」，避免无用功
            self.tts.pause_all_book_cache()
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

# ============ Mixin 组装 ============
from .shelf_ui import ShelfMixin
from .import_ui import ImportMixin
from .reader_ui import ReaderMixin
from .theme_ui import ThemeMixin
from .tts_ui import TtsMixin
from .dialog_ui import DialogMixin
from .cache_ui import CacheMixin
from .download_ui import DownloadMixin
from .shortcuts_ui import ShortcutsMixin
from .bookmark_ui import BookmarkMixin
from .search_ui import SearchMixin


class NovelReaderApp(
    NovelReaderBase,
    ShelfMixin,
    ImportMixin,
    ReaderMixin,
    ThemeMixin,
    TtsMixin,
    DialogMixin,
    CacheMixin,
    DownloadMixin,
    ShortcutsMixin,
    BookmarkMixin,
    SearchMixin,
):
    """多多朗读主应用（由基类 + 各功能 Mixin 组装，行为与原单文件一致）。"""
    pass
