# -*- coding: utf-8 -*-
"""书架界面：列表 / 排序 / 多选 / 右键 / 复制 / 删除音频缓存 / 书架开合（从 gui.py 拆分的 Mixin 之一）。"""
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

class ShelfMixin:
    """书架界面：列表 / 排序 / 多选 / 右键 / 复制 / 删除音频缓存 / 书架开合"""
    def _refresh_bookshelf(self):
        self._init_shelf_size_cache()
        # 保存当前选中
        sel_bids = set()
        try:
            for iid in self.shelf_tree.selection():
                sel_bids.add(iid)
        except Exception:
            pass
        self.shelf_tree.delete(*self.shelf_tree.get_children())
        books = self.storage.all_books()
        rows = []
        for b in books.values():
            prog = b.get("progress") or {}
            pct = prog.get("percent", 0)
            # 优先内存实时值，其次持久化值；缺失（老数据）显示…，由后台一次性校准
            bid_i = b["id"]
            csize = self._shelf_size_cache.get(bid_i)
            if csize is None:
                csize = self.storage.book_cache_size(bid_i)
            if csize is None:
                csize = 0
                size_s = "…"
            else:
                size_s = self._format_bytes(csize) if csize else "-"
            ts = b.get("added_at", b.get("last_read_at", 0))
            try:
                time_s = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "-"
            except Exception:
                time_s = "-"
            rows.append({
                "id": b["id"],
                "progress": pct,
                "title": b.get("title", "未命名"),
                "size": csize,
                "size_s": size_s,
                "time": ts,
                "time_s": time_s,
            })
        # 排序
        col = getattr(self, "_shelf_sort_col", "time")
        rev = getattr(self, "_shelf_sort_rev", True)
        key_map = {"progress": "progress", "title": "title", "size": "size", "time": "time"}
        rows.sort(key=lambda r: r.get(key_map.get(col, "time"), 0), reverse=rev)
        # 更新表头箭头
        arrows = {"progress": "进度", "title": "书名", "size": "大小", "time": "时间"}
        for c, label in arrows.items():
            txt = label
            if c == col:
                txt = ("▼ " if rev else "▲ ") + label
            self.shelf_tree.heading(c, text=txt)
        # 插入
        for r in rows:
            prog_s = f"{r['progress']:.0f}%"
            iid = r["id"]
            self.shelf_tree.insert("", "end", iid=iid,
                                    values=(prog_s, r["title"], r["size_s"], r["time_s"]))
        # 恢复选中
        for bid in sel_bids:
            try:
                self.shelf_tree.selection_add(bid)
            except Exception:
                pass
        if not rows:
            self.shelf_tree.insert("", "end", iid="__empty__",
                                    values=("", "（书架为空，点击『添加书籍』导入）", "", ""))
        # 后台异步计算缺失的缓存大小
        self._refresh_shelf_sizes_async()
    def _shelf_sort(self, col):
        if getattr(self, "_shelf_sort_col", "") == col:
            self._shelf_sort_rev = not getattr(self, "_shelf_sort_rev", True)
        else:
            self._shelf_sort_col = col
            self._shelf_sort_rev = (col == "time")  # 时间默认倒序
        self._refresh_bookshelf()
    def _selected_bid(self):
        try:
            sel = self.shelf_tree.selection()
        except Exception:
            return None
        if not sel:
            return None
        # 多选时返回第一个
        return sel[0] if sel[0] != "__empty__" else None
    def _selected_bids(self):
        """返回所有选中的书籍 id 列表（多选支持）。"""
        try:
            sel = list(self.shelf_tree.selection())
        except Exception:
            return []
        return [s for s in sel if s != "__empty__"]
    def _popup_shelf_menu(self, event):
        # 右键点击的行
        iid = self.shelf_tree.identify_row(event.y)
        if not iid or iid == "__empty__":
            return
        sel = self._selected_bids()
        multi = len(sel) > 1
        # 如果右键的行不在选中中，则只选中该行
        if iid not in self.shelf_tree.selection():
            self.shelf_tree.selection_set(iid)
            multi = False
        # 多选时：打开书籍、复制原文件、复制书名 变灰
        self.shelf_menu.entryconfigure("打开书籍", state="disabled" if multi else "normal")
        self.shelf_menu.entryconfigure("复制原文件", state="disabled" if multi else "normal")
        self.shelf_menu.entryconfigure("复制书名", state="disabled" if multi else "normal")
        try:
            self.shelf_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.shelf_menu.grab_release()
    def _copy_book_file(self):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        path = meta.get("path", "")
        if not path or not os.path.exists(path):
            messagebox.showinfo("提示", "原文件不存在或已被移动。")
            return
        if _copy_files_to_clipboard([path]):
            messagebox.showinfo("已复制", "原文件已复制到剪贴板，可在文件管理器中直接粘贴（Ctrl+V）。")
        else:
            messagebox.showerror("失败", "复制到剪贴板失败。")
    def _copy_book_title(self):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(meta.get("title", ""))
        messagebox.showinfo("已复制", f"已复制书名：{meta.get('title', '')}")
    def _delete_audio_cache(self, bid):
        """删除某本书的全部音频缓存目录。"""
        try:
            root = self._effective_tts_cache_root()
            import shutil
            for d in audio_cache_dirs(root, bid):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.storage.set_book_cache_size(bid, 0)
            self._shelf_size_cache[bid] = 0
            self.tts.tts_cache_invalidate(bid)
        except Exception:
            pass
    def _toggle_shelf(self):
        try:
            pane_paths = [str(p) for p in self._inner_paned.panes()]
        except Exception:
            pane_paths = []
        if str(self._left) in pane_paths:
            self._inner_paned.remove(self._left)
            self._shelf_visible = False
        else:
            self._inner_paned.insert(0, self._left, weight=0)
            self._shelf_visible = True

    # ---------- 书架备份 / 还原（一键导出 / 导入） ----------
    def _backup_library(self):
        """一键备份：把书架 + 进度 + 设置 + 书签（library.json）导出到用户选择的文件。"""
        src = self.storage.path
        if not os.path.exists(src):
            messagebox.showinfo("提示", "暂无数据可备份")
            return
        default_name = f"多多朗读备份_{time.strftime('%Y%m%d_%H%M%S')}.json"
        try:
            dest = filedialog.asksaveasfilename(
                defaultextension=".json",
                initialfile=default_name,
                filetypes=[("JSON 备份文件", "*.json")],
            )
        except Exception:
            return
        if not dest:
            return
        try:
            import shutil
            shutil.copyfile(src, dest)
            messagebox.showinfo("备份成功",
                                f"已备份书架、阅读进度、设置与书签到：\n{dest}\n\n提示：缓存体积较大，不包含在备份内。")
        except Exception as e:
            messagebox.showerror("备份失败", f"备份失败：{e}")

    def _restore_library(self):
        """一键还原：从备份文件恢复书架 + 进度 + 设置 + 书签。

        还原前先自动备份当前数据（.pre_restore），失败不影响原数据。
        """
        try:
            src = filedialog.askopenfilename(
                title="选择备份文件",
                filetypes=[("JSON 备份文件", "*.json")],
            )
        except Exception:
            return
        if not src:
            return
        if not messagebox.askyesno("确认还原", "还原将覆盖当前的书架、阅读进度、设置与书签，是否继续？"):
            return
        try:
            import json
            import shutil
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "books" not in data:
                raise ValueError("备份文件格式不正确（缺少 books 字段）")
            cur_bak = self.storage.path + ".pre_restore"
            try:
                if os.path.exists(self.storage.path):
                    shutil.copyfile(self.storage.path, cur_bak)
            except Exception:
                cur_bak = None
            from .storage import DEFAULT_SETTINGS
            new_settings = dict(DEFAULT_SETTINGS)
            new_settings.update(data.get("settings", {}) or {})
            self.storage.data = {"books": data.get("books", {}), "settings": new_settings}
            self.storage.save()
            self.settings = dict(new_settings)
            try:
                self._apply_settings_to_ui()
            except Exception:
                pass
            try:
                self._apply_ui_theme(new_settings.get("ui_theme", "A·米黄暖读"))
            except Exception:
                pass
            self._refresh_bookshelf()
            self._apply_bookmark_tags()
            last = new_settings.get("last_book")
            if last:
                try:
                    self.open_book(last)
                except Exception:
                    pass
            msg = "已从备份还原书架、阅读进度、设置与书签。"
            if cur_bak:
                msg += f"\n\n还原前数据已自动备份到：\n{cur_bak}"
            messagebox.showinfo("还原成功", msg)
        except Exception as e:
            messagebox.showerror("还原失败", f"还原失败：{e}\n原数据未受影响。")
