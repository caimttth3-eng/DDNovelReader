# -*- coding: utf-8 -*-
"""缓存管理：正文 / 音频缓存文件夹自定义、一键转移、清除、大小统计（从 gui.py 拆分的 Mixin 之一）。"""
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

class CacheMixin:
    """缓存管理：正文 / 音频缓存文件夹自定义、一键转移、清除、大小统计"""
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
            # 新位置后台一次性校准（索引随转移一起移动，校准补漏）
            threading.Thread(
                target=lambda: self.tts.tts_cache_calibrate(), daemon=True
            ).start()
        label = "正文解析缓存" if kind == "text" else "音频缓存"
        messagebox.showinfo("转移完成", f"{label}已转移到：\n{new}")
    def _update_tts_cache_size_label(self, lbl):
        try:
            siz = self.tts.tts_cache_size()
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
                if name.startswith(".tts_sizes"):
                    continue  # 索引文件由 invalidate 统一处理（条目清空后删除）
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
        try:
            self.tts.tts_cache_invalidate()
        except Exception:
            pass
        if size_lbl is not None:
            self._update_tts_cache_size_label(size_lbl)
        self._refresh_bookshelf()
        messagebox.showinfo("已清除", f"已清除 {n} 个音频缓存目录。")
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
    def _on_status_cache_click(self, event=None):
        """点击右下角整本缓存状态：在 暂停/继续 之间切换下载。"""
        try:
            st = self.tts.book_cache_status(self.current_bid)
        except Exception:
            st = None
        if not st:
            return
        if st["state"] == "caching":
            self.tts.pause_book_cache(self.current_bid)
            self._flash_status(f"已暂停整本缓存（{st['done']}/{st['total']}），点击状态可继续")
        elif st["state"] == "paused":
            self.tts.resume_book_cache(self.current_bid)
            self._flash_status("已继续整本缓存下载")
