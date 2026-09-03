# -*- coding: utf-8 -*-
"""书籍导入：阅读区右键菜单 / 拖拽导入 / 批量导入 / 重复确认 / 删除书籍（从 gui.py 拆分的 Mixin 之一）。"""
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

class ImportMixin:
    """书籍导入：阅读区右键菜单 / 拖拽导入 / 批量导入 / 重复确认 / 删除书籍"""
    def _popup_text_menu(self, event):
        try:
            self._ctx_index = self.text.index(f"@{event.x},{event.y}")
        except Exception:
            self._ctx_index = None
        try:
            self.text_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.text_menu.grab_release()
    def _selected_text(self):
        try:
            if self.text.tag_ranges("sel"):
                return self.text.get("sel.first", "sel.last").strip()
        except Exception:
            pass
        return ""
    def _snippet_under_cursor(self):
        """右键位置没有选中文字时，取光标所在行（过长则截取光标附近）作为搜索词。"""
        idx = getattr(self, "_ctx_index", None)
        if not idx:
            return ""
        try:
            line = int(idx.split(".")[0])
            col = int(idx.split(".")[1])
            txt = self.text.get(f"{line}.0", f"{line}.end").strip()
            if not txt:
                return ""
            if len(txt) <= 120:
                return txt
            lo = max(0, col - 40)
            hi = min(len(txt), col + 80)
            return txt[lo:hi]
        except Exception:
            return ""
    def _copy_selection(self):
        t = self._selected_text()
        if not t:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(t)
    def _search_selection(self, engine):
        t = self._selected_text()
        if not t:
            t = self._snippet_under_cursor()
        if not t:
            messagebox.showinfo("提示", "请先在阅读区选中文字，或右键点击某一行。")
            return
        q = urllib.parse.quote(t)
        urls = {
            "baidu": f"https://www.baidu.com/s?wd={q}",
            "google": f"https://www.google.com/search?q={q}",
            "bing": f"https://www.bing.com/search?q={q}",
            "translate": f"https://translate.google.com/?sl=auto&tl=zh-CN&text={q}",
        }
        webbrowser.open(urls[engine])
    def _open_selected(self):
        bid = self._selected_bid()
        if bid:
            self.open_book(bid)
    def _setup_drag_drop(self):
        """绑定文件拖拽（tkinterdnd2 原生方案，事件在主线程，稳定不闪退）。

        只绑定主窗口，整个窗口区域都能拖入文件。
        若 tkinterdnd2 不可用（降级为普通 Tk），则拖拽功能不可用，不影响其他功能。
        """
        try:
            if not hasattr(self.root, 'drop_target_register'):
                return  # 普通 Tk，不支持拖拽
            from tkinterdnd2 import DND_FILES
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop_files)
        except Exception:
            pass
    def _on_drop_files(self, event):
        """拖拽释放回调（tkinterdnd2 在主线程调用）。"""
        try:
            # event.data 是空格分隔的文件路径字符串，含空格的路径用 {} 包裹
            paths = list(self.root.tk.splitlist(event.data))
            self._handle_dropped_files(paths)
        except Exception as e:
            try:
                if self.root.winfo_exists():
                    messagebox.showerror("拖拽导入失败", str(e))
            except Exception:
                pass
    def _handle_dropped_files(self, paths):
        """处理拖拽进来的文件列表（在主线程调用）。任何异常都不闪退。"""
        try:
            if not self.root.winfo_exists():
                return
            paths = [p for p in paths if p and os.path.isfile(p)]
            if not paths:
                return
            try:
                paths = self._filter_large_files(paths)
            except Exception:
                return
            if not paths:
                return
            self._import_many(paths)
        except Exception as e:
            try:
                if self.root.winfo_exists():
                    messagebox.showerror("拖拽导入失败", str(e))
            except Exception:
                pass
    def _add_book(self):
        paths = filedialog.askopenfilenames(title="选择要加入书架的小说（可多选）", filetypes=FILE_TYPES)
        if not paths:
            return
        paths = [p for p in paths if p]
        if not paths:
            return
        # 大文件确认：超过 25MB 弹确认，防止误选
        paths = self._filter_large_files(paths)
        if not paths:
            return
        # v1.93：单本 / 多本统一走后台线程 + 进度条窗口，解析分章不卡 UI
        self._import_many(paths)
    def _filter_large_files(self, paths, threshold_mb=25):
        """检查文件大小，超过阈值的弹确认窗口询问是否选中正确文件。返回确认后的路径列表。"""
        large = []
        for p in paths:
            try:
                size_mb = os.path.getsize(p) / (1024 * 1024)
                if size_mb > threshold_mb:
                    large.append((p, size_mb))
            except OSError:
                continue
        if not large:
            return paths
        # 弹确认窗口
        dlg = tk.Toplevel(self.root)
        dlg.title("文件较大")
        dlg.geometry("480x220")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        names = "\n".join(f"  · {os.path.basename(p)} ({sz:.1f}MB)" for p, sz in large)
        tk.Label(dlg, text=f"以下 {len(large)} 个文件超过 {threshold_mb}MB：",
                 font=("微软雅黑", 10, "bold")).pack(pady=(14, 6), anchor="w", padx=16)
        tk.Label(dlg, text=names, fg="#444444", font=("微软雅黑", 9),
                 justify="left", anchor="w").pack(padx=20, anchor="w")
        tk.Label(dlg, text="确认选中的是小说文件？",
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(8, 4))
        result = [None]
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text="确认导入", width=10,
                  command=lambda: (result.__setitem__(0, True), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="取消", width=8,
                  command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        if result[0]:
            return paths
        return []
    def _import_single(self, path):
        bid = self.storage.book_id(path)
        existing = self.storage.get_book(bid)
        if existing:
            # 重复书籍：弹确认，避免直接覆盖/重解析百万字大书造成假死
            choice = self._ask_duplicate(existing.get("title", os.path.basename(path)))
            if choice is None:
                return
            if choice == "reparse":
                self._reprocess_book(path, bid)
                return
            # 覆盖：沿用旧解析结果，仅更新书架记录（读缓存，不重新分章）
            try:
                content = self._load_book(path)
            except Exception as e:
                messagebox.showerror("无法打开", f"读取缓存失败：\n{e}")
                return
            self._save_import(content, path)
            self._refresh_bookshelf()
            self.open_book(bid)
            self._flash_status(f"已覆盖「{content.title}」（沿用已有解析）")
            return
        # 首次导入：后台线程 + 进度条窗口（v1.93：单本也弹进度窗，解析分章不卡 UI）
        self._import_many([path])
    def _ask_duplicate(self, title):
        """重复书籍确认框。返回 None=取消 / "overwrite"=覆盖 / "reparse"=重新预处理。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("重复书籍")
        dlg.geometry("460x200")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=f"「{title}」已在书架中", font=("微软雅黑", 10, "bold")).pack(pady=(16, 4))
        tk.Label(dlg, text="覆盖：沿用已有分章解析，立即完成；\n重新预处理：丢弃旧解析重新分章（长篇耗时较久）。",
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(2, 8))
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text="覆盖", width=10,
                  command=lambda: (result.__setitem__(0, "overwrite"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="重新预处理文本", width=14,
                  command=lambda: (result.__setitem__(0, "reparse"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="取消", width=8, command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]
    def _reprocess_book(self, path, bid):
        """重新预处理：后台线程强制重新解析分章，带进度窗，避免百万字假死。"""
        win = tk.Toplevel(self.root)
        win.title("重新预处理")
        win.geometry("380x130")
        win.resizable(False, False)
        win.transient(self.root)
        self._center_window(win)
        try:
            win.grab_set()
        except Exception:
            pass
        tk.Label(win, text="正在重新解析并分章，请稍候…", font=("微软雅黑", 11)).pack(pady=(18, 8))
        bar = ttk.Progressbar(win, mode="indeterminate")
        bar.pack(fill="x", padx=28)
        bar.start(12)
        done = queue.Queue()

        def worker():
            try:
                content = book_loader.parse_book(path)  # 强制重新解析
                self.storage.write_cache(bid, content)
                self._cache[bid] = content
                done.put(("ok", content))
            except Exception as e:
                done.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                msg = done.get_nowait()
            except queue.Empty:
                win.after(100, poll)
                return
            bar.stop()
            win.destroy()
            if msg[0] == "ok":
                content = msg[1]
                self._save_import(content, path)
                self._refresh_bookshelf()
                self.open_book(bid)
                self._flash_status(f"已重新预处理「{content.title}」")
            else:
                messagebox.showerror("解析失败", msg[1])

        win.after(100, poll)
    def _save_import(self, content, path):
        """把解析结果写入书架与缓存，返回书籍 id。"""
        bid = self.storage.book_id(path)
        meta = {
            "id": bid,
            "title": content.title,
            "author": content.author,
            "format": content.format,
            "path": path,
            "added_at": time.time(),
            "last_read_at": time.time(),
            "total_chars": content.total_chars,
            "chapter_titles": [c.title for c in content.chapters],
            "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
        }
        self.storage.add_book(meta)
        self.storage.write_cache(bid, content)
        self._cache[bid] = content
        return bid
    def _import_many(self, paths):
        """批量导入：弹出进度条窗口，后台线程逐本解析分章，全部完成后刷新书架。"""
        total = len(paths)
        self._first_imported_bid = None
        # 重复书籍处理：弹一次确认，避免直接覆盖/重解析百万字大书造成假死
        force_reparse = set()
        dups = []
        for p in paths:
            b = self.storage.book_id(p)
            if self.storage.get_book(b):
                dups.append((p, b))
        if dups:
            choice = self._ask_duplicate_batch(len(dups))
            if choice is None:
                return
            if choice == "reparse":
                force_reparse = {b for _, b in dups}
        win = tk.Toplevel(self.root)
        win.title("正在导入")
        win.geometry("400x140")
        win.resizable(False, False)
        win.transient(self.root)
        self._center_window(win)
        try:
            win.grab_set()  # 模态，防止导入期间误操作
        except Exception:
            pass
        tk.Label(win, text="正在解析并分章，请稍候…", font=("微软雅黑", 11)).pack(pady=(16, 6))
        prog_var = tk.DoubleVar(value=0)
        bar = ttk.Progressbar(win, maximum=total, variable=prog_var)
        bar.pack(fill="x", padx=28)
        info_lbl = tk.Label(win, text=f"0 / {total}", font=("微软雅黑", 10))
        info_lbl.pack(pady=(8, 0))
        name_lbl = tk.Label(win, text="", fg="#666666", font=("微软雅黑", 9), wraplength=360)
        name_lbl.pack(pady=(2, 0))

        done = queue.Queue()  # ("ok", bid) / ("err", path, msg) / ("done",)
        self._import_worker_stop = threading.Event()

        def worker():
            for p in paths:
                if self._import_worker_stop.is_set():
                    break
                try:
                    bid = self.storage.book_id(p)
                    content = self._load_book(p, force_reparse=(bid in force_reparse))
                    bid = self._save_import(content, p)
                    done.put(("ok", bid, os.path.basename(p)))
                except Exception as e:
                    done.put(("err", os.path.basename(p), str(e)))
            done.put(("done",))

        threading.Thread(target=worker, daemon=True).start()
        self._import_win = win
        self._import_queue = done
        self._poll_import(win, prog_var, info_lbl, name_lbl, total)
    def _ask_duplicate_batch(self, count):
        """批量导入中重复书籍的确认框。返回 None=取消 / "overwrite" / "reparse"。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("重复书籍")
        dlg.geometry("460x190")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=f"所选文件中有 {count} 本已在书架中", font=("微软雅黑", 10, "bold")).pack(pady=(16, 4))
        tk.Label(dlg, text="全部覆盖：沿用已有分章解析，立即完成；\n全部重新预处理：丢弃旧解析重新分章（长篇耗时较久）。",
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(2, 8))
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text="全部覆盖", width=10,
                  command=lambda: (result.__setitem__(0, "overwrite"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="全部重新预处理", width=14,
                  command=lambda: (result.__setitem__(0, "reparse"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text="取消导入", width=10, command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]
    def _poll_import(self, win, prog_var, info_lbl, name_lbl, total):
        if not win.winfo_exists():
            return
        try:
            while True:
                msg = self._import_queue.get_nowait()
                if msg[0] == "done":
                    win.grab_release()
                    win.destroy()
                    self._refresh_bookshelf()
                    # 打开第一本成功导入的书
                    if getattr(self, "_first_imported_bid", None):
                        self.open_book(self._first_imported_bid)
                    return
                if msg[0] == "ok":
                    if getattr(self, "_first_imported_bid", None) is None:
                        self._first_imported_bid = msg[1]
                prog_var.set(prog_var.get() + 1)
                info_lbl.configure(text=f"{int(prog_var.get())} / {total}")
                name_lbl.configure(text=msg[1] if msg[0] == "ok" else f"失败：{msg[1]}：{msg[2]}")
        except queue.Empty:
            pass
        self.root.after(80, lambda: self._poll_import(win, prog_var, info_lbl, name_lbl, total))
    def _remove_book(self, clear_cache=False):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        label = "删除文件" if clear_cache else "删除书籍"
        tip = ("（从书架移除并清空缓存，不删除原文件）" if clear_cache
               else "（从书架移除，保留缓存，不删除原文件）")
        if not messagebox.askyesno(label, f"确定{label}《{meta.get('title','')}》？\n{tip}"):
            return
        self.tts.stop()
        self.storage.remove_book(bid)
        self._cache.pop(bid, None)
        if clear_cache:
            # 删除文本解析缓存
            cp = self.storage.cache_path(bid)
            if cp and os.path.exists(cp):
                try:
                    os.remove(cp)
                except Exception:
                    pass
            # 同步删除该书音频缓存（整本语音缓存）
            self._delete_audio_cache(bid)
        if self.current_bid == bid:
            self.current_bid = None
            self.book = None
            self._render_empty()
        self._refresh_bookshelf()
