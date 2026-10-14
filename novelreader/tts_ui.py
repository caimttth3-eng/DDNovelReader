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
from .i18n import T as _T
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
    def _start_tts(self, book, ci, off):
        """统一朗读入口（多人有声模块已下线，保留此入口供 tts_ui / reader_ui 调用）。"""
        # GPT-SoVITS：目录未填→同步引导；填过但服务未启动→弹动画窗后台启动（不卡界面），
        # 启动完成后自动继续朗读。
        try:
            if getattr(self.tts, "is_gptsovits_active", lambda: False)():
                self._ensure_gptsovits_async(
                    lambda ok, why: self._on_gptsovits_ready(ok, why, book, ci, off))
                return
        except Exception:
            pass  # 探测/启动逻辑异常不影响正常朗读
        self.tts.start(book, ci, off)

    def _ensure_gptsovits_async(self, on_done):
        """GPT-SoVITS 引擎统一引导：目录未填→同步弹窗引导（on_done(False,'no_root')）；
        填过但端口没开→弹非模态动画窗，后台线程启动，完成回调 on_done(ok, why)。
        已在启动中→直接忽略（动画窗还开着，防重复）。"""
        from . import tts_engine
        root = (self.settings or {}).get("gptsovits_root", "")
        if not root or not os.path.isdir(root):
            if messagebox.askyesno(
                    _T("提示"),
                    _T("使用 GPT-SoVITS 前需要先选择它的根目录（设置→语音引擎→GPT-SoVITS）。现在去设置？")):
                self._show_engines_dialog()
            on_done(False, "no_root")
            return
        if tts_engine._gptsovits_port_open():
            on_done(True, "port_open")
            return
        if getattr(self, "_gptsovits_starting", False):
            return
        self._gptsovits_starting = True
        self._show_gptsovits_starting(on_done)

    def _show_gptsovits_starting(self, on_done):
        """非模态提示窗：进度条动画 + 文案，后台线程启动服务。完成回调在 UI 线程执行。"""
        from . import tts_engine
        top = tk.Toplevel(self.root)
        top.title(_T("提示"))
        top.resizable(False, False)
        try:
            top.transient(self.root)
        except Exception:
            pass
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        frm = tk.Frame(top, padx=28, pady=20)
        frm.pack()
        tk.Label(frm, text=_T("正在启动…") + " GPT-SoVITS",
                 font=("微软雅黑", 11, "bold")).pack()
        bar = ttk.Progressbar(frm, mode="indeterminate", length=280)
        bar.pack(pady=(14, 8))
        bar.start(12)
        tk.Label(frm, text=_T("启动中，若长时间无响应请检查所选目录是否正确"),
                 fg="#888888", font=("微软雅黑", 9)).pack()
        try:
            top.update_idletasks()
            w, h = top.winfo_width(), top.winfo_height()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
            top.geometry("+%d+%d" % (max(x, 10), max(y, 10)))
        except Exception:
            pass

        # 后台线程绝不直接碰 Tk（跨线程 after 会阻塞/死锁）。
        # 只写状态字典，由动画窗主线程的 after 轮询收尾。
        state = {"done": False, "ok": False, "why": ""}

        def _finish(ok, why):
            state["done"] = True
            state["ok"] = bool(ok)
            state["why"] = why or ""

        def _poll():
            if not top.winfo_exists():
                return
            if state["done"]:
                self._gptsovits_starting = False
                try:
                    top.destroy()
                except Exception:
                    pass
                try:
                    on_done(state["ok"], state["why"])
                except Exception:
                    pass
                return
            top.after(100, _poll)

        top.after(100, _poll)
        tts_engine.ensure_gptsovits_service_async(self.settings, _finish)

    def _on_gptsovits_ready(self, ok, why, book, ci, off):
        """GPT-SoVITS 服务就绪后继续朗读；失败给出对应提示。"""
        if not ok:
            if why == "no_root":
                return  # 引导流程已提示过，避免重复弹窗
            msg = {
                "no_python": _T("选择的 GPT-SoVITS 文件夹里没有可用的 Python；\n源码部署的用户请先手动启动服务（python3 api.py -p 9880），再点保存。"),
                "launch_error": _T("GPT-SoVITS 服务启动失败，请到语音引擎里点「一键启动」看具体提示"),
                "timeout": _T("GPT-SoVITS 服务启动超时（60 秒），请到语音引擎里检查目录是否正确"),
            }.get(why, _T("GPT-SoVITS 服务不可用"))
            messagebox.showinfo(_T("提示"), msg)
            return
        self.tts.start(book, ci, off)
    def _tts_toggle(self):
        """开始 / 暂停 / 继续 切换。"""
        if not self.book:
            messagebox.showinfo(_T("提示"), _T("请先从书架打开一本书"))
            return
        if self.tts.is_playing():
            self.tts.pause()
            self._set_tts_ui("paused")
        elif self.tts.is_paused():
            self.tts.resume()
            self._set_tts_ui("playing")
        else:
            self._start_tts(self.book, self.chapter_idx, self.char_offset)
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
            self.tts_toggle_btn.configure(text=_T("⏸ 暂停"), state="normal")
            self.tts_stop_btn.configure(state="normal")
        elif state == "paused":
            self.tts_toggle_btn.configure(text=_T("▶ 继续"), state="normal")
            self.tts_stop_btn.configure(state="normal")
        else:
            self.tts_toggle_btn.configure(text=_T("▶ 开始朗读"), state="normal")
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
            self.tts_cache_btn.configure(text=_T("整本缓存"))
            self.book_cache_label.configure(text="")
            return
        state, done, total = st["state"], st["done"], st["total"]
        if state == "caching":
            pct = (done / total * 100) if total else 0
            self.tts_cache_btn.configure(text=_T("缓存中 {pct}%").format(pct=f"{pct:.0f}"))
            self.book_cache_label.configure(
                text=_T("整本缓存 {done}/{total}（点击暂停）").format(done=done, total=total),
                fg="#8a5a00",
                font=("微软雅黑", 9),
            )
        elif state == "paused":
            pct = (done / total * 100) if total else 0
            self.tts_cache_btn.configure(text=_T("已暂停 {pct}%").format(pct=f"{pct:.0f}"))
            # 暂停：醒目加粗 + 高对比色提示
            self.book_cache_label.configure(
                text=_T("⏸ 缓存已暂停 {done}/{total}（点击继续）").format(done=done, total=total),
                fg="#c0392b",
                font=("微软雅黑", 9, "bold"),
            )
        elif state == "done":
            self.tts_cache_btn.configure(text=_T("整本缓存"))
            self.book_cache_label.configure(
                text=_T("整本缓存完成 {total} 句").format(total=total),
                fg="#8a5a00",
                font=("微软雅黑", 9),
            )
            if not getattr(self, "_book_cache_done_flashed", False):
                self._book_cache_done_flashed = True
                self._flash_status(_T("整本语音缓存完成，朗读将零网络延迟"))
        elif state == "cancelled":
            self.tts_cache_btn.configure(text=_T("整本缓存"))
            self.book_cache_label.configure(text="", fg="#8a5a00", font=("微软雅黑", 9))
        else:
            self.tts_cache_btn.configure(text=_T("整本缓存"))
            self.book_cache_label.configure(text="", fg="#8a5a00", font=("微软雅黑", 9))
    def _change_rate(self, delta):
        old_rate = int(self.settings.get("tts_rate", 200))
        new_rate = max(50, min(400, old_rate + delta))
        if new_rate == old_rate:
            return
        if self._has_any_book_cache():
            from tkinter import messagebox
            if not messagebox.askyesno("提示", "更改语速后，已有的整本缓存将不命中，需要重新缓存。是否继续？"):
                return
        self.settings["tts_rate"] = new_rate
        self.rate_label.configure(text=str(new_rate))
        self.tts.set_rate(new_rate)
        self.storage.set_setting("tts_rate", new_rate)
    def _on_voice_change(self, event):
        idx = self.voice_cb.current()
        if idx < 0 or not getattr(self, "_voice_ids", None):
            return
        voice_id = self._voice_ids[idx]
        old_voice = self.settings.get("tts_voice", "")
        if voice_id == old_voice:
            return
        if self._has_any_book_cache():
            from tkinter import messagebox
            if not messagebox.askyesno("提示", "更改语音后，已有的整本缓存将不命中，需要重新缓存。是否继续？"):
                old_idx = self._voice_ids.index(old_voice) if old_voice in self._voice_ids else 0
                self.voice_cb.current(old_idx)
                return
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

    def _jump_sentence(self, delta):
        """快捷键 ← / →：跳到上一句 / 下一句（朗读中则从新句子继续读）。"""
        if not self.book:
            return "break"
        content = self.book.chapters[self.chapter_idx].content
        n = len(content)
        off = self.char_offset
        if delta > 0:
            # 下一句：从当前位置向后找句末标点
            i = off
            while i < n:
                if content[i] in "。！？!?…；;":
                    off = i + 1
                    break
                i += 1
            else:
                return "break"  # 已在章节末尾
        else:
            # 上一句：直接跳到上一句的起点
            cur = self._sentence_start(off)
            if cur > 0:
                off = self._sentence_start(cur - 1)
            elif off > 0:
                off = 0  # 第一句中间 → 回到第一句开头
            else:
                return "break"  # 已在第一句开头
        off = min(off, n)
        self.char_offset = off
        was_active = self.tts.is_active()
        if was_active:
            # 朗读中：直接切换朗读位置，继续朗读
            self._start_tts(self.book, self.chapter_idx, off)
            self._set_tts_ui("playing")
        else:
            # 未朗读：仅跳转阅读位置
            self._scroll_to_offset(off)
            self._update_status()
            self._schedule_save()
        return "break"

    def _has_any_book_cache(self):
        """检查是否有任何一本书存在整本缓存。"""
        try:
            cache_dir = self.storage.get_setting("tts_cache_dir", "")
            if not cache_dir or not os.path.isdir(cache_dir):
                return False
            for entry in os.listdir(cache_dir):
                full = os.path.join(cache_dir, entry)
                if os.path.isdir(full):
                    for root, dirs, files in os.walk(full):
                        for f in files:
                            if f.endswith(".mp3"):
                                return True
        except Exception:
            pass
        return False
