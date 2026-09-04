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
        tsb = make_scrollbar(tab_update, txt.yview)
        txt.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("end", version_info.format_history())
        txt.configure(state="disabled")

        keys = tk.Text(tab_keys, wrap="word", padx=12, pady=10, relief="flat", font=("微软雅黑", 10))
        ksb = make_scrollbar(tab_keys, keys.yview)
        keys.configure(yscrollcommand=ksb.set)
        ksb.pack(side="right", fill="y")
        keys.pack(side="left", fill="both", expand=True)
        for k, desc in version_info.SHORTCUTS:
            keys.insert("end", f"{k}\n    {desc}\n\n")
        keys.configure(state="disabled")

        # —— 缓存管理（可滚动卡片式布局） ——
        tab_cache.configure(bg=_about_bg)
        cache_canvas = tk.Canvas(tab_cache, bg=_about_bg, highlightthickness=0, bd=0)
        cache_scroll = make_scrollbar(tab_cache, cache_canvas.yview)
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
    def _update_cache_size_label(self, lbl):
        try:
            root = self._effective_text_cache_root()
            siz = dir_size(root)
            loc = "默认位置" if not (self.settings.get("cache_dir") or "") else "自定义位置"
            lbl.configure(text=f"缓存总大小：{self._format_bytes(siz)}（{loc}）")
        except Exception:
            pass
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
