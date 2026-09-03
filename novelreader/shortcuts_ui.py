# -*- coding: utf-8 -*-
"""快捷键绑定 / 全屏模式 / 悬浮信息（从 gui.py 拆分的 Mixin 之一）。"""
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

class ShortcutsMixin:
    """快捷键绑定 / 全屏模式 / 悬浮信息"""
    def _bind_shortcuts(self):
        r = self.root
        # ---- 文件 / 书架 ----
        r.bind("<Control-o>", lambda e: self._add_book())
        r.bind("<Control-O>", lambda e: self._add_book())
        r.bind("<Control-b>", lambda e: self._toggle_shelf())
        r.bind("<Control-B>", lambda e: self._toggle_shelf())
        # ---- 字体 / 排版 ----
        r.bind("<Control-plus>", lambda e: self._change_font_size(1))
        r.bind("<Control-equal>", lambda e: self._change_font_size(1))
        r.bind("<Control-minus>", lambda e: self._change_font_size(-1))
        r.bind("<Control-0>", lambda e: self._reset_font())
        # 小键盘 + / - / 0 控制字号（本 Tk 环境小键盘 keysym 为 plus/minus/0，非 KP_Add）
        r.bind("<KP_Add>", lambda e: self._keypad_font(e, 1))
        r.bind("<KP_Subtract>", lambda e: self._keypad_font(e, -1))
        r.bind("<KP_0>", lambda e: self._keypad_font(e, 0))
        r.bind("<Key-equal>", lambda e: self._keypad_font(e, 1))
        r.bind("<Key-plus>", lambda e: self._keypad_font(e, 1))
        r.bind("<Key-minus>", lambda e: self._keypad_font(e, -1))
        r.bind("<Key-0>", lambda e: self._keypad_font(e, 0))
        r.bind("<plus>", lambda e: self._keypad_font(e, 1))
        r.bind("<minus>", lambda e: self._keypad_font(e, -1))
        r.bind("<0>", lambda e: self._keypad_font(e, 0))
        # 通用兜底：捕获 keysym 为 plus/minus/0 的裸键（小键盘 / 主键盘）
        r.bind("<KeyPress>", self._on_global_keypad)
        r.bind("<Control-Alt-plus>", lambda e: self._change_line_spacing(0.2))
        r.bind("<Control-Alt-equal>", lambda e: self._change_line_spacing(0.2))
        r.bind("<Control-Alt-minus>", lambda e: self._change_line_spacing(-0.2))
        r.bind("<Control-m>", lambda e: self._cycle_paragraph_mode())
        r.bind("<Control-M>", lambda e: self._cycle_paragraph_mode())
        # ---- 书页配色（白天 / 护眼 / 夜间 / 米黄）----
        _themes = list(THEMES.keys())
        for i in range(min(4, len(_themes))):
            r.bind(f"<Control-{i + 1}>", lambda e, t=_themes[i]: self._set_theme(t))
        # ---- 导航 / 视图 ----
        r.bind("<Control-l>", lambda e: self._toggle_toc())
        r.bind("<Control-L>", lambda e: self._toggle_toc())
        r.bind("<Control-Prior>", lambda e: self._goto_chapter(self.chapter_idx - 1))
        r.bind("<Control-Next>", lambda e: self._goto_chapter(self.chapter_idx + 1))
        r.bind("<F11>", lambda e: self._toggle_fullscreen())
        r.bind("<Alt-Return>", lambda e: self._toggle_fullscreen())
        r.bind("<Escape>", lambda e: self._exit_fullscreen())
        # ---- 朗读控制 ----
        # Space 在阅读区触发朗读切换，并阻止输入空格
        self.text.bind("<space>", self._shortcut_tts_toggle)
        r.bind("<Control-p>", lambda e: self._tts_toggle())
        r.bind("<Control-P>", lambda e: self._tts_toggle())
        r.bind("<Control-s>", lambda e: self._tts_stop())
        r.bind("<Control-S>", lambda e: self._tts_stop())
        r.bind("<Control-r>", lambda e: self._shortcut_read_from_paragraph())
        r.bind("<Control-R>", lambda e: self._shortcut_read_from_paragraph())
        # ---- 朗读参数（音量：Ctrl+↑↓；语速：Ctrl+←→）----
        r.bind("<Control-Up>", lambda e: self._change_volume(5))
        r.bind("<Control-Down>", lambda e: self._change_volume(-5))
        r.bind("<Control-Left>", lambda e: self._change_rate(-10))
        r.bind("<Control-Right>", lambda e: self._change_rate(10))
        # ---- 功能（下载管理器 / 定时）----
        r.bind("<Control-d>", lambda e: self._open_cache_dialog())
        r.bind("<Control-D>", lambda e: self._open_cache_dialog())
        r.bind("<Control-t>", lambda e: self._open_timer_dialog())
        r.bind("<Control-T>", lambda e: self._open_timer_dialog())
        # 定时停止朗读的秒级轮询（常驻）
        self.root.after(1000, self._tick_timer)

    def _on_global_keypad(self, e):
        """通用兜底：裸键 plus/minus/0（小键盘或主键盘）控制字号。"""
        if e.state & 0x0004 or e.state & 0x0001:  # Ctrl / Shift 修饰键下放行
            return None
        ks = e.keysym
        if ks in ("KP_Add", "plus", "equal"):
            return self._keypad_font(e, 1)
        if ks in ("KP_Subtract", "minus"):
            return self._keypad_font(e, -1)
        if ks in ("KP_0", "0"):
            return self._keypad_font(e, 0)
        return None

    def _keypad_font(self, e, delta):
        """小键盘/裸键字号控制：焦点在输入控件时放行，否则执行并阻断字符输入。"""
        w = self.root.focus_get()
        if w is not None and isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox)):
            return None
        if delta == 0:
            self._reset_font()
        else:
            self._change_font_size(delta)
        return "break"
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
