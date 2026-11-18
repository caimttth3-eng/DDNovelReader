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
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror(_T("无法打开"), _T("打开缓存文件夹失败：{e}").format(e=e))
    def _choose_text_cache_folder(self, path_lbl=None, size_lbl=None):
        """自定义正文解析缓存文件夹：可顺带一键转移。"""
        cur = self._effective_text_cache_root()
        d = filedialog.askdirectory(
            title=_T("选择正文解析缓存文件夹"),
            initialdir=os.path.dirname(cur),
        )
        if not d:
            return
        move = messagebox.askyesno(
            _T("转移缓存"),
            _T("已选择新缓存文件夹：\n{d}\n\n").format(d=d) + _T("是否现在把现有缓存转移到新位置？\n（选择「否」则仅切换位置，不移动现有文件）"),
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
        d = filedialog.askdirectory(title=_T("选择正文缓存新位置"), initialdir=os.path.dirname(cur))
        if not d:
            return
        if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(cur)):
            messagebox.showinfo(_T("提示"), _T("新位置与当前缓存位置相同，无需转移。"))
            return
        if not messagebox.askyesno(
            "一键转移缓存",
            _T("将正文解析缓存从：\n{cur}\n转移到：\n{d}\n\n转移后立即生效，确定？").format(cur=cur, d=d),
        ):
            return
        self._do_transfer_cache(cur, d, kind="text")
        if path_lbl is not None:
            path_lbl.configure(text=self._effective_text_cache_root())
        if size_lbl is not None:
            self._update_cache_size_label(size_lbl)
    def _clear_cache(self, size_lbl=None):
        if not messagebox.askyesno(_T("清除缓存"), _T("确定清除全部正文解析缓存？\n（下次打开书籍需重新解析，不影响书籍原文件）")):
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
        messagebox.showinfo(_T("已清除"), _T("已清除 {n} 个缓存文件。").format(n=n))
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
            messagebox.showerror(_T("转移失败"), _T("转移缓存时出错：\n{err}").format(err=e))
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
        label = _T("正文解析缓存") if kind == "text" else _T("音频缓存")
        messagebox.showinfo(_T("转移完成"), label + _T("已转移到：\n{path}").format(path=new))
    def _update_tts_cache_size_label(self, lbl):
        try:
            siz = self.tts.tts_cache_size()
            loc = _T("默认位置") if not (self.settings.get("tts_cache_dir") or "") else _T("自定义位置")
            lbl.configure(text=_T("音频缓存总大小：{size}（{loc}）").format(size=self._format_bytes(siz), loc=loc))
        except Exception:
            pass
    def _open_tts_cache_folder(self):
        d = self._effective_tts_cache_root()
        try:
            os.makedirs(d, exist_ok=True)
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror(_T("无法打开"), _T("打开音频缓存文件夹失败：{e}").format(e=e))
    def _choose_tts_cache_folder(self, path_lbl=None, size_lbl=None):
        """自定义音频缓存文件夹：可顺带一键转移。"""
        cur = self._effective_tts_cache_root()
        d = filedialog.askdirectory(
            title=_T("选择音频缓存文件夹（建议放在非 C 盘）"),
            initialdir=os.path.dirname(cur),
        )
        if not d:
            return
        move = messagebox.askyesno(
            _T("转移缓存"),
            _T("已选择新缓存文件夹：\n{d}\n\n").format(d=d) + _T("是否现在把现有音频缓存转移到新位置？\n（选择「否」则仅切换位置，不移动现有文件）"),
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
        d = filedialog.askdirectory(title=_T("选择音频缓存新位置（建议放在非 C 盘）"), initialdir=os.path.dirname(cur))
        if not d:
            return
        if os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(cur)):
            messagebox.showinfo(_T("提示"), _T("新位置与当前缓存位置相同，无需转移。"))
            return
        if not messagebox.askyesno(
            _T("一键转移缓存"),
            _T("将音频缓存从：\n{cur}\n转移到：\n{d}\n\n转移后立即生效，确定？").format(cur=cur, d=d),
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
            _T("清除音频缓存"),
            _T("确定清除全部音频缓存？\n（整本语音缓存文件将被删除，朗读需重新联网合成）"),
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
        messagebox.showinfo(_T("已清除"), _T("已清除 {n} 个音频缓存目录。").format(n=n))
    def _update_tts_cache_label(self):
        """右下角显示 Edge 语音预取缓存进度（如 30/30）；本地语音/未朗读时留空。"""
        try:
            pr = self.tts.prefetch_progress()
        except Exception:
            pr = None
        if pr:
            self.tts_cache_label.configure(text=_T("语音缓存 {a}/{b}").format(a=pr[0], b=pr[1]))
        else:
            self.tts_cache_label.configure(text="")
    def _lan_data_root(self):
        """局域网同步数据的落盘目录：`<正文缓存根>/lan`（与 cache/ 内容同根，可自定义位置）。

        v2.2.9 起：从旧的 <数据目录>/lan 迁到这里，目的是让所有缓存类文件都在
        一个可自定义的目录里，用户删缓存时能一次清干净（其余 library.json /
        stats.json / custom.ico 属用户数据，不参与清理）。
        目录结构：lan/<远端目录>/v1/{books,devices,sources}。
        """
        from .storage import lan_dir
        return lan_dir(self.storage.get_setting("cache_dir") or "")

    def _open_lan_folder(self):
        d = self._lan_data_root()
        try:
            os.makedirs(d, exist_ok=True)
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror(_T("无法打开"), _T("打开局域网同步文件夹失败：{e}").format(e=e))

    def _lan_data_usage(self):
        """返回 (字节数, 文件数)；目录不存在返回 (0, 0)。"""
        root = self._lan_data_root()
        total = 0
        count = 0
        try:
            for base, _dirs, files in os.walk(root):
                for f in files:
                    count += 1
                    try:
                        total += os.path.getsize(os.path.join(base, f))
                    except OSError:
                        pass
        except Exception:
            pass
        return total, count

    def _update_lan_size_label(self, lbl):
        """异步统计局域网同步数据体积（记录 + 书源文件），结果回 Tk 主线程。"""
        def _work():
            size, count = self._lan_data_usage()
            text = _T("占用 {size}（{n} 个文件）").format(size=self._format_bytes(size), n=count)
            try:
                self.root.after(0, lambda: lbl.configure(text=text))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _clear_lan_data(self, size_lbl=None):
        """清除局域网同步数据（记录 + 书源文件）；不影响本机书架与进度。"""
        if not messagebox.askyesno(
                _T("清除同步数据"),
                _T("确定清除局域网同步数据？\n\n"
                   "· 电脑端的书架、阅读进度、书签不受影响\n"
                   "· 手机端独有的书需要重新同步一次才会回到这里")):
            return
        import shutil
        root = self._lan_data_root()
        n = 0
        try:
            if os.path.isdir(root):
                _size, n = self._lan_data_usage()
                shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass
        if size_lbl is not None:
            self._update_lan_size_label(size_lbl)
        messagebox.showinfo(_T("已清除"), _T("已清除 {n} 个缓存文件。").format(n=n))

    def _legacy_lan_pending(self):
        """旧位置（<数据目录>/lan）里待迁移的文件数；没有则 0。"""
        from .storage import legacy_lan_dir
        old = legacy_lan_dir()
        new = self._lan_data_root()
        if not os.path.isdir(old):
            return 0
        if os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(new)):
            return 0
        n = 0
        for _base, _dirs, files in os.walk(old):
            n += len(files)
        return n

    def _migrate_lan_data_click(self, size_lbl=None, path_lbl=None):
        """手动把旧位置 <数据目录>/lan 迁到当前缓存目录（自动迁移失败/换过缓存盘时用）。"""
        from .storage import migrate_legacy_lan_dir
        n = self._legacy_lan_pending()
        if not n:
            messagebox.showinfo(_T("迁移同步数据"),
                                _T("没有需要迁移的旧数据（旧位置不存在或已迁移过）。"))
            return
        if not messagebox.askyesno(
                _T("迁移同步数据"),
                _T("将把旧位置的 {n} 个同步文件迁移到当前缓存目录：\n{d}\n\n"
                   "迁移后旧目录会被删除；如已开启局域网同步会自动重启服务。继续？").format(
                    n=n, d=self._lan_data_root())):
            return
        running = False
        try:
            from .lan_server import ST_RUNNING
            running = self._lan_status().get("state") == ST_RUNNING
        except Exception:
            running = False
        if running:
            self._lan_stop()
        res = migrate_legacy_lan_dir(self.storage.get_setting("cache_dir") or "")
        if running or self.storage.get_setting("lan_enabled", False):
            try:
                self._lan_start(quiet=True)
            except Exception:
                pass
        if path_lbl is not None:
            path_lbl.configure(text=self._lan_data_root())
        if size_lbl is not None:
            self._update_lan_size_label(size_lbl)
        if res.get("moved"):
            messagebox.showinfo(_T("迁移同步数据"),
                                _T("已迁移 {n} 个文件到：\n{d}").format(
                                    n=res.get("files", 0), d=res.get("to") or self._lan_data_root()))
        else:
            messagebox.showwarning(_T("迁移同步数据"),
                                   _T("迁移未完成：{e}").format(e=res.get("reason") or "未知原因"))

    # ---------------- 内部清理（无弹窗，供单个清除与"一键全部清理"复用） ----------------
    def _purge_text_cache(self):
        """删除正文解析缓存文件，返回删除数。"""
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
        return n

    def _purge_tts_cache(self):
        """删除音频缓存目录项（保留大小索引由 invalidate 处理），返回删除数。"""
        import shutil
        root = self._effective_tts_cache_root()
        n = 0
        try:
            for name in os.listdir(root):
                if name.startswith(".tts_sizes"):
                    continue
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
        return n

    def _purge_lan_cache(self):
        """删除局域网同步数据目录，返回删除的文件数。"""
        import shutil
        root = self._lan_data_root()
        n = 0
        try:
            if os.path.isdir(root):
                _size, n = self._lan_data_usage()
                shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass
        return n

    def _clear_all_caches(self, text_lbl=None, tts_lbl=None, lan_lbl=None):
        """一键清理全部缓存：正文解析 + 音频 + 局域网同步数据（一次确认，全部清掉）。"""
        try:
            text_size = dir_size(self._effective_text_cache_root())
        except Exception:
            text_size = 0
        try:
            tts_size = self.tts.tts_cache_size()
        except Exception:
            tts_size = dir_size(self._effective_tts_cache_root())
        lan_size, lan_cnt = self._lan_data_usage()
        total = text_size + tts_size + lan_size
        if not messagebox.askyesno(
                _T("清理全部缓存"),
                _T("将一次性清理全部缓存，合计 {size}：\n\n"
                   "· 正文解析缓存 {a}\n"
                   "· 音频缓存 {b}\n"
                   "· 局域网同步数据 {c}（{n} 个文件）\n\n"
                   "影响：下次打开书籍需重新解析；整本语音缓存需重新下载；"
                   "手机端独有的书需重新同步一次。\n"
                   "书架、阅读进度、书签与设置不受影响。继续？").format(
                    size=self._format_bytes(total),
                    a=self._format_bytes(text_size),
                    b=self._format_bytes(tts_size),
                    c=self._format_bytes(lan_size),
                    n=lan_cnt)):
            return
        n_text = self._purge_text_cache()
        n_tts = self._purge_tts_cache()
        n_lan = self._purge_lan_cache()
        if text_lbl is not None:
            self._update_cache_size_label(text_lbl)
        if tts_lbl is not None:
            self._update_tts_cache_size_label(tts_lbl)
        if lan_lbl is not None:
            self._update_lan_size_label(lan_lbl)
        try:
            self._refresh_bookshelf()
        except Exception:
            pass
        messagebox.showinfo(
            _T("已清除"),
            _T("已清理全部缓存：正文 {a} 个文件 / 音频 {b} 项 / 同步数据 {c} 个文件").format(
                a=n_text, b=n_tts, c=n_lan))

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
            self._flash_status(_T("已继续整本缓存下载"))
