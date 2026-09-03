# -*- coding: utf-8 -*-
"""整本缓存下载管理器：多书任务列表 / 章节选择 / 暂停继续 / 继续上次 / 自动关机（从 gui.py 拆分的 Mixin 之一）。"""
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

class DownloadMixin:
    """整本缓存下载管理器：多书任务列表 / 章节选择 / 暂停继续 / 继续上次 / 自动关机"""
    def _open_cache_dialog(self):
        """整本缓存管理窗口（下载管理器）：列出书架所有书，多本可同时缓存。"""
        if getattr(self, "_cache_dlg", None) and self._cache_dlg.winfo_exists():
            try:
                self._cache_dlg.lift()
            except Exception:
                pass
            return
        self._cache_mgr_target_bid = None
        self._cache_mgr_target_book = None

        win = tk.Toplevel(self.root)
        win.title("整本缓存管理（多书）")
        win.geometry("880x600")
        win.minsize(720, 460)
        win.transient(self.root)
        self._center_window(win)
        self._cache_dlg = win

        tk.Label(
            win,
            text="整本缓存：每本书一条任务，可多本同时缓存；音频体积较大，请慎用。双击或点「章节选择」进入章节。",
            fg="#b00020", font=("微软雅黑", 9), anchor="w", wraplength=840,
        ).pack(fill="x", padx=12, pady=(8, 2))

        bar = tk.Frame(win)
        bar.pack(fill="x", padx=12, pady=4)
        tk.Button(bar, text="全部开始/继续", width=12, command=self._cache_mgr_all_start).pack(side="left")
        tk.Button(bar, text="全部暂停", width=10, command=self._cache_mgr_all_pause).pack(side="left", padx=4)

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=2)
        tree = ttk.Treeview(
            frame, columns=("title", "status", "progress", "size"),
            show="headings", selectmode="extended",
        )
        tree.heading("title", text="书名")
        tree.heading("status", text="状态")
        tree.heading("progress", text="进度")
        tree.heading("size", text="缓存大小")
        tree.column("title", width=280, anchor="w")
        tree.column("status", width=90, anchor="center")
        tree.column("progress", width=230, anchor="center")
        tree.column("size", width=110, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("caching", foreground="#1a6bbd")
        tree.tag_configure("paused", foreground="#c0392b")
        tree.tag_configure("done", foreground="#1a7a3a")
        tree.tag_configure("building", foreground="#8a6d00")
        tree.tag_configure("cur", background="#e6effb")
        tree.bind("<Double-1>", lambda e: self._cache_mgr_open_selected())
        tree.bind("<Button-3>", self._cache_mgr_menu)
        self._cache_tree = tree

        ops = tk.Frame(win)
        ops.pack(fill="x", padx=12, pady=6)
        tk.Button(ops, text="开始/继续", width=10, command=self._cache_mgr_start).pack(side="left")
        tk.Button(ops, text="暂停", width=7, command=self._cache_mgr_pause).pack(side="left", padx=4)
        tk.Button(ops, text="章节选择…", width=10, command=self._cache_mgr_open_selected).pack(side="left", padx=4)
        tk.Button(ops, text="删除音频缓存", width=11, command=self._cache_mgr_delete_audio).pack(side="left", padx=4)
        tk.Button(ops, text="关闭", width=8, command=win.destroy).pack(side="right")

        self._cache_mgr_status = tk.Label(win, text="", anchor="w", fg="#666666", font=("微软雅黑", 9))
        self._cache_mgr_status.pack(fill="x", padx=12, pady=(0, 8))

        self._cache_mgr_refresh_rows()
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.after(400, self._cache_mgr_tick, win)
    @staticmethod
    def _cache_bar_str(pct, width=20):
        """精细进度条：20 格 + 1/8 精度尾块，比粗字符条平滑。"""
        pct = max(0.0, min(100.0, float(pct)))
        filled = pct / 100.0 * width
        full = int(filled)
        s = "█" * full
        rest = width - full
        if rest > 0:
            frac = filled - full
            if frac >= 0.5 / 8:  # 过半 1/8 才画尾块，避免闪动
                idx = min(7, int(frac * 8))
                s += "▏▎▍▌▋▊▉█"[idx]
                rest -= 1
            s += "░" * rest
        return s
    def _cache_mgr_state_of(self, bid):
        try:
            st = self.tts.book_cache_status(bid)
        except Exception:
            st = None
        if not st:
            # 无正在运行的任务：读历史进度索引（重启/已结束后也能显示进度）
            try:
                st = self.tts.book_cache_history(bid)
            except Exception:
                st = None
        state = st["state"] if st else None
        if state == "caching":
            return "● 下载中", "caching", st
        if state == "building":
            return "⏳ 准备中…", "building", st
        if state == "paused":
            return "⏸ 已暂停", "paused", st
        if state == "done":
            return "✔ 已完成", "done", st
        if state == "cancelled":
            return "已取消", "", st
        return "未开始", "", st
    def _cache_mgr_row_values(self, bid, meta):
        stat_s, tag, st = self._cache_mgr_state_of(bid)
        if st and st.get("total"):
            d, t = st["done"], st["total"]
            pct = (d / t * 100) if t else 0
            prog_s = f"{self._cache_bar_str(pct)} {pct:.0f}% · {d}/{t}"
        else:
            size0 = self.tts.tts_cache_size(bid)
            prog_s = f"已缓存 {self._format_bytes(size0)}" if size0 else "-"
        size = self.tts.tts_cache_size(bid)
        size_s = self._format_bytes(size) if size else "-"
        title = meta.get("title") or os.path.basename(meta.get("path", ""))
        if bid == self.current_bid:
            tag = (tag + " cur").strip()
        return (title, stat_s, prog_s, size_s), tag
    def _cache_mgr_refresh_rows(self):
        tree = self._cache_tree
        tree.delete(*tree.get_children())
        for bid, meta in self.storage.all_books().items():
            vals, tag = self._cache_mgr_row_values(bid, meta)
            tree.insert("", "end", iid=bid, values=vals, tags=(tag,))
        self._cache_mgr_update_status()
    def _cache_mgr_update_status(self):
        try:
            books = self.storage.all_books()
            caching = sum(1 for b in books if self._is_caching(b))
            total = self.tts.tts_cache_size()
            sel = len(self._cache_tree.selection()) if hasattr(self, "_cache_tree") else 0
            self._cache_mgr_status.configure(
                text=f"已选 {sel} 本 · 缓存中 {caching} 本 · 音频缓存总大小 {self._format_bytes(total)}"
            )
        except Exception:
            pass
    def _is_caching(self, bid):
        try:
            st = self.tts.book_cache_status(bid)
            return st and st["state"] in ("caching", "building")
        except Exception:
            return False
    def _cache_mgr_tick(self, win):
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        try:
            tree = self._cache_tree
            for bid, meta in self.storage.all_books().items():
                if not tree.exists(bid):
                    continue
                vals, tag = self._cache_mgr_row_values(bid, meta)
                tree.item(bid, values=vals, tags=(tag,))
            self._cache_mgr_update_status()
        except Exception:
            pass
        try:
            if win.winfo_exists():
                win.after(400, self._cache_mgr_tick, win)
        except Exception:
            pass
    def _cache_mgr_selected_bids(self):
        try:
            return list(self._cache_tree.selection())
        except Exception:
            return []
    def _cache_mgr_start_one(self, bid, chapter_indices=None):
        """开始/继续：暂停中→恢复；缓存中→跳过；其余→从历史进度续传（已缓存自动跳过）。"""
        meta = self.storage.get_book(bid)
        if not meta:
            return

        def worker():
            try:
                if bid == self.current_bid and self.book is not None:
                    book = self.book
                elif bid in self._cache:
                    book = self._cache[bid]
                else:
                    book = self._load_book(meta["path"])
                try:
                    st = self.tts.book_cache_status(bid)
                except Exception:
                    st = None
                if st and st["state"] == "paused":
                    self.tts.resume_book_cache(bid)
                    return
                if st and st["state"] in ("caching", "building"):
                    return  # 已在下载，无需重复开始
                st = self.tts.start_book_cache(book, bid, chapter_indices, resume=True)
                if st and st.get("state") == "unsupported":
                    self.root.after(0, lambda: messagebox.showinfo(
                        "提示", "整本缓存仅支持 Edge 神经语音（如晓晓/云希等）。\n请在「语音」下拉框选择 Edge 音色后再试。"))
            except Exception as e:
                self.root.after(0, lambda e=e: messagebox.showinfo("提示", f"缓存失败：{e}"))
        threading.Thread(target=worker, daemon=True).start()
    def _cache_mgr_start(self):
        bids = self._cache_mgr_selected_bids()
        if not bids:
            messagebox.showinfo("提示", "请先在列表中选择至少一本书", parent=self._cache_dlg)
            return
        for bid in bids:
            self._cache_mgr_start_one(bid)
    def _cache_mgr_all_start(self):
        for bid in list(self.storage.all_books().keys()):
            self._cache_mgr_start_one(bid)
        self._cache_mgr_refresh_rows()
    def _cache_mgr_pause(self):
        for bid in self._cache_mgr_selected_bids():
            self.tts.pause_book_cache(bid)
    def _cache_mgr_all_pause(self):
        for bid in list(self.storage.all_books().keys()):
            self.tts.pause_book_cache(bid)
    def _cache_mgr_delete_audio(self):
        bids = self._cache_mgr_selected_bids()
        if not bids:
            messagebox.showinfo("提示", "请先选择要删除音频缓存的书", parent=self._cache_dlg)
            return
        if not messagebox.askyesno(
            "删除音频缓存",
            f"确定删除选中的 {len(bids)} 本书的音频缓存？\n（朗读需重新联网合成）",
            parent=self._cache_dlg,
        ):
            return
        for bid in bids:
            self._delete_audio_cache(bid)
        self._cache_mgr_refresh_rows()
    def _cache_mgr_open_selected(self):
        bids = self._cache_mgr_selected_bids()
        if not bids:
            messagebox.showinfo("提示", "请先选择一本书", parent=self._cache_dlg)
            return
        self._open_book_cache_dialog(bids[0])
    def _cache_mgr_menu(self, event):
        try:
            iid = self._cache_tree.identify_row(event.y)
            if iid and iid not in self._cache_tree.selection():
                self._cache_tree.selection_set(iid)
        except Exception:
            pass
        m = tk.Menu(self._cache_dlg, tearoff=0)
        m.add_command(label="打开该书缓存…", command=self._cache_mgr_open_selected)
        m.add_command(label="删除音频缓存", command=self._cache_mgr_delete_audio)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
    def _open_book_cache_dialog(self, bid):
        meta = self.storage.get_book(bid)
        if not meta:
            return
        if bid == self.current_bid and self.book is not None:
            book = self.book
        else:
            try:
                book = self._load_book(meta["path"])
            except Exception:
                cached = self.storage.read_cache(bid)
                if cached:
                    book = book_loader.BookContent.from_dict(cached)
                else:
                    messagebox.showerror("无法打开", f"无法读取书籍文件：\n{meta['path']}")
                    return
        self._cache_mgr_target_bid = bid
        self._cache_mgr_target_book = book

        win = tk.Toplevel(self.root)
        win.title("整本语音缓存 - 章节选择")
        win.geometry("660x740")
        win.minsize(600, 620)
        win.transient(self.root)
        self._center_window(win)
        self._cache_book_dlg = win
        self._cache_sel = {}
        self._cache_busy = True

        info = f"书籍：{book.title}    章节：{len(book.chapters)}"
        tk.Label(win, text=info, anchor="w", font=("微软雅黑", 10, "bold")).pack(
            fill="x", padx=12, pady=(10, 2)
        )
        self._cache_info2 = tk.Label(win, text="", anchor="w", fg="#666666", font=("微软雅黑", 9))
        self._cache_info2.pack(fill="x", padx=12)

        tk.Label(
            win,
            text="整本缓存功能用于网络不稳定时提前缓存减少卡顿；缓存音频体积较大，请慎用。",
            fg="#b00020", font=("微软雅黑", 9), anchor="w", justify="left", wraplength=600,
        ).pack(fill="x", padx=12, pady=(8, 0))
        self._cache_bar = ttk.Progressbar(win, maximum=100, value=0)
        self._cache_bar.pack(fill="x", padx=12, pady=(6, 4))
        self._cache_prog = tk.Label(win, text="尚未开始", anchor="w", font=("微软雅黑", 9))
        self._cache_prog.pack(fill="x", padx=12)
        self._cache_disk = tk.Label(win, text="缓存已占容量：0 MB", anchor="w", fg="#1a6bbd", font=("微软雅黑", 9))
        self._cache_disk.pack(fill="x", padx=12, pady=(2, 4))

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
        for ch in book.chapters:
            lb.insert("end", ch.title)
        self._cache_lb = lb

        btns = tk.Frame(win)
        btns.pack(fill="x", padx=12, pady=6)
        cur = self.chapter_idx if bid == self.current_bid else 0
        tk.Button(btns, text="全选", width=8, command=lambda: self._cache_select_all()).pack(side="left")
        tk.Button(btns, text="反选", width=8, command=lambda: self._cache_select_invert()).pack(side="left", padx=4)
        tk.Button(btns, text="从本章起", width=8, command=lambda: self._cache_select_from(cur)).pack(side="left", padx=4)
        tk.Label(btns, text="续传：已缓存过的句子自动跳过，无需重复下载", fg="#888888", font=("微软雅黑", 8)).pack(side="right")

        self._cache_shutdown_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            win,
            text="缓存完成后自动关机（60 秒倒计时，可运行 shutdown /a 取消）",
            variable=self._cache_shutdown_var,
            command=self._cache_sync_shutdown,
        ).pack(anchor="w", padx=12, pady=(2, 2))

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

        self._cache_select_all()
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.after(300, self._cache_tick, win)
        self._cache_sync_state()
    def _cache_selected(self):
        book = self._cache_mgr_target_book
        if book is None:
            return None
        n = len(book.chapters)
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
            self.tts.set_book_cache_auto_shutdown(self._cache_shutdown_var.get(), self._cache_mgr_target_bid)
        except Exception:
            pass
    def _cache_start(self):
        book = self._cache_mgr_target_book
        bid = self._cache_mgr_target_bid
        if not book or not bid:
            return
        idx = self._cache_selected()
        if idx == set() or (idx is not None and len(idx) == 0):
            messagebox.showinfo("提示", "请先选择至少一个要缓存的章节", parent=self._cache_book_dlg)
            return
        st = self.tts.start_book_cache(book, bid, idx)
        if st is None:
            return
        if st["state"] == "unsupported":
            messagebox.showinfo(
                "提示",
                "整本缓存仅支持 Edge 神经语音（如晓晓/云希等）。\n请在「语音」下拉框选择一个 Edge 音色后再试。",
                parent=self._cache_book_dlg,
            )
            return
        if st["state"] == "unavailable":
            messagebox.showinfo("提示", "当前书籍暂无可缓存的章节", parent=self._cache_book_dlg)
            return
        self._book_cache_done_flashed = False
        self._cache_sync_shutdown()
        self._cache_sync_state()
    def _cache_pause(self):
        self.tts.pause_book_cache(self._cache_mgr_target_bid)
        self._cache_sync_state()
    def _cache_resume(self):
        self.tts.resume_book_cache(self._cache_mgr_target_bid)
        self._cache_sync_state()
    def _cache_continue(self):
        """继续上次下载：若有暂停中的任务则恢复；否则从持久化进度精确续传。"""
        bid = self._cache_mgr_target_bid
        book = self._cache_mgr_target_book
        if not bid or not book:
            return
        try:
            st = self.tts.book_cache_status(bid)
        except Exception:
            st = None
        if st and st["state"] == "paused":
            self._cache_resume()
            self._cache_sync_state()
            return
        self._cache_select_all()
        st = self.tts.start_book_cache(book, bid, None, resume=True)
        if st is None:
            return
        if st.get("state") == "unsupported":
            messagebox.showinfo(
                "提示",
                "整本缓存仅支持 Edge 神经语音（如晓晓/云希等）。\n请在「语音」下拉框选择一个 Edge 音色后再试。",
                parent=self._cache_book_dlg,
            )
            return
        if st.get("state") == "unavailable":
            messagebox.showinfo("提示", "当前书籍暂无可缓存的章节", parent=self._cache_book_dlg)
            return
        if st.get("state") == "done":
            messagebox.showinfo("提示", "所有章节均已缓存完成。", parent=self._cache_book_dlg)
            return
        self._book_cache_done_flashed = False
        self._cache_sync_shutdown()
        self._cache_sync_state()
    def _cache_sync_state(self):
        try:
            st = self.tts.book_cache_status(self._cache_mgr_target_bid)
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
        """单书缓存窗口周期刷新进度/容量/按钮状态。"""
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        try:
            st = self.tts.book_cache_status(self._cache_mgr_target_bid)
        except Exception:
            st = None
        if st:
            state, done, total = st["state"], st["done"], st["total"]
            if state == "building" or total == 0:
                self._cache_bar.configure(value=0)
                self._cache_prog.configure(text="正在准备缓存任务…")
            else:
                pct = done / total * 100
                self._cache_bar.configure(value=pct)
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
            disk = self.tts.book_cache_disk_used(self._cache_mgr_target_bid)
            self._cache_disk.configure(text=f"缓存已占容量：{disk / 1048576:.1f} MB")
        else:
            self._cache_prog.configure(text="尚未开始")
        self._cache_sync_state()
        try:
            if win.winfo_exists():
                win.after(300, self._cache_tick, win)
        except Exception:
            pass
