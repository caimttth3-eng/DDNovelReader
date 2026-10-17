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
from . import chapterizer  # 分章异常诊断与人工重切（2026-10-17）
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
            messagebox.showinfo(_T("提示"), _T("请先在阅读区选中文字，或右键点击某一行。"))
            return
        q = urllib.parse.quote(t)
        urls = {
            "baidu": f"https://www.baidu.com/s?wd={q}",
            "google": f"https://www.google.com/search?q={q}",
            "bing": f"https://www.bing.com/search?q={q}",
            "baidu_translate": f"https://fanyi.baidu.com/#auto/zh/{q}",
            "google_translate": f"https://translate.google.com/?sl=auto&tl=zh-CN&text={q}",
            "sogou_translate": f"https://fanyi.sogou.com/text?keyword={q}",
        }
        url = urls[engine]
        try:
            import os
            os.startfile(url)
        except Exception:
            try:
                webbrowser.open(url)
            except Exception as e:
                messagebox.showerror(_T("错误"), f"{_T('打开浏览器失败')}: {e}")
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
                    messagebox.showerror(_T("拖拽导入失败"), str(e))
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
                    messagebox.showerror(_T("拖拽导入失败"), str(e))
            except Exception:
                pass
    def _add_book(self):
        paths = filedialog.askopenfilenames(title=_T("选择要加入书架的小说（可多选）"), filetypes=FILE_TYPES)
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
        dlg.title(_T("文件较大"))
        dlg.geometry("480x220")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        names = "\n".join(f"  · {os.path.basename(p)} ({sz:.1f}MB)" for p, sz in large)
        tk.Label(dlg, text=_T("以下 {n} 个文件超过 {mb}MB：").format(n=len(large), mb=threshold_mb),
                 font=("微软雅黑", 10, "bold")).pack(pady=(14, 6), anchor="w", padx=16)
        tk.Label(dlg, text=names, fg="#444444", font=("微软雅黑", 9),
                 justify="left", anchor="w").pack(padx=20, anchor="w")
        tk.Label(dlg, text=_T("确认选中的是小说文件？"),
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(8, 4))
        result = [None]
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text=_T("确认导入"), width=14,
                  command=lambda: (result.__setitem__(0, True), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("取消"), width=8,
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
                messagebox.showerror(_T("无法打开"), _T("读取缓存失败：\n{e}").format(e=e))
                return
            self._save_import(content, path)
            self._refresh_bookshelf()
            self.open_book(bid)
            self._flash_status(_T("已覆盖「{title}」（沿用已有解析）").format(title=content.title))
            return
        # 首次导入：后台线程 + 进度条窗口（v1.93：单本也弹进度窗，解析分章不卡 UI）
        self._import_many([path])
    def _ask_duplicate(self, title):
        """重复书籍确认框。返回 None=取消 / "overwrite"=覆盖 / "reparse"=重新预处理。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(_T("重复书籍"))
        dlg.geometry("460x200")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=_T("「{title}」已在书架中").format(title=title), font=("微软雅黑", 10, "bold")).pack(pady=(16, 4))
        tk.Label(dlg, text=_T("覆盖：沿用已有分章解析，立即完成；\n重新预处理：丢弃旧解析重新分章（长篇耗时较久）。"),
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(2, 8))
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text=_T("覆盖"), width=10,
                  command=lambda: (result.__setitem__(0, "overwrite"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("重新预处理文本"), width=14,
                  command=lambda: (result.__setitem__(0, "reparse"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("取消"), width=8, command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]
    def _read_raw_text(self, path):
        """读取 txt 原文（多编码探测，与解析引擎一致）。"""
        with open(path, "rb") as f:
            raw = f.read()
        return book_loader._decode(raw)

    def _fmt_anomaly(self, a):
        """把诊断异常 dict 翻译为用户可见的原因文本。"""
        code = a.get("code")
        if code == "A1":
            return _T("未能识别章节标题（{c} 个疑似标题仅识别 {h} 个），整本被切成 {n} 节").format(**a)
        if code == "A2":
            return _T("疑似有 {m} 个标题未被采用（识别 {h} / 候选 {c}）").format(**a)
        if code == "A3":
            return _T("有 {n} 个以句号/分号结尾的标题未被采用（可能是章节标题）").format(**a)
        if code == "A4":
            return _T("有 {n} 个较长标题（超过 40 字）未被采用").format(**a)
        if code == "A5":
            return _T("全书未发现任何章节标题标记（候选行为 0），按大小切成了 {n} 节").format(**a)
        if code == "A6":
            return _T("书很大但只有 {n} 节，章节标题可能整体未被识别").format(**a)
        if code == "B1":
            return _T("有 {n} 个极短章节，可能是正文被误判为标题").format(**a)
        if code == "C1":
            return _T("章节编号存在断号（如 {a} 后接 {b}）").format(**a)
        return str(a)

    def _ask_chapter_anomaly(self, title, diag):
        """分章异常弹窗：让用户确认候选标题 / 手动输入标记 / 保持现状。

        返回 "candidates" / "marker" / "keep" / None（None=关闭弹窗，等同保持现状）。
        """
        anomalies = diag.get("anomalies") or []
        cands_n = len(diag.get("candidates") or [])
        n = diag.get("n", 0)
        has_cands = cands_n >= 2
        dlg = tk.Toplevel(self.root)
        dlg.title(_T("分章异常"))
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=_T("「{title}」的分章可能有问题").format(title=title),
                 font=("微软雅黑", 10, "bold"), wraplength=440).pack(pady=(14, 4), padx=16)
        reasons = "；".join(self._fmt_anomaly(a) for a in anomalies)
        tk.Label(dlg, text=_T("当前切分为 {n} 节。\n{reasons}").format(n=n, reasons=reasons),
                 fg="#666666", font=("微软雅黑", 9), wraplength=440, justify="left").pack(pady=(2, 6), padx=16)
        samples = diag.get("samples") or []
        if samples:
            box = tk.Text(dlg, height=5, width=56, font=("微软雅黑", 9), wrap="word")
            box.pack(padx=16, pady=(0, 6))
            box.insert("1.0", _T("疑似章节标题样例（前 {k} 个）：\n").format(k=len(samples)) + "\n".join(samples))
            box.configure(state="disabled")
        ops = tk.Frame(dlg)
        ops.pack(pady=8)
        if has_cands:
            tk.Button(ops, text=_T("按这些标题切分"), width=14,
                      command=lambda: (result.__setitem__(0, "candidates"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("手动输入标题标记"), width=15,
                  command=lambda: (result.__setitem__(0, "marker"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("保持现状"), width=10,
                  command=lambda: (result.__setitem__(0, "keep"), dlg.destroy())).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]

    def _ask_marker(self, title):
        """手动输入章节标题关键词的输入框。返回标记字符串或 None。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(_T("手动分章"))
        dlg.geometry("480x200")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=_T("「{title}」的章节标题关键词是什么？").format(title=title),
                 font=("微软雅黑", 10, "bold"), wraplength=440).pack(pady=(14, 4), padx=16)
        tk.Label(dlg, text=_T("输入章节标题的开头关键词，例如：第X章 / 第1章 / Chapter / 卷一\nX 可代表任意数字；也可粘贴一个章节标题样例行。"),
                 fg="#666666", font=("微软雅黑", 9), wraplength=440, justify="left").pack(pady=(2, 6), padx=16)
        var = tk.StringVar()
        ent = tk.Entry(dlg, textvariable=var, font=("微软雅黑", 10), width=36)
        ent.pack(pady=(0, 8), padx=16)
        ent.focus_set()

        def on_ok():
            result[0] = var.get().strip() or None
            dlg.destroy()

        ent.bind("<Return>", lambda e: on_ok())
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text=_T("确定"), width=10, command=on_ok).pack(side="left", padx=6)
        tk.Button(ops, text=_T("取消"), width=8, command=dlg.destroy).pack(side="left", padx=6)
        self.root.wait_window(dlg)
        return result[0]

    def _apply_chapter_choice(self, content, path, diag):
        """按用户在异常弹窗的选择重新分章（候选确认 / 手动标记）。

        返回 (content, changed)；未弹窗或用户保持现状时不改动。
        """
        if not diag or not (diag.get("anomalies") or []):
            return content, False
        choice = self._ask_chapter_anomaly(content.title, diag)
        if choice is None or choice == "keep":
            return content, False
        new_pairs = None
        if choice == "candidates":
            new_pairs = chapterizer.split_by_candidates(diag.get("text") or "")
        elif choice == "marker":
            marker = self._ask_marker(content.title)
            if marker:
                new_pairs = chapterizer.split_by_marker(diag.get("text") or "", marker)
        if not new_pairs or len(new_pairs) < 2:
            messagebox.showwarning(_T("分章异常"), _T("未能按所选方式重新分章，保持当前结果。"))
            return content, False
        # 替换分章结果并重算累计长度（进度/百分比依赖 cum/total_chars）
        content.chapters = [book_loader.Chapter(t, book_loader.normalize_body(b)) for t, b in new_pairs]
        content.cum = [0]
        for c in content.chapters:
            content.cum.append(content.cum[-1] + len(c.content))
        content.total_chars = content.cum[-1]
        self._flash_status(_T("已按你的选择重新分章：{n} 章").format(n=len(content.chapters)))
        return content, True

    def _reprocess_book(self, path, bid):
        """重新预处理：后台线程强制重新解析分章，带进度窗，避免百万字假死。"""
        win = tk.Toplevel(self.root)
        win.title(_T("重新预处理"))
        win.geometry("380x130")
        win.resizable(False, False)
        win.transient(self.root)
        self._center_window(win)
        try:
            win.grab_set()
        except Exception:
            pass
        tk.Label(win, text=_T("正在重新解析并分章，请稍候…"), font=("微软雅黑", 11)).pack(pady=(18, 8))
        bar = ttk.Progressbar(win, mode="indeterminate")
        bar.pack(fill="x", padx=28)
        bar.start(12)
        done = queue.Queue()

        def worker():
            try:
                content = book_loader.parse_book(path)  # 强制重新解析
                diag = None
                # 仅纯文本格式可做分章诊断（epub/pdf/chm 章节来自文件结构，不适用）
                if content.format in ("txt",):
                    try:
                        diag = chapterizer.diagnose(_read_raw_text(path), chapters=content.chapters)
                    except Exception:
                        diag = None
                done.put(("ok", content, diag))
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
                content, diag = msg[1], msg[2]
                # 分章异常 → 弹窗让用户选择（候选确认 / 手动标记 / 保持现状）
                content, _changed = self._apply_chapter_choice(content, path, diag)
                self._save_import(content, path)
                self._refresh_bookshelf()
                self.open_book(bid)
                self._flash_status(_T("已重新预处理「{title}」").format(title=content.title))
            else:
                messagebox.showerror(_T("解析失败"), msg[1])

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
        # 备份源文件到缓存目录：原文件被删除/移动后仍可重解析、复制原文件
        try:
            bak = self.storage.backup_source(bid, path)
            if bak:
                meta["source_bak"] = bak
        except Exception:
            pass
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
        win.title(_T("正在导入"))
        win.geometry("400x140")
        win.resizable(False, False)
        win.transient(self.root)
        self._center_window(win)
        try:
            win.grab_set()  # 模态，防止导入期间误操作
        except Exception:
            pass
        tk.Label(win, text=_T("正在解析并分章，请稍候…"), font=("微软雅黑", 11)).pack(pady=(16, 6))
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
                    diag = None
                    if content and content.format in ("txt",):
                        try:
                            diag = chapterizer.diagnose(_read_raw_text(p), chapters=content.chapters)
                        except Exception:
                            diag = None
                    bid = self._save_import(content, p)
                    done.put(("ok", bid, os.path.basename(p), diag))
                except Exception as e:
                    done.put(("err", os.path.basename(p), str(e)))
            done.put(("done",))

        threading.Thread(target=worker, daemon=True).start()
        self._import_win = win
        self._import_queue = done
        self._import_anomalies = []
        self._poll_import(win, prog_var, info_lbl, name_lbl, total)
    def _ask_duplicate_batch(self, count):
        """批量导入中重复书籍的确认框。返回 None=取消 / "overwrite" / "reparse"。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(_T("重复书籍"))
        dlg.geometry("460x190")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        try:
            dlg.grab_set()
        except Exception:
            pass
        result = [None]
        tk.Label(dlg, text=_T("所选文件中有 {n} 本已在书架中").format(n=count), font=("微软雅黑", 10, "bold")).pack(pady=(16, 4))
        tk.Label(dlg, text=_T("全部覆盖：沿用已有分章解析，立即完成；\n全部重新预处理：丢弃旧解析重新分章（长篇耗时较久）。"),
                 fg="#666666", font=("微软雅黑", 9)).pack(pady=(2, 8))
        ops = tk.Frame(dlg)
        ops.pack(pady=6)
        tk.Button(ops, text=_T("全部覆盖"), width=13,
                  command=lambda: (result.__setitem__(0, "overwrite"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("全部重新预处理"), width=14,
                  command=lambda: (result.__setitem__(0, "reparse"), dlg.destroy())).pack(side="left", padx=6)
        tk.Button(ops, text=_T("取消导入"), width=13, command=dlg.destroy).pack(side="left", padx=6)
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
                    self._after_import_check(total)
                    return
                if msg[0] == "ok":
                    _bid, _name, _diag = msg[1], msg[2], msg[3]
                    if getattr(self, "_first_imported_bid", None) is None:
                        self._first_imported_bid = msg[1]
                    if _diag and (_diag.get("anomalies") or []):
                        anoms = getattr(self, "_import_anomalies", [])
                        anoms.append((msg[1], msg[2]))
                        self._import_anomalies = anoms
                prog_var.set(prog_var.get() + 1)
                info_lbl.configure(text=f"{int(prog_var.get())} / {total}")
                name_lbl.configure(text=msg[2] if msg[0] == "ok" else _T("失败：{a}：{b}").format(a=msg[1], b=msg[2]))
        except queue.Empty:
            pass
        self.root.after(80, lambda: self._poll_import(win, prog_var, info_lbl, name_lbl, total))

    def _after_import_check(self, total):
        """导入完成后处理分章异常的书：单本直接弹窗；多本汇总提示，用户可选逐本处理。"""
        anoms = getattr(self, "_import_anomalies", [])
        self._import_anomalies = []
        if not anoms:
            return
        if total == 1:
            bid, _name = anoms[0]
            meta = self.storage.get_book(bid)
            if meta and meta.get("path"):
                self._reprocess_book(meta["path"], bid)
            return
        names = "\n".join(f"  · {nm}" for _b, nm in anoms)
        if messagebox.askyesno(
                _T("分章异常"),
                _T("{n} 本书分章可能异常：\n{names}\n\n是否逐本处理？").format(n=len(anoms), names=names)):
            for bid, _name in anoms:
                meta = self.storage.get_book(bid)
                if meta and meta.get("path"):
                    self._reprocess_book(meta["path"], bid)
                    break
    def _remove_book(self, clear_cache=False):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        label = _T("删除文件") if clear_cache else _T("删除书籍")
        tip = (_T("（从书架移除并清空缓存，不删除原文件）") if clear_cache
               else _T("（从书架移除，保留缓存，不删除原文件）"))
        if not messagebox.askyesno(label, _T("确定{label}《{title}》？\n{tip}").format(label=label, title=meta.get("title", ""), tip=tip)):
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
            # 删除源文件备份（原文件不删，只删软件自带的副本）
            try:
                self.storage.remove_source_backup(bid)
            except Exception:
                pass
            # 同步删除该书音频缓存（整本语音缓存）
            self._delete_audio_cache(bid)
        if self.current_bid == bid:
            self.current_bid = None
            self.book = None
            self._render_empty()
        self._refresh_bookshelf()
