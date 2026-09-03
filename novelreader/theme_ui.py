# -*- coding: utf-8 -*-
"""外观设置：主题 / 书页配色 / 字号 / 行距 / 空行模式 / 停顿间隔（从 gui.py 拆分的 Mixin 之一）。"""
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

class ThemeMixin:
    """外观设置：主题 / 书页配色 / 字号 / 行距 / 空行模式 / 停顿间隔"""
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
    def _cycle_paragraph_mode(self):
        """循环切换空行压缩模式：不压缩→合并为一行→清理所有行→不压缩。"""
        try:
            if self.paragraph_cb is None:
                return
            cur = int(self.settings.get("paragraph_mode", 1))
            nxt = (cur % 3) + 1
            self.paragraph_cb.current(nxt - 1)
            self.settings["paragraph_mode"] = nxt
            self.storage.set_setting("paragraph_mode", nxt)
            self._render_chapter()
        except Exception:
            pass
