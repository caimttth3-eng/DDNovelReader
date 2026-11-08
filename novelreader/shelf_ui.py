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

class ShelfMixin:
    """书架界面：列表 / 排序 / 多选 / 右键 / 复制 / 删除音频缓存 / 书架开合"""
    def _refresh_bookshelf(self):
        self._init_shelf_size_cache()
        self._shelf_drag_reset()
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
                "title": b.get("title", _T("未命名")),
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
        arrows = {"progress": _T("进度"), "title": _T("书名"), "size": _T("大小"), "time": _T("时间")}
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
                                    values=("", _T("（书架为空，点击『添加书籍』导入）"), "", ""))
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

    # ---------- 书架鼠标框选（按住左键拖动连续多选） ----------
    def _shelf_rows(self):
        """书架里的书籍行（排除空书架占位行）。"""
        try:
            return [iid for iid in self.shelf_tree.get_children() if iid != "__empty__"]
        except Exception:
            return []

    def _shelf_row_index_at(self, x, y):
        """返回坐标所在的行序号；空白区域按最近的边界行归属，无书时返回 None。"""
        rows = self._shelf_rows()
        if not rows:
            return None
        try:
            if self.shelf_tree.identify_region(x, y) in ("heading", "separator"):
                return None
        except Exception:
            pass
        iid = ""
        try:
            iid = self.shelf_tree.identify_row(y)
        except Exception:
            pass
        if iid:
            try:
                return rows.index(iid)
            except ValueError:
                return None
        # 空白处（末行之下 / 行间缝隙）：归到最近的上一行或末行之后
        for idx, rid in enumerate(rows):
            try:
                bb = self.shelf_tree.bbox(rid)
            except Exception:
                bb = None
            if not bb:
                continue
            if y < bb[1] + bb[3] / 2:
                return idx
        return len(rows)

    def _shelf_drag_reset(self):
        self._shelf_drag_anchor = None
        self._shelf_drag_active = False
        self._shelf_drag_base = set()
        self._shelf_drag_append = False

    def _shelf_drag_begin(self, event):
        """按下左键：记录拖拽起点（不拦截事件，单击仍走 Treeview 默认选中）。"""
        self._shelf_drag_reset()
        try:
            region = self.shelf_tree.identify_region(event.x, event.y)
        except Exception:
            region = ""
        if region in ("heading", "separator"):
            return
        idx = self._shelf_row_index_at(event.x, event.y)
        if idx is None:
            return
        self._shelf_drag_anchor = idx
        self._shelf_drag_start_y = event.y
        self._shelf_drag_start_x = event.x
        # Ctrl / Shift 拖动 = 在原选择上追加，否则重新框选（macOS 用 Command）
        state = getattr(event, "state", 0)
        mod = bool((state & 0x0004) or (state & 0x0001))
        if sys.platform == "darwin" and (state & 0x0008):
            mod = True
        self._shelf_drag_append = mod
        self._shelf_drag_base = set(self._shelf_rows()) & set(self.shelf_tree.selection())

    def _shelf_drag_motion(self, event):
        """拖动中：把起点到当前行之间的书全部选中，边界处自动滚动。"""
        anchor = getattr(self, "_shelf_drag_anchor", None)
        if anchor is None:
            return
        if not getattr(self, "_shelf_drag_active", False):
            dx = abs(event.x - getattr(self, "_shelf_drag_start_x", event.x))
            dy = abs(event.y - getattr(self, "_shelf_drag_start_y", event.y))
            if dx < 4 and dy < 4:
                return
            self._shelf_drag_active = True
        try:
            h = self.shelf_tree.winfo_height()
            if event.y < 14:
                self.shelf_tree.yview_scroll(-1, "units")
            elif event.y > h - 14:
                self.shelf_tree.yview_scroll(1, "units")
        except Exception:
            pass
        idx = self._shelf_row_index_at(event.x, event.y)
        rows = self._shelf_rows()
        if idx is None or not rows:
            return "break"
        lo, hi = sorted((anchor, idx))
        lo = max(0, lo)
        hi = min(len(rows) - 1, hi)
        range_ids = rows[lo:hi + 1] if hi >= lo else []
        base = getattr(self, "_shelf_drag_base", set()) if getattr(self, "_shelf_drag_append", False) else set()
        chosen = set(range_ids) | base
        final = [rid for rid in rows if rid in chosen]
        try:
            if tuple(final) != tuple(self.shelf_tree.selection()):
                self.shelf_tree.selection_set(tuple(final))
                focus_iid = rows[min(max(idx, 0), len(rows) - 1)]
                self.shelf_tree.focus(focus_iid)
        except Exception:
            pass
        return "break"

    def _shelf_drag_end(self, event=None):
        self._shelf_drag_reset()

    def _shelf_select_all(self, event=None):
        """Ctrl+A 全选书架。"""
        rows = self._shelf_rows()
        if rows:
            try:
                self.shelf_tree.selection_set(tuple(rows))
                self.shelf_tree.focus(rows[-1])
                self.shelf_tree.see(rows[-1])
            except Exception:
                pass
        return "break"

    def _open_shelf_row(self, event):
        """双击按鼠标所在行打开，避免多选后打开第一本。"""
        try:
            iid = self.shelf_tree.identify_row(event.y)
        except Exception:
            iid = ""
        if iid and iid != "__empty__":
            self.open_book(iid)

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
        self.shelf_menu.entryconfigure(_T("打开书籍"), state="disabled" if multi else "normal")
        # 单选时：复制原文件 仅当源文件（原路径/备份源）可用，否则置灰
        src_ok = False
        if not multi:
            sbid = sel[0] if sel else None
            smeta = self.storage.get_book(sbid) if sbid else None
            if smeta:
                sp = smeta.get("path", "")
                sbak = smeta.get("source_bak", "")
                src_ok = (sp and os.path.exists(sp)) or (sbak and os.path.exists(sbak))
        self.shelf_menu.entryconfigure(_T("复制原文件"), state="disabled" if (multi or not src_ok) else "normal")
        self.shelf_menu.entryconfigure(_T("复制书名"), state="disabled" if multi else "normal")
        # 多选时删除菜单显示数量，明确是批量操作
        try:
            n = len(sel)
            if multi:
                self.shelf_menu.entryconfigure(
                    self._shelf_del_keep_idx,
                    label=_T("删除选中的 {n} 本（保留缓存）").format(n=n))
                self.shelf_menu.entryconfigure(
                    self._shelf_del_clear_idx,
                    label=_T("删除选中的 {n} 本文件（清空缓存）").format(n=n))
            else:
                self.shelf_menu.entryconfigure(self._shelf_del_keep_idx, label=_T("删除（保留缓存）"))
                self.shelf_menu.entryconfigure(self._shelf_del_clear_idx, label=_T("删除文件（清空缓存）"))
        except Exception:
            pass
        try:
            self._mv_refresh_menu_state()
        except Exception:
            pass
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
            # 原文件被删/移动 → 回退到软件缓存的源文件备份
            bak = meta.get("source_bak", "")
            if bak and os.path.exists(bak):
                path = bak
            else:
                messagebox.showinfo(_T("提示"), _T("原文件不存在或已被移动。"))
                return
        if _copy_files_to_clipboard([path]):
            messagebox.showinfo(_T("已复制"), _T("原文件已复制到剪贴板，可在文件管理器中直接粘贴（Ctrl+V）。"))
        else:
            messagebox.showerror(_T("失败"), _T("复制到剪贴板失败。"))
    def _copy_book_title(self):
        bid = self._selected_bid()
        if not bid:
            return
        meta = self.storage.get_book(bid)
        if not meta:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(meta.get("title", ""))
        messagebox.showinfo(_T("已复制"), _T("已复制书名：{title}").format(title=meta.get("title", "")))
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
            messagebox.showinfo(_T("提示"), _T("暂无数据可备份"))
            return
        default_name = f"多多朗读备份_{time.strftime('%Y%m%d_%H%M%S')}.json"
        try:
            dest = filedialog.asksaveasfilename(
                defaultextension=".json",
                initialfile=default_name,
                filetypes=[(_T("JSON 备份文件"), "*.json")],
            )
        except Exception:
            return
        if not dest:
            return
        try:
            import shutil
            shutil.copyfile(src, dest)
            messagebox.showinfo(_T("备份成功"),
                                _T("已备份书架、阅读进度、设置与书签到：\n{dest}\n\n提示：缓存体积较大，不包含在备份内。").format(dest=dest))
        except Exception as e:
            messagebox.showerror(_T("备份失败"), _T("备份失败：{e}").format(e=e))

    def _restore_library(self):
        """一键还原：从备份文件恢复书架 + 进度 + 设置 + 书签。

        还原前先自动备份当前数据（.pre_restore），失败不影响原数据。
        """
        try:
            src = filedialog.askopenfilename(
                title=_T("选择备份文件"),
                filetypes=[(_T("JSON 备份文件"), "*.json")],
            )
        except Exception:
            return
        if not src:
            return
        if not messagebox.askyesno(_T("确认还原"), _T("还原将覆盖当前的书架、阅读进度、设置与书签，是否继续？")):
            return
        try:
            import json
            import shutil
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "books" not in data:
                raise ValueError(_T("备份文件格式不正确（缺少 books 字段）"))
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
            msg = _T("已从备份还原书架、阅读进度、设置与书签。")
            if cur_bak:
                msg += "\n\n" + _T("还原前数据已自动备份到：\n{path}").format(path=cur_bak)
            messagebox.showinfo(_T("还原成功"), msg)
        except Exception as e:
            messagebox.showerror(_T("还原失败"), _T("还原失败：{e}\n原数据未受影响。").format(e=e))
