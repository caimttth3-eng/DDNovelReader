# -*- coding: utf-8 -*-
"""弹窗：关于 / 皮肤选择 / 邮箱复制 / 定时停止 / 百分比跳转（从 gui.py 拆分的 Mixin 之一）。"""
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
from .i18n import T as _T
from .constants import (
    make_scrollbar,
    THEMES,
    UI_THEMES,
    FILE_TYPES,
    _PREFERRED_FONTS,
    CF_HDROP,
    GMEM_MOVEABLE,
    GMEM_ZEROINIT,
    _copy_files_to_clipboard,
)

class DialogMixin:
    """弹窗：关于 / 皮肤选择 / 邮箱复制 / 定时停止 / 百分比跳转"""
    def _open_settings_menu(self, event=None):
        """主界面「设置」按钮：在按钮/鼠标下方弹出纵向列表面板（方案 C）。

        四入口：缓存管理 / 快捷键说明 / 更新记录 / 关于，每行带图标与小注，
        hover 高亮整行，点击主界面任意处自动收起。
        """
        self._close_settings_panel()
        _bg = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        _card_bg = "#FFFFFF"
        _border = "#DDE3EC"
        _hover = "#E8EEFB"
        _main_fg = "#1A1B1C"
        _sub_fg = "#9AA0A6"

        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.configure(bg=_bg)
        try:
            panel.attributes("-topmost", True)
        except Exception:
            pass

        outer = tk.Frame(panel, bg=_bg, padx=4, pady=4)
        outer.pack()
        box = tk.Frame(outer, bg=_card_bg, highlightbackground=_border, highlightthickness=1)
        box.pack()

        # 加载 4 枚线性图标（透明 PNG，随主题背景）
        if not getattr(self, "_settings_icons", None):
            self._settings_icons = {}

        def _icon_path(name):
            _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            return os.path.join(_base, "assets", "icons", name + ".png")

        for _n in ("cache", "keyboard", "changelog", "about"):
            if _n not in self._settings_icons:
                try:
                    self._settings_icons[_n] = tk.PhotoImage(file=_icon_path(_n))
                except Exception:
                    self._settings_icons[_n] = None

        entries = [
            ("cache", _T("缓存管理"), _T("正文/音频"), self._show_cache_manager_dialog),
            ("keyboard", _T("快捷键说明"), _T("Ctrl/±"), self._show_shortcuts_dialog),
            ("changelog", _T("更新记录"), _T("版本历史"), self._show_changelog_dialog),
            ("about", _T("关于"), _T("版本/作者"), self._show_about),
        ]

        def _make_row(icon, title, note, cmd):
            row = tk.Frame(box, bg=_card_bg, cursor="hand2")
            row.pack(fill="x")
            _img = self._settings_icons.get(icon)
            if _img is not None:
                _icon_lbl = tk.Label(row, image=_img, bg=_card_bg)
                _icon_lbl.pack(side="left", padx=(12, 8), pady=7)
            else:
                _icon_lbl = tk.Label(row, text="•", font=("微软雅黑", 13), bg=_card_bg)
                _icon_lbl.pack(side="left", padx=(12, 8), pady=8)
            tk.Label(row, text=title, font=("微软雅黑", 10, "bold"),
                     bg=_card_bg, fg=_main_fg).pack(side="left", pady=8)
            tk.Label(row, text=note, font=("微软雅黑", 8),
                     bg=_card_bg, fg=_sub_fg).pack(side="right", padx=12, pady=8)
            children = list(row.winfo_children())

            def _enter(_e):
                row.configure(bg=_hover)
                for w in children:
                    w.configure(bg=_hover)

            def _leave(_e):
                row.configure(bg=_card_bg)
                for w in children:
                    w.configure(bg=_card_bg)

            def _click(_e=None):
                self._close_settings_panel()
                cmd()

            for w in [row] + children:
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)
                w.bind("<Button-1>", _click)

        for it in entries:
            _make_row(*it)

        if event is not None:
            x, y = event.x_root, event.y_root
        else:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        panel.update_idletasks()
        _w, _h = panel.winfo_reqwidth(), panel.winfo_reqheight()
        _sw, _sh = panel.winfo_screenwidth(), panel.winfo_screenheight()
        if x + _w > _sw - 8:
            x = _sw - _w - 8
        if y + _h > _sh - 8:
            y = _sh - _h - 8
        panel.geometry(f"+{x}+{y}")

        self._settings_panel = panel
        self._settings_panel_close_id = self.root.bind(
            "<Button-1>", lambda _e: self._close_settings_panel(), add="+")

    def _close_settings_panel(self):
        """收起设置列表面板，并解除主窗口的点击监听。"""
        if getattr(self, "_settings_panel", None) is not None:
            try:
                self._settings_panel.destroy()
            except Exception:
                pass
            self._settings_panel = None
        cid = getattr(self, "_settings_panel_close_id", None)
        if cid:
            try:
                self.root.unbind("<Button-1>", cid)
            except Exception:
                pass
            self._settings_panel_close_id = None

    def _show_cache_manager_dialog(self):
        """独立弹窗：缓存管理（正文解析缓存 / 音频缓存）。

        从根上不引入滚动容器：两张卡片直接平铺，窗口宽度/高度按真实内容
        自适应（路径较长自动换行会撑高窗口），内容刚好放下就绝不出现
        滚动条；仅在屏幕不足以容纳时按屏幕高度收敛。
        """
        top = tk.Toplevel(self.root)
        top.title(_T("缓存管理"))
        top.transient(self.root)
        _bg = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        top.configure(bg=_bg)

        body = tk.Frame(top, bg=_bg)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        def _make_cache_card(parent, title, subtitle, icon, accent, kind):
            """创建一个缓存管理卡片。kind: 'text' or 'audio'."""
            _card_bg = UI_THEMES.get(self.settings.get("ui_theme", "A·米黄暖读"), UI_THEMES["A·米黄暖读"])["field"]
            card = tk.Frame(parent, bg=_card_bg, highlightbackground="#e2e5ea", highlightthickness=1)
            card.pack(fill="x", padx=6, pady=(0, 12))

            head = tk.Frame(card, bg=_card_bg)
            head.pack(fill="x", padx=14, pady=(12, 4))
            tk.Label(head, text=icon, font=("微软雅黑", 14), bg=_card_bg).pack(side="left")
            tk.Label(head, text=title, font=("微软雅黑", 11, "bold"), fg=accent, bg=_card_bg).pack(side="left", padx=(6, 0))
            loc_lbl = tk.Label(head, text="", font=("微软雅黑", 9), fg="#999999", bg=_card_bg)
            loc_lbl.pack(side="right")

            if subtitle:
                tk.Label(card, text=subtitle, font=("微软雅黑", 9), fg="#888888", bg=_card_bg,
                         wraplength=580, justify="left").pack(anchor="w", padx=14, pady=(0, 4))

            path_text = self._effective_text_cache_root() if kind == "text" else self._effective_tts_cache_root()
            path_lbl = tk.Label(card, text=path_text, font=("微软雅黑", 9), fg="#2b6cb0",
                                bg=_card_bg, wraplength=580, justify="left", cursor="hand2")
            path_lbl.pack(anchor="w", padx=14, pady=(2, 2))
            open_cmd = self._open_cache_folder if kind == "text" else self._open_tts_cache_folder
            path_lbl.bind("<Button-1>", lambda e: open_cmd())

            size_lbl = tk.Label(card, text=_T("正在统计…"), font=("微软雅黑", 10), fg="#444444", bg=_card_bg)
            size_lbl.pack(anchor="w", padx=14, pady=(2, 6))

            btn_row = tk.Frame(card, bg=_card_bg)
            btn_row.pack(anchor="w", padx=14, pady=(0, 12))
            if kind == "text":
                ttk.Button(btn_row, text=_T("自定义位置"),
                           command=lambda: self._choose_text_cache_folder(path_lbl, size_lbl)).pack(side="left")
                ttk.Button(btn_row, text=_T("一键转移"),
                           command=lambda: self._transfer_text_cache(path_lbl, size_lbl)).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text=_T("打开文件夹"),
                           command=self._open_cache_folder).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text=_T("清除"),
                           command=lambda: self._clear_cache(size_lbl)).pack(side="left", padx=(8, 0))
            else:
                ttk.Button(btn_row, text=_T("自定义位置"),
                           command=lambda: self._choose_tts_cache_folder(path_lbl, tts_size_lbl)).pack(side="left")
                ttk.Button(btn_row, text=_T("一键转移"),
                           command=lambda: self._transfer_tts_cache(path_lbl, tts_size_lbl)).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text=_T("打开文件夹"),
                           command=self._open_tts_cache_folder).pack(side="left", padx=(8, 0))
                ttk.Button(btn_row, text=_T("清除"),
                           command=lambda: self._clear_audio_cache(tts_size_lbl)).pack(side="left", padx=(8, 0))
            return path_lbl, size_lbl, loc_lbl

        # 正文解析缓存卡片
        path_lbl, size_lbl, loc_lbl1 = _make_cache_card(
            body,
            title=_T("正文解析缓存"),
            subtitle=_T("书籍分章解析结果，删除后下次打开需重新解析（不影响原文件）。"),
            icon="📄",
            accent="#2b6cb0",
            kind="text",
        )
        # 音频缓存卡片
        tts_path_lbl, tts_size_lbl, loc_lbl2 = _make_cache_card(
            body,
            title=_T("音频缓存（整本语音）"),
            subtitle=_T("整本语音合成缓存，体积较大，建议放到非 C 盘。"),
            icon="🔊",
            accent="#b00020",
            kind="audio",
        )

        def _fit_window():
            """按 body 真实请求尺寸贴合窗口并居中（内容变高时窗口跟着长）。"""
            top.update_idletasks()
            _w = min(max(body.winfo_reqwidth() + 20, 620), int(top.winfo_screenwidth() * 0.94))
            _h = min(body.winfo_reqheight() + 26, int(top.winfo_screenheight() * 0.94))
            top.geometry(f"{_w}x{_h}")
            self._center_window(top)

        def _refresh_sizes():
            self._update_cache_size_label(size_lbl)
            self._update_tts_cache_size_label(tts_size_lbl)
            loc_lbl1.configure(text=_T("自定义位置") if self.settings.get("cache_dir") else _T("默认位置"))
            loc_lbl2.configure(text=_T("自定义位置") if self.settings.get("tts_cache_dir") else _T("默认位置"))
            _fit_window()

        _fit_window()
        top.after(120, _refresh_sizes)
        top.focus_set()

    def _show_shortcuts_dialog(self):
        """独立弹窗：快捷键说明（按当前界面语言单列显示）。"""
        from . import version_info
        from .i18n import get_lang as _gl

        top = tk.Toplevel(self.root)
        top.title(_T("快捷键说明"))
        top.geometry("560x620")
        top.minsize(440, 420)
        top.transient(self.root)
        self._center_window(top)
        _bg = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        top.configure(bg=_bg)

        keys = tk.Text(top, wrap="word", padx=12, pady=10, relief="flat", font=("微软雅黑", 10))
        ksb = make_scrollbar(top, keys.yview)
        keys.configure(yscrollcommand=ksb.set)
        ksb.pack(side="right", fill="y")
        keys.pack(side="left", fill="both", expand=True)
        _col = {"zh": 1, "en": 2, "ja": 3, "ko": 4}.get(_gl(), 2)
        for _item in version_info.SHORTCUTS:
            keys.insert("end", f"{_item[0]}\n    {_item[_col]}\n\n")
        keys.configure(state="disabled")
        top.focus_set()

    def _show_changelog_dialog(self):
        """独立弹窗：更新记录（正文保持原文，不翻译）。"""
        from . import version_info

        top = tk.Toplevel(self.root)
        top.title(_T("更新记录"))
        top.geometry("640x620")
        top.minsize(500, 420)
        top.transient(self.root)
        self._center_window(top)
        _bg = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        top.configure(bg=_bg)

        txt = tk.Text(top, wrap="word", padx=12, pady=10, relief="flat", font=("微软雅黑", 10))
        tsb = make_scrollbar(top, txt.yview)
        txt.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("end", version_info.format_history())
        txt.configure(state="disabled")
        top.focus_set()

    def _show_about(self):
        """独立弹窗：关于（版本信息 / 图标彩蛋 / 作者联系方式 / 界面语言）。"""
        from . import version_info

        top = tk.Toplevel(self.root)
        top.title(f"{_T('关于')} {version_info.APP_NAME}")
        top.geometry("660x560")
        top.minsize(560, 480)
        top.transient(self.root)
        self._center_window(top)
        _bg = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        top.configure(bg=_bg)

        head = tk.Frame(top, bg=_bg)
        head.pack(fill="x", padx=18, pady=(16, 6))
        # 左侧：当前图标（彩蛋：点击进入主题选择）
        icon_col = tk.Frame(head, bg=_bg)
        icon_col.pack(side="left", padx=(0, 14))
        try:
            png = self._current_skin_png()
            if png:
                self._about_icon_img = tk.PhotoImage(file=png)
                self._about_icon_img = self._about_icon_img.subsample(4, 4)
                icon_lbl = tk.Label(icon_col, image=self._about_icon_img, bg=_bg, cursor="hand2")
                icon_lbl.pack()
                icon_lbl.bind("<Button-1>", lambda e: self._open_skin_picker())
                tk.Label(icon_col, text=_T("点击换主题"), fg="#999999", bg=_bg,
                         font=("微软雅黑", 8)).pack(pady=(2, 0))
        except Exception:
            pass
        # 右侧：文字信息
        info = tk.Frame(head, bg=_bg)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=f"{_T(version_info.APP_NAME)}  v{__version__}",
                 font=("微软雅黑", 17, "bold"), bg=_bg).pack(anchor="w")
        tk.Label(info, text=_T("支持 Windows / macOS / Linux / Android 的有声小说阅读器（多多朗读）  ·  当前版本 v{version}").format(version=__version__),
                 fg="#777777", bg=_bg, font=("微软雅黑", 10),
                 wraplength=520, justify="left").pack(anchor="w", pady=(2, 0))
        email_row = tk.Frame(info, bg=_bg)
        email_row.pack(anchor="w", pady=(6, 0))
        tk.Label(email_row, text=_T("作者联系方式："), fg="#555555", bg=_bg,
                 font=("微软雅黑", 10)).pack(side="left")
        self._email_label = tk.Label(
            email_row, text="230468896@qq.com", fg="#2b6cb0", bg=_bg,
            font=("微软雅黑", 10, "underline"), cursor="hand2")
        self._email_label.pack(side="left")
        self._email_label.bind("<Button-1>", lambda e: self._copy_email())
        tk.Label(email_row, text=_T("（点击复制）"), fg="#999999", bg=_bg,
                 font=("微软雅黑", 9)).pack(side="left", padx=(6, 0))

        bili_row = tk.Frame(info, bg=_bg)
        bili_row.pack(anchor="w", pady=(4, 0))
        tk.Label(bili_row, text=_T("B站空间："), fg="#555555", bg=_bg,
                 font=("微软雅黑", 10)).pack(side="left")
        self._bili_label = tk.Label(
            bili_row, text="https://space.bilibili.com/42444", fg="#2b6cb0", bg=_bg,
            font=("微软雅黑", 10, "underline"), cursor="hand2")
        self._bili_label.pack(side="left")
        self._bili_label.bind("<Button-1>", lambda e: self._open_bili())
        tk.Label(bili_row, text=_T("（点击打开）"), fg="#999999", bg=_bg,
                 font=("微软雅黑", 9)).pack(side="left", padx=(6, 0))
        tk.Label(info, text=_T("反馈问题可以在B站动态留言，B站我天天看。"), fg="#8a5a00", bg=_bg,
                 font=("微软雅黑", 9), wraplength=520, justify="left").pack(anchor="w", pady=(4, 0))

        dy_row = tk.Frame(info, bg=_bg)
        dy_row.pack(anchor="w", pady=(2, 0))
        tk.Label(dy_row, text=_T("抖音号："), fg="#555555", bg=_bg,
                 font=("微软雅黑", 10)).pack(side="left")
        self._dy_label = tk.Label(
            dy_row, text="120735162", fg="#2b6cb0", bg=_bg,
            font=("微软雅黑", 10, "underline"), cursor="hand2")
        self._dy_label.pack(side="left")
        self._dy_label.bind("<Button-1>", lambda e: self._copy_douyin())
        tk.Label(dy_row, text=_T("（点击复制）"), fg="#999999", bg=_bg,
                 font=("微软雅黑", 9)).pack(side="left", padx=(6, 0))

        gh_row = tk.Frame(info, bg=_bg)
        gh_row.pack(anchor="w", pady=(2, 0))
        tk.Label(gh_row, text=_T("软件发布："), fg="#555555", bg=_bg,
                 font=("微软雅黑", 10)).pack(side="left")
        self._gh_label = tk.Label(
            gh_row, text="github.com/caimttth3-eng/DDNovelReader", fg="#2b6cb0", bg=_bg,
            font=("微软雅黑", 10, "underline"), cursor="hand2")
        self._gh_label.pack(side="left")
        self._gh_label.bind("<Button-1>", lambda e: self._open_github())
        tk.Label(gh_row, text=_T("（点击打开）"), fg="#999999", bg=_bg,
                 font=("微软雅黑", 9)).pack(side="left", padx=(6, 0))

        lang_row = tk.Frame(info, bg=_bg)
        lang_row.pack(anchor="w", pady=(8, 0))
        tk.Label(lang_row, text=_T("界面语言"), fg="#555555", bg=_bg,
                 font=("微软雅黑", 10)).pack(side="left")
        lang_cb = ttk.Combobox(lang_row, state="readonly", width=10,
                               values=["中文", "English", "日本語", "한국어"])
        lang_cb.pack(side="left")
        from .i18n import get_lang as _get_lang
        _lang_map = {"zh": "中文", "en": "English", "ja": "日本語", "ko": "한국어"}
        lang_cb.set(_lang_map.get(_get_lang(), "中文"))
        def _on_lang(e=None):
            v = lang_cb.get()
            _rev = {n: c for c, n in _lang_map.items()}
            self.storage.set_setting("ui_lang", _rev.get(v, "zh"))
            messagebox.showinfo(_T("提示"), _T("界面语言已切换，重启软件后生效"), parent=top)
        lang_cb.bind("<<ComboboxSelected>>", _on_lang)

        tk.Label(top, text=_T("更多功能请从主界面「设置」打开（缓存管理 / 快捷键说明 / 更新记录）。"),
                 fg="#8a5a00", bg=_bg, font=("微软雅黑", 9),
                 wraplength=560, justify="left").pack(anchor="w", padx=20, pady=(8, 12))

        # 按内容自适应大小并居中（长文本已换行，窗口刚好放下内容）
        top.update_idletasks()
        _w = min(max(top.winfo_reqwidth(), 620), int(top.winfo_screenwidth() * 0.94))
        _h = min(top.winfo_reqheight() + 24, int(top.winfo_screenheight() * 0.94))
        top.geometry(f"{_w}x{_h}")
        self._center_window(top)
        self._about_win = top
        top.focus_set()

    def _open_skin_picker(self):
        """主题选择：六套控件风格（A-F），点击即切换，即时生效。"""
        top = tk.Toplevel(self.root)
        top.title(_T("主题选择"))
        top.geometry("560x440")
        top.minsize(480, 380)
        top.transient(self.root)
        self._center_window(top)
        cur = UI_THEMES.get(self.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])
        top.configure(bg=cur["bg"])

        tk.Label(top, text=_T("选择控件主题风格（点击即切换，即时生效）"),
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

            tk.Label(card, text=_T(name), bg=c["field"], fg=c["fg"],
                     font=("微软雅黑", 10, "bold"), cursor="hand2").pack(pady=(6, 2))
            tk.Label(card, text=(_T("✓ 当前") if is_cur else f"{_T('按钮')} {c['btn']}"),
                     bg=c["field"], fg=(c["accent"] if is_cur else c["muted"]),
                     font=("微软雅黑", 8), cursor="hand2").pack(pady=(0, 6))

            # 绑定点击切换（卡片及所有子控件）
            _sw = _make_switch(name, top)
            for w in (card, prev, btn_prev):
                w.bind("<Button-1>", _sw)
            for w in card.winfo_children():
                w.bind("<Button-1>", _sw)
    def _open_bili(self):
        """打开作者 B 站空间。"""
        import webbrowser
        webbrowser.open("https://space.bilibili.com/42444")


    def _open_github(self):
        try:
            import webbrowser
            webbrowser.open("https://github.com/caimttth3-eng/DDNovelReader")
        except Exception:
            pass

    def _copy_douyin(self):
        """复制作者抖音号到剪贴板。"""
        dy = "120735162"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(dy)
            lbl = self._dy_label
            try:
                lbl.configure(text=_T("已复制 ✓"))

                def _restore():
                    try:
                        if lbl.winfo_exists():
                            lbl.configure(text=dy)
                    except Exception:
                        pass

                self.root.after(1500, _restore)
            except Exception:
                pass
        except Exception:
            pass

    def _copy_email(self):
        """复制作者邮箱到剪贴板。"""
        email = "230468896@qq.com"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(email)
            lbl = self._email_label
            try:
                lbl.configure(text=_T("已复制 ✓"))

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
    def _update_cache_size_label(self, lbl):
        try:
            root = self._effective_text_cache_root()
            siz = dir_size(root)
            loc = _T("默认位置") if not (self.settings.get("cache_dir") or "") else _T("自定义位置")
            lbl.configure(text=_T("缓存总大小：{size}（{loc}）").format(size=self._format_bytes(siz), loc=loc))
        except Exception:
            pass
    def _open_timer_dialog(self):
        """定时停止播放：以分钟为单位自填数字，到点自动停止。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(_T("定时停止朗读"))
        dlg.geometry("340x180")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        tk.Label(dlg, text=_T("设置定时时长（分钟）："), font=("微软雅黑", 10)).pack(pady=(16, 6))
        var = tk.IntVar(value=30)
        sp = tk.Spinbox(dlg, from_=1, to=600, textvariable=var, width=8, font=("微软雅黑", 11))
        sp.pack()
        if self._timer_running:
            tk.Label(dlg, text=_T("（当前已有定时，重新设置将覆盖）"), fg="#cc6600",
                     font=("微软雅黑", 8)).pack(pady=(6, 0))

        def do_start():
            mins = max(1, var.get())
            self._timer_minutes = mins
            self._timer_deadline = time.time() + mins * 60
            self._timer_running = True
            self._flash_status(_T("已设定 {mins} 分钟定时，到点自动停止朗读").format(mins=mins))
            dlg.destroy()

        def do_cancel():
            self._timer_running = False
            self._timer_deadline = None
            self.timer_btn.configure(text=_T("定时"))
            self._flash_status(_T("已取消定时"))
            dlg.destroy()

        ops = tk.Frame(dlg)
        ops.pack(pady=12)
        tk.Button(ops, text=_T("开始"), width=8, command=do_start).pack(side="left", padx=8)
        tk.Button(ops, text=_T("取消定时"), width=12, command=do_cancel).pack(side="left", padx=8)
    def _tick_timer(self):
        """每秒轮询：到点停止朗读，并刷新定时按钮倒计时。"""
        try:
            if self._timer_running and self._timer_deadline:
                remain = self._timer_deadline - time.time()
                if remain <= 0:
                    mins = self._timer_minutes
                    self._timer_running = False
                    self._timer_deadline = None
                    self.timer_btn.configure(text=_T("定时"))
                    self._tts_stop()
                    self._flash_status(_T("定时时间到，已停止朗读（本次定时 {mins} 分钟）").format(mins=mins))
                else:
                    m, s = divmod(int(remain), 60)
                    self.timer_btn.configure(text=_T("定时 {m}:{s}").format(m=f"{m:02d}", s=f"{s:02d}"))
            else:
                self.timer_btn.configure(text=_T("定时"))
        except Exception:
            pass
        try:
            self.root.after(1000, self._tick_timer)
        except Exception:
            pass
    def _open_percent_dialog(self):
        """点击右下角百分比：弹窗手动输入百分比并跳转。"""
        if not self.book:
            messagebox.showinfo(_T("提示"), _T("请先从书架打开一本书"))
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(_T("跳转到进度"))
        dlg.geometry("300x150")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        cur = self._compute_percent(self.chapter_idx, self.char_offset)
        tk.Label(dlg, text=_T("当前进度 {cur}%，输入目标百分比（0~100）：").format(cur=f"{cur:.1f}"),
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

        tk.Button(ops, text=_T("跳转"), width=8, command=ok).pack(side="left", padx=8)
        tk.Button(ops, text=_T("取消"), width=8, command=dlg.destroy).pack(side="left", padx=8)
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
        self._flash_status(_T("已跳转到全书 {pct}%").format(pct=f"{pct:.1f}"))
