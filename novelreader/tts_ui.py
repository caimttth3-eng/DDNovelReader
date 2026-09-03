# -*- coding: utf-8 -*-
"""TTS 控制：开始 / 暂停 / 继续 / 停止 / 语速 / 音量 / 语音切换（从 gui.py 拆分的 Mixin 之一）。"""
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

class TtsMixin:
    """TTS 控制：开始 / 暂停 / 继续 / 停止 / 语速 / 音量 / 语音切换"""
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
            st = self.tts.book_cache_status(self.current_bid)
        except Exception:
            st = None
        if st and self.current_bid:
            try:
                bid = self.current_bid
                last_persists = getattr(self, "_book_cache_last_persists", {})
                # 读索引（文本+音频），零扫盘
                total = self._book_total_cache_size(bid)
                self._shelf_size_cache[bid] = total
                state = st["state"]
                now = time.time()
                last = last_persists.get(bid, 0)
                if state in ("paused", "done", "cancelled"):
                    self.storage.set_book_cache_size(bid, total)
                    self._book_cache_last_persists[bid] = now
                elif state == "caching" and now - last > 5:
                    # 缓存进行中每 5 秒持久化一次，避免频繁写 library.json
                    self.storage.set_book_cache_size(bid, total)
                    self._book_cache_last_persists[bid] = now
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
    def _change_volume(self, delta):
        """快捷键增减音量（0-100）。"""
        v = int(self.settings.get("volume", 100)) + delta
        v = max(0, min(100, v))
        try:
            self.volume_var.set(v)
        except Exception:
            pass
        self._on_volume_change(v)
    def _shortcut_tts_toggle(self, event=None):
        self._tts_toggle()
        return "break"
