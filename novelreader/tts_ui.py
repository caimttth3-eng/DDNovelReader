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
from .tts_engine import SpeechController, split_sentences
from .content_key import (
    RATE_MIN as _RATE_MIN,
    RATE_MAX as _RATE_MAX,
    format_multiplier as _format_multiplier,
)
from .textproc import clean_to_orig
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
    def _start_tts(self, book, ci, off, keep_prefetch=False):
        """统一朗读入口（多人有声模块已下线，保留此入口供 tts_ui / reader_ui 调用）。

        keep_prefetch=True 用于快捷键跳句：目标句通常已在预取队列里，直接取用即可
        立即出声，不必丢弃队列重等一次完整合成。"""
        # GPT-SoVITS：目录未填→同步引导；填过但服务未启动→弹动画窗后台启动（不卡界面），
        # 启动完成后自动继续朗读。
        try:
            if getattr(self.tts, "is_gptsovits_active", lambda: False)():
                self._ensure_gptsovits_async(
                    lambda ok, why: self._on_gptsovits_ready(ok, why, book, ci, off, keep_prefetch))
                return
        except Exception:
            pass  # 探测/启动逻辑异常不影响正常朗读
        self.tts.start(book, ci, off, keep_prefetch=keep_prefetch)

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

    def _on_gptsovits_ready(self, ok, why, book, ci, off, keep_prefetch=False):
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
        self.tts.start(book, ci, off, keep_prefetch=keep_prefetch)

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
        timer = getattr(self, "_jump_timer", None)
        if timer:
            try:
                self.root.after_cancel(timer)
            except Exception:
                pass
        self._jump_timer = None
        self._jump_reading = False
        self.tts.stop()
        self._set_tts_ui("stopped")
        self._clear_highlight()
        lyr = getattr(self, "_lyric", None)
        if lyr is not None:
            try:
                lyr.clear()
            except Exception:
                pass
    def _toggle_lyric(self):
        """桌面模式（歌词式悬浮窗）开关：开 = 显示当前朗读句的置顶悬浮窗，关 = 关闭。"""
        lyr = getattr(self, "_lyric", None)
        try:
            alive = lyr is not None and lyr.win.winfo_exists()
        except Exception:
            alive = False
        if alive:
            try:
                lyr.close()
            except Exception:
                pass
            self._lyric = None
            self.tts_lyric_btn.configure(text=_T("桌面模式"))
            self._flash_status(_T("桌面歌词已关闭"))
            return
        from .lyric_ui import LyricWindow
        self._lyric = LyricWindow(self.root, self)
        self.tts_lyric_btn.configure(text=_T("桌面模式"))
        self._flash_status(_T("桌面歌词已开启，可拖动 / 缩放 / 锁定"))
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
        new_rate = max(_RATE_MIN, min(_RATE_MAX, old_rate + delta))
        if new_rate == old_rate:
            return
        if self._has_any_book_cache():
            from tkinter import messagebox
            if not messagebox.askyesno("提示", "更改语速后，已有的整本缓存将不命中，需要重新缓存。是否继续？"):
                return
        self.settings["tts_rate"] = new_rate
        try:
            self.rate_var.set(new_rate)      # 快捷键也要把滑块同步过去
        except Exception:
            pass
        self.rate_label.configure(text=_format_multiplier(new_rate))
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
        """快捷键 ← / →：跳到上一句 / 下一句（重写版）。

        执行顺序固定为三步，任何一步都不依赖其它步骤的时序：
        1. 先静音：`halt_for_jump()` 同步停掉当前音频并推进令牌，旧会话立刻作废；
        2. 再定位：阅读位置 / 高亮 / 进度条即时跟手（不等合成）；
        3. 后开读：防抖后只按最后一次按键的位置开一个会话（连按十来下也只开一次）。
        """
        if not self.book:
            return "break"
        offs = self._sentence_offsets(self.chapter_idx)
        if not offs:
            return "break"
        cur = 0
        for i, o in enumerate(offs):
            if o <= self.char_offset:
                cur = i
            else:
                break
        k = cur + (1 if delta > 0 else -1)
        cross = False
        if k >= len(offs):
            # 章末再按 → 直接进下一章第一句。否则一直按到头就停在最后一句不动，
            # 看起来就是"卡住"（按键既不高亮也不出声）。
            if self.chapter_idx + 1 >= len(self.book.chapters):
                return "break"  # 全书最后一句：真的没有下一句了
            ci, off, cross = self.chapter_idx + 1, 0, True
        elif k < 0:
            if self.chapter_idx <= 0:
                return "break"
            ci = self.chapter_idx - 1
            prev = self._sentence_offsets(ci)
            off, cross = (prev[-1] if prev else 0), True
        else:
            ci, off = self.chapter_idx, offs[k]
        # 是否"本来在朗读"：引擎在播/在暂停，或上一次跳句刚掐断、新会话还没开读。
        # 后者必须算朗读中，否则连按时第二次键看到 idle 就不再排期，朗读会断掉。
        self._jump_reading = bool(self._jump_reading or self.tts.is_active())
        if self._jump_reading:
            self.tts.halt_for_jump()   # 按键瞬间静音：同步停音频 + 令牌推进
        if cross:
            self.chapter_idx = ci        # 跨章：先换章再定位（位置须在渲染前给好）
            self.char_offset = off
            self._render_chapter()
            # 跨章渲染必须立刻落地：不然连按时下一键算出来的还是旧章的行列，
            # 高亮反复落在同一处，看起来就是"按了没反应"。
            try:
                self.text.update_idletasks()
            except Exception:
                pass
            self._schedule_save()
        else:
            self.char_offset = off
        self._jump_show(off)           # 高亮跟手
        if self._jump_reading:
            # 跨章时预取队列属于旧章，不能复用
            self._jump_keep = not cross
            self._jump_schedule()      # 只排一次，按最后位置开读
            self._set_tts_ui("playing")
        return "break"

    def _jump_show(self, off):
        """立刻把高亮 / 滚动 / 进度条挪到目标句（与朗读合成完全解耦）。"""
        try:
            content = self.book.chapters[self.chapter_idx].content
            probe = content[off:off + 80]
            ends = [probe.find(ch) for ch in "。！？!?…；;\n"]
            ends = [p for p in ends if p >= 0]
            if ends:
                probe = probe[:min(ends) + 1]
            self._highlight_sentence(self.chapter_idx, off, probe)
        except Exception:
            try:
                self._scroll_to_offset(off)
            except Exception:
                pass
        try:
            self._update_status()
            self._schedule_save()
        except Exception:
            pass

    def _sentence_offsets(self, ci):
        """本章每句的原文起点，与朗读引擎逐句推进的位置完全一致。

        引擎是按「纯净朗读文本」切句的，和按标点手算的结果并不相同；
        跳句必须用同一套切句，否则「高亮的那一句」和「读出来的那一句」会对不上。
        """
        ch = self.book.chapters[ci]
        key = (ci, id(ch), len(ch.content))
        cached = getattr(self, "_sent_off_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        offs = []
        try:
            clean, cmap = ch.tts_content()
            n = len(ch.content)
            pos = 0
            while pos < len(clean):
                seg = clean[pos:]
                frags = split_sentences(seg)
                if not frags:
                    break
                t = frags[0]
                s = seg.find(t)
                if s < 0:
                    break
                offs.append(clean_to_orig(cmap, pos + s, n))
                pos += s + len(t)
        except Exception:
            offs = []
        if not offs:
            # 兜底：拿不到引擎切句时退回按标点切（保证任何章节都能跳）
            offs = self._punct_sentence_offsets(ch.content)
        self._sent_off_cache = (key, offs)
        return offs

    @staticmethod
    def _punct_sentence_offsets(content):
        """按句末标点切句的兜底实现（引擎切句不可用时使用）。"""
        n = len(content)
        offs = [0]
        off = 0
        while off < n:
            i = off
            while i < n and content[i] not in "。！？!?…；;":
                i += 1
            if i >= n:
                break
            j = i + 1
            while j < n and content[j] in " \t\r\n\u3000\u00a0":
                j += 1
            if j >= n or j == offs[-1]:
                break
            offs.append(j)
            off = j
        return offs

    # ---- 快捷键跳句：按键即静音 → 高亮跟手 → 只按最后位置开一次会话 ----
    # 连按期间每一键都同步静音并重排防抖；只有最后一次按键的位置会真正开会话。
    _JUMP_DEBOUNCE_MS = 160
    _jump_timer = None
    _jump_reading = False
    _jump_keep = True

    def _jump_schedule(self):
        """排期开读：取消上一次排期，只保留最后这次（连按十来下也只开一次会话）。"""
        timer = getattr(self, "_jump_timer", None)
        if timer:
            try:
                self.root.after_cancel(timer)
            except Exception:
                pass
        self._jump_timer = self.root.after(self._JUMP_DEBOUNCE_MS, self._do_jump_start)

    def _do_jump_start(self):
        """防抖到点：按当前（也就是最后一次按键的）位置开一次会话。"""
        self._jump_timer = None
        if not self.book or not self._jump_reading:
            return
        self._jump_reading = False
        self._start_tts(self.book, self.chapter_idx, self.char_offset, keep_prefetch=True)
        self._set_tts_ui("playing")

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
