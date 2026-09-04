# -*- coding: utf-8 -*-
"""全文搜索：书内关键词搜索 + 结果列表 + 点击跳转（独立 Mixin，不写入 gui.py）。"""
import tkinter as tk
from tkinter import messagebox, ttk
from .constants import make_scrollbar


class SearchMixin:
    """全文搜索：在当前书全部章节内搜索关键词，结果列表点击即跳转并定位。"""

    def _open_search_dialog(self):
        """功能区“搜索”按钮：弹出书内全文搜索窗口（初始位置屏幕中间）。"""
        if not self.book:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"全文搜索 - {self.book.title}")
        dlg.geometry("520x460")
        dlg.minsize(420, 320)
        dlg.transient(self.root)
        self._center_window(dlg)
        dlg.configure(bg=self._dialog_bg())

        top_bar = tk.Frame(dlg, bg=self._dialog_bg())
        top_bar.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(top_bar, text="关键词：", bg=self._dialog_bg(),
                 font=("微软雅黑", 10)).pack(side="left")
        qvar = tk.StringVar()
        ent = tk.Entry(top_bar, textvariable=qvar, font=("微软雅黑", 10))
        ent.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ent.focus_set()

        res_lbl = tk.Label(dlg, text="输入关键词后点击搜索（搜索前 500 条结果）", bg=self._dialog_bg(),
                           fg="#888888", font=("微软雅黑", 9))
        res_lbl.pack(anchor="w", padx=14, pady=(0, 4))

        list_frame = tk.Frame(dlg)
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        lb = tk.Listbox(list_frame, font=("微软雅黑", 10), exportselection=False)
        sb = make_scrollbar(list_frame, lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)

        self._search_results = []

        def do_search(event=None):
            q = qvar.get().strip()
            if not q:
                return
            lb.delete(0, "end")
            self._search_results = []
            count = 0
            truncated = False
            try:
                for ci, ch in enumerate(self.book.chapters):
                    content = ch.content
                    pos = 0
                    while True:
                        i = content.find(q, pos)
                        if i < 0:
                            break
                        self._search_results.append((ci, i, q))
                        count += 1
                        pos = i + len(q)
                        if count >= 500:
                            truncated = True
                            break
                    if truncated:
                        break
            except Exception:
                pass
            if not self._search_results:
                res_lbl.configure(text=f"未找到“{q}”")
                return
            # 填充结果列表：章节号 + 上下文片段
            for k, (ci, i, _) in enumerate(self._search_results):
                try:
                    content = self.book.chapters[ci].content
                    title = self.book.chapters[ci].title.strip() or f"第 {ci + 1} 章"
                    lo = max(0, i - 16)
                    hi = min(len(content), i + len(q) + 24)
                    snippet = content[lo:hi].replace("\n", " ")
                    if len(snippet) > 42:
                        snippet = snippet[:42] + "…"
                    lb.insert("end", f"{k + 1}. 〔{title}〕 …{snippet}…")
                except Exception:
                    lb.insert("end", f"{k + 1}. 第 {ci + 1} 章")
            res_lbl.configure(text=f"找到 {count} 处{('，仅显示前 500 条' if truncated else '')}：双击结果跳转")

        def on_pick(event):
            sel = lb.curselection()
            if not sel or sel[0] >= len(self._search_results):
                return
            ci, off, q = self._search_results[sel[0]]
            try:
                dlg.destroy()
            except Exception:
                pass
            self._goto_chapter(ci, off)
            self._flash_status(f"已定位到“{q}”")
            # 可选：把关键词高亮一下
            try:
                content = self.book.chapters[self.chapter_idx].content
                line, col = self._transformed_pos(content, off)
                self._pin_highlight_top(f"{line}.{col}")
            except Exception:
                pass

        lb.bind("<Double-Button-1>", on_pick)
        lb.bind("<Return>", on_pick)
        dlg.bind("<Return>", do_search)

        ops = tk.Frame(dlg, bg=self._dialog_bg())
        ops.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(ops, text="搜索", command=do_search).pack(side="left")
        ttk.Button(ops, text="关闭", command=dlg.destroy).pack(side="right")
