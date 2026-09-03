# -*- coding: utf-8 -*-
"""书签 + 划线高亮 + 备注笔记：阅读区右键标记、目录/书签面板切换、书签高亮渲染、跳转与删除（独立 Mixin，不写入 gui.py）。"""
import time
import tkinter as tk
from tkinter import messagebox, ttk


class BookmarkMixin:
    """书签：划线高亮 + 备注笔记；与目录共用同一面板，顶部“目录 / 书签”按钮切换。"""

    # ---------- 选区映射：渲染文本 index → 原文字符偏移 ----------
    def _idx_to_raw_offset(self, idx):
        """把渲染文本中的 index 映射回该章节原文字符偏移（用于书签记录定位）。

        空行模式会改变显示行号，须按模式反算：模式3 用非换行列计数，
        模式1/2 用行号映射到段落起点再加段内列偏移。
        """
        try:
            line = int(str(idx).split(".")[0])
            col = int(str(idx).split(".")[1])
        except Exception:
            return 0
        if not self.book:
            return 0
        try:
            body_start = getattr(self, "_body_start_line", 1)
            if line < body_start:
                return 0  # 位于章节标题区
            mode = int(self.settings.get("paragraph_mode", 1))
            if mode == 3:
                return self._display_col_to_raw_offset(col)
            raw_off = self._display_line_to_offset(line)
            return max(0, raw_off + col)
        except Exception:
            return 0

    # ---------- 新增书签（右键菜单） ----------
    def _add_bookmark_from_selection(self):
        """阅读区右键：把选中文字存为书签（划线高亮），并弹出备注输入框（默认文本可直接确认）。"""
        if not self.book or not self.current_bid:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            messagebox.showinfo("提示", "请先在阅读区选中要标记的文字，再右键选择『添加书签/划线』")
            return
        try:
            start, end = sel[0], sel[1]
            text_sel = self.text.get(start, end).strip()
        except Exception:
            return
        if not text_sel:
            messagebox.showinfo("提示", "选中的内容为空")
            return
        off = self._idx_to_raw_offset(start)
        off_e = self._idx_to_raw_offset(end)
        content = self.book.chapters[self.chapter_idx].content
        off = max(0, min(off, len(content)))
        off_e = max(off, min(off_e, len(content)))
        if off_e <= off:
            off_e = off + len(text_sel)

        # 备注输入框：默认文本 = 选中文字前 30 字，用户可直接点“确定”
        dlg = tk.Toplevel(self.root)
        dlg.title("添加书签")
        dlg.geometry("460x240")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._center_window(dlg)
        dlg.configure(bg=self._dialog_bg())
        tk.Label(dlg, text="书签备注（默认取选中文字，可直接确认）：", bg=self._dialog_bg(),
                 font=("微软雅黑", 10)).pack(anchor="w", padx=16, pady=(14, 6))
        default_note = (text_sel[:30] + "…") if len(text_sel) > 30 else text_sel
        var = tk.StringVar(value=default_note)
        ent = tk.Entry(dlg, textvariable=var, font=("微软雅黑", 10))
        ent.pack(fill="x", padx=16)
        ent.select_range(0, "end")
        ent.focus_set()
        preview = text_sel[:80] + ("…" if len(text_sel) > 80 else "")
        tk.Label(dlg, text=f"选中内容：{preview}", bg=self._dialog_bg(), fg="#888888",
                 font=("微软雅黑", 9), wraplength=420, justify="left").pack(anchor="w", padx=16, pady=(8, 0))

        def do_save(event=None):
            note = var.get().strip() or default_note
            bm = {
                "chapter_idx": self.chapter_idx,
                "offset": off,
                "offset_end": off_e,
                "text": text_sel,
                "note": note,
            }
            self.storage.add_bookmark(self.current_bid, bm)
            try:
                dlg.destroy()
            except Exception:
                pass
            self._apply_bookmark_tags()
            self._fill_bookmark_panel(keep=True)
            self._flash_status("已添加书签/划线")
            try:
                self.text.tag_remove("sel", "1.0", "end")
            except Exception:
                pass

        ops = tk.Frame(dlg, bg=self._dialog_bg())
        ops.pack(pady=12)
        tk.Button(ops, text="确定", width=10, command=do_save).pack(side="left", padx=8)
        tk.Button(ops, text="取消", width=10, command=dlg.destroy).pack(side="left", padx=8)
        dlg.bind("<Return>", do_save)

    def _dialog_bg(self):
        from .constants import UI_THEMES
        try:
            return UI_THEMES.get(self.settings.get("ui_theme", "A·米黄暖读"), UI_THEMES["A·米黄暖读"])["bg"]
        except Exception:
            return "#FAF3E0"

    # ---------- 渲染书签高亮（章节渲染后调用） ----------
    def _apply_bookmark_tags(self):
        """把当前章节的书签位置用高亮 tag 标出（浅绿，区别于朗读高亮的黄色）。"""
        try:
            self.text.tag_remove("bm", "1.0", "end")
            if not self.book or not self.current_bid:
                return
            bms = self.storage.get_bookmarks(self.current_bid)
            content = self.book.chapters[self.chapter_idx].content
            for bm in bms:
                if bm.get("chapter_idx") != self.chapter_idx:
                    continue
                try:
                    off = max(0, min(int(bm.get("offset", 0)), len(content)))
                    off_e = max(off, min(int(bm.get("offset_end", off)), len(content)))
                    if off_e <= off:
                        off_e = off + 1
                    s_line, s_col = self._transformed_pos(content, off)
                    e_line, e_col = self._transformed_pos(content, off_e)
                    self.text.tag_add("bm", f"{s_line}.{s_col}", f"{e_line}.{e_col}")
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- 目录 / 书签 面板切换 ----------
    def _set_panel_mode(self, mode):
        """切换目录面板内容：'toc' 章节列表 / 'bookmark' 书签列表。"""
        try:
            self._panel_mode = mode
            if mode == "bookmark":
                self._panel_toc_btn.configure(relief="raised")
                self._panel_bm_btn.configure(relief="sunken")
                self._fill_bookmark_panel()
            else:
                self._panel_toc_btn.configure(relief="sunken")
                self._panel_bm_btn.configure(relief="raised")
                self._populate_chapters()
        except Exception:
            pass

    def _fill_bookmark_panel(self, keep=False):
        """填充书签列表；keep=True 表示保存后刷新当前选择。"""
        try:
            self.chapter_list.delete(0, "end")
            if not self.book or not self.current_bid:
                self.chapter_list.insert("end", "（无书签）")
                return
            bms = self.storage.get_bookmarks(self.current_bid)
            if not bms:
                self.chapter_list.insert("end", "（暂无书签，选中文字后右键添加）")
                return
            self._bookmark_meta = bms
            for i, bm in enumerate(bms):
                ci = int(bm.get("chapter_idx", 0))
                note = (bm.get("note") or "").replace("\n", " ")
                if len(note) > 26:
                    note = note[:26] + "…"
                title = ""
                try:
                    title = self.book.chapters[ci].title.strip() if 0 <= ci < len(self.book.chapters) else ""
                except Exception:
                    pass
                self.chapter_list.insert("end", f"〔{title or ('第' + str(ci + 1) + '章')}〕 {note}")
        except Exception:
            pass

    def _on_panel_select(self, event):
        """目录面板选中分发：目录模式→切章；书签模式→跳书签。"""
        try:
            if getattr(self, "_panel_mode", "toc") == "bookmark":
                self._on_bookmark_pick(event)
            else:
                sel = self.chapter_list.curselection()
                if sel:
                    self._goto_chapter(sel[0])
        except Exception:
            pass

    def _on_bookmark_pick(self, event):
        """点击书签条目：跳转到对应章节位置，并把书签高亮钉到顶部。"""
        try:
            sel = self.chapter_list.curselection()
            if not sel:
                return
            bms = self.storage.get_bookmarks(self.current_bid)
            if sel[0] >= len(bms):
                return
            bm = bms[sel[0]]
            ci = max(0, min(int(bm.get("chapter_idx", 0)), len(self.book.chapters) - 1))
            off = int(bm.get("offset", 0))
            self._goto_chapter(ci, off)
            self._flash_status("已跳转到书签位置")
            # 书签高亮钉到窗口第一行
            try:
                bms2 = self.storage.get_bookmarks(self.current_bid)
                content = self.book.chapters[self.chapter_idx].content
                for b in bms2:
                    if b.get("chapter_idx") == ci and b.get("offset") == off:
                        line, col = self._transformed_pos(content, off)
                        self._pin_highlight_top(f"{line}.{col}")
                        break
            except Exception:
                pass
        except Exception:
            pass

    def _popup_bookmark_menu(self, event):
        """书签列表右键：删除书签。"""
        try:
            if getattr(self, "_panel_mode", "toc") != "bookmark":
                return
            iid = self.chapter_list.nearest(event.y)
            if iid < 0:
                return
            bms = self.storage.get_bookmarks(self.current_bid)
            if iid >= len(bms):
                return
            self.chapter_list.selection_clear(0, "end")
            self.chapter_list.selection_set(iid)
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="删除该书签", command=lambda: self._remove_bookmark_at(iid))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        except Exception:
            pass

    def _remove_bookmark_at(self, idx):
        try:
            bms = self.storage.get_bookmarks(self.current_bid)
            if idx < len(bms):
                bm = bms[idx]
                self.storage.remove_bookmark(self.current_bid, bm.get("id"))
                self._fill_bookmark_panel()
                self._apply_bookmark_tags()
                self._flash_status("已删除书签")
        except Exception:
            pass
