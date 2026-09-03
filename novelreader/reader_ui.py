# -*- coding: utf-8 -*-
"""阅读区：书籍加载 / 章节渲染 / 进度 / 滚动 / 高亮跟随 / 分页 / 定时保存（从 gui.py 拆分的 Mixin 之一）。"""
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

class ReaderMixin:
    """阅读区：书籍加载 / 章节渲染 / 进度 / 滚动 / 高亮跟随 / 分页 / 定时保存"""
    def _load_book(self, path, force_reparse=False):
        bid = self.storage.book_id(path)
        if not force_reparse:
            if bid in self._cache:
                return self._cache[bid]
            cached = self.storage.read_cache(bid)
            if cached:
                # 缓存版本校验：旧版本缓存可能是坏数据（如段落换行丢失），
                # 版本不符时强制重新解析，避免“从该段朗读/高亮跟随”等依赖段落的功能失效
                if int(cached.get("v", 0)) == book_loader.CONTENT_CACHE_VERSION:
                    content = book_loader.BookContent.from_dict(cached)
                    self._cache[bid] = content
                    return content
        content = book_loader.parse_book(path)
        self.storage.write_cache(bid, content)
        self._cache[bid] = content
        return content
    def open_book(self, bid):
        meta = self.storage.get_book(bid)
        if not meta:
            return
        self.tts.stop()
        self.current_bid = bid
        self.tts.set_book_id(bid)
        try:
            self.book = self._load_book(meta["path"])
        except Exception:
            cached = self.storage.read_cache(bid)
            if cached:
                self.book = book_loader.BookContent.from_dict(cached)
            else:
                # 打开失败不弹模态框（避免启动/切书时卡死主线程），状态栏提示并进入空状态
                try:
                    self._flash_status(f"无法读取书籍文件：{os.path.basename(meta['path'])}")
                except Exception:
                    pass
                self._render_empty()
                return
        prog = meta.get("progress") or {}
        self.chapter_idx = max(0, min(int(prog.get("chapter_idx", 0)), len(self.book.chapters) - 1))
        self.char_offset = int(prog.get("char_offset", 0))
        self.char_offset = max(0, min(self.char_offset, len(self.book.chapters[self.chapter_idx].content)))
        self.root.title(f"{self.book.title} - 多多朗读 v{__version__}")
        self.title_label.configure(text=self.book.title)
        self._populate_chapters()
        self._render_chapter()
        self._refresh_bookshelf()
    def _populate_chapters(self):
        titles = [c.title for c in self.book.chapters]
        self._chapter_titles_cache = titles
        self.chapter_cb["values"] = titles
        self.chapter_cb.set(titles[self.chapter_idx])
        self.chapter_list.delete(0, "end")
        for i, t in enumerate(titles):
            self.chapter_list.insert("end", t)
        self.chapter_list.see(self.chapter_idx)
        self.chapter_list.selection_clear(0, "end")
        self.chapter_list.selection_set(self.chapter_idx)
    def _sync_chapter_sel(self):
        """切章时轻量同步目录选中态/章节下拉框，不重建整个列表（大目录下避免卡顿）。"""
        try:
            titles = getattr(self, "_chapter_titles_cache", None)
            if titles is None or len(titles) != len(self.book.chapters):
                titles = [c.title for c in self.book.chapters]
                self._chapter_titles_cache = titles
            if 0 <= self.chapter_idx < len(titles):
                self.chapter_cb.set(titles[self.chapter_idx])
            self.chapter_list.selection_clear(0, "end")
            self.chapter_list.selection_set(self.chapter_idx)
            self.chapter_list.see(self.chapter_idx)
        except Exception:
            pass
    def _render_chapter(self):
        if not self.book:
            return
        ch = self.book.chapters[self.chapter_idx]
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        # 插入章节标题（用户翻章时能明显看到当前章节）
        title_text = ch.title.strip() if ch.title else f"第 {self.chapter_idx + 1} 章"
        self.text.insert("1.0", title_text + "\n\n", "chapter_title")
        # 记录标题偏移量（标题+两个换行）
        self._title_char_len = len(title_text) + 2
        # 正文起始行号：注意 Tk 的 index("end") 在文本以 \n\n 结尾时会多返回 1 行
        # （隐含末尾空行），而正文实际插入后落在 end-1c 所在行，故用 end-1c。
        self._body_start_line = int(self.text.index("end-1c").split(".")[0])
        # 插入正文
        self.text.insert("end", self._apply_paragraph_mode(ch.content), "body")
        self.text.configure(state="disabled")
        self._apply_font()
        self._tag_body()
        self._scroll_to_offset(self.char_offset)
        # 轻量同步目录选中态/下拉框（不重建整个列表，避免大目录每次重建）
        self._sync_chapter_sel()
        self._update_status()
        self._repin_reading()
        # 渲染本书书签高亮（划线标注）
        self._apply_bookmark_tags()
    def _render_empty(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.title_label.configure(text="未打开书籍")
        self.percent_label.configure(text="0.0%")
        self.pos_label.configure(text="")
        self.total_label.configure(text="")
        self.tts_cache_label.configure(text="")
        self.progress_var.set(0)
        self.root.title(f"多多朗读 v{__version__}")
    def _compute_percent(self, ci, off):
        if not self.book or self.book.total_chars <= 0:
            return 0.0
        cum = self.book.cum[ci] + off
        return min(100.0, max(0.0, cum / self.book.total_chars * 100.0))
    def _update_status(self):
        if not self.book:
            return
        pct = self._compute_percent(self.chapter_idx, self.char_offset)
        self.progress_var.set(pct)
        self.percent_label.configure(text=f"{pct:.1f}%")
        self.pos_label.configure(text=f"第 {self.chapter_idx + 1} 章 / 共 {len(self.book.chapters)} 章")
        self.total_label.configure(text=f"总字数 {self.book.total_chars:,}")
    def _on_seek_press(self, event=None):
        self._seeking = True
        self._pending_seek_pct = None
    def _on_seek_release(self, event=None):
        self._seeking = False
        # 松手后一次性跳转；拖动过程中不渲染，避免每动一格都渲染整章导致卡顿
        p = getattr(self, "_pending_seek_pct", None)
        if p is not None:
            self._pending_seek_pct = None
            self._seek_to_percent(p)
    def _on_seek(self, value):
        # 仅响应用户拖动；程序自动更新进度时不触发跳转
        if not self.book or not self._seeking:
            return
        self._pending_seek_pct = float(value)
        # 拖动中实时刷新百分比，便于用户精准定位目标进度
        try:
            self.percent_label.configure(text=f"{float(value):.1f}%")
        except Exception:
            pass
    def _seek_to_percent(self, pct):
        import bisect
        target = max(0.0, min(100.0, pct)) / 100.0 * self.book.total_chars
        cum = self.book.cum
        ci = bisect.bisect_right(cum, target) - 1
        ci = max(0, min(ci, len(self.book.chapters) - 1))
        off = int(target - self.book.cum[ci])
        off = max(0, min(off, len(self.book.chapters[ci].content)))
        was = self._seeking
        self._seeking = False
        try:
            self._goto_chapter(ci, off)
        finally:
            self._seeking = was
    def _on_scroll(self, event=None):
        if not self.book:
            return
        self.char_offset = self._current_offset()
        self._schedule_status_update()
        self._schedule_save()
    def _schedule_status_update(self):
        """滚动高频时合并状态栏刷新（进度百分比/章节位置），避免每帧重复配置标签。"""
        t = getattr(self, "_status_scroll_timer", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
        self._status_scroll_timer = self.root.after(50, self._flush_status_update)
    def _flush_status_update(self):
        self._status_scroll_timer = None
        try:
            self._update_status()
        except Exception:
            pass
    def _on_text_resize(self, event=None):
        """窗口缩放/移动/初始化触发视口变化：朗读中把高亮重新钉到顶部。

        必须直接执行而不能用防抖合并：窗口持续调整时 <Configure> 事件狂发，
        若用 after(120ms) 防抖会被反复取消，_repin_reading() 永远不触发，
        导致朗读高亮在窗口变化后跳出视口、无法跟随。
        """
        try:
            if self.book and self.tts.is_playing():
                self._repin_reading()
            self._on_scroll(event)
        except Exception:
            pass
    def _current_offset(self):
        try:
            top = self.text.index("@0,0")
            cnt = self.text.count("1.0", top, "chars")
            raw = cnt[0] if cnt else 0
            # 减去章节标题的字符数，得到正文中的偏移
            return max(0, raw - self._title_char_len)
        except Exception:
            return 0
    def _display_line_to_offset(self, line):
        """把渲染文本的显示行号映射回原文字符偏移（该段落起点的原文偏移）。

        空行模式会改变显示行号：模式1 原始第 L 行→显示第 L 行；
        模式2 原始第 L 行→显示第 2L-1 行（偶数为空行）；模式3 全文仅 1 行。
        """
        content = self.book.chapters[self.chapter_idx].content
        mode = int(self.settings.get("paragraph_mode", 1))
        line = max(1, int(line))
        # 减去章节标题占的行数，得到正文中的行号
        line = max(1, line - self._body_start_line + 1)
        if mode == 3:
            return 0
        if mode == 2:
            orig_line = (line + 1) // 2
        else:
            orig_line = line
        if orig_line <= 1:
            return 0
        idx = -1
        for _ in range(orig_line - 1):
            idx = content.find("\n", idx + 1)
            if idx < 0:
                return len(content)
        return idx + 1
    def _read_from_paragraph(self):
        """阅读区右键：从光标所在段落起点开始朗读。"""
        if not self.book:
            messagebox.showinfo("提示", "请先从书架打开一本书")
            return
        idx = getattr(self, "_ctx_index", None)
        if not idx:
            return
        try:
            line = int(idx.split(".")[0])
            col = int(idx.split(".")[1])
        except Exception:
            return
        mode = int(self.settings.get("paragraph_mode", 1))
        content = self.book.chapters[self.chapter_idx].content
        if mode == 3:
            # 清理所有行模式：全文只有一行，用点击处的列号映射回原文偏移
            raw_off = self._display_col_to_raw_offset(col)
        elif mode == 2:
            # 合并为一行模式：段间有空行，向上找到段落起始显示行（空行之前的一行）
            start_line = line
            try:
                while start_line > 1:
                    prev = self.text.get(f"{start_line - 1}.0", f"{start_line - 1}.end")
                    if not prev.strip():
                        break
                    start_line -= 1
            except Exception:
                pass
            raw_off = self._display_line_to_offset(start_line)
        else:
            # 不压缩模式：每段一行无空行，直接定位点击行对应的段落起点
            raw_off = self._display_line_to_offset(line)
        if "\n" in content:
            # 正常分段书：从该段起点朗读
            if mode == 3:
                nl = content.rfind("\n", 0, raw_off)
                off = (nl + 1) if nl >= 0 else 0
            else:
                off = raw_off
        else:
            # 无换行符的书（空格分段等）：“段落”无意义，改为从本句起点开始朗读
            off = self._sentence_start(raw_off)
        self._goto_chapter(self.chapter_idx, off)  # 渲染/保存；正在朗读则自动继续
        if not self.tts.is_active():
            self.tts.start(self.book, self.chapter_idx, off)
            self._set_tts_ui("playing")
        self._flash_status("已从该段开始朗读")
    def _display_col_to_raw_offset(self, col):
        """模式3：显示列号（删除换行后）→ 原文偏移（不回退段）。

        col 是 0-based 索引（前面有 col 个非换行字符），因此第 col 个
        非换行字符即 count == col 时（count 也从 0 计）。
        """
        content = self.book.chapters[self.chapter_idx].content
        col = max(0, int(col))
        if col == 0:
            return 0
        count = 0
        for i, ch in enumerate(content):
            if ch == "\n":
                continue
            if count == col:
                return i
            count += 1
        return len(content)
    def _sentence_start(self, off):
        """往回找到 off 所在句子的起点（上一个句末标点之后）。"""
        content = self.book.chapters[self.chapter_idx].content
        off = max(0, min(off, len(content)))
        i = off
        while i > 0:
            i -= 1
            if content[i] in "。！？!?…；;":
                return i + 1
        return 0
    def _scroll_to_offset(self, offset):
        if not self.book:
            return
        content = self.book.chapters[self.chapter_idx].content
        line, col = self._transformed_pos(content, offset)
        self.text.see(f"{line}.{col}")
    def _schedule_save(self):
        if self._save_timer:
            self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(700, self._save_now)
    def _save_now(self):
        self._save_timer = None
        if not self.current_bid or not self.book:
            return
        pct = self._compute_percent(self.chapter_idx, self.char_offset)
        self.storage.update_progress(
            self.current_bid,
            {
                "chapter_idx": self.chapter_idx,
                "char_offset": self.char_offset,
                "percent": round(pct, 3),
            },
        )
        self.storage.set_setting("last_book", self.current_bid)
        self._refresh_bookshelf()
    def _goto_chapter(self, ci, offset=0):
        if not self.book:
            return
        ci = max(0, min(ci, len(self.book.chapters) - 1))
        was_active = self.tts.is_active()
        if was_active:
            self.tts.stop()  # 结束旧会话，避免旧章节状态冲突
        self.chapter_idx = ci
        self.char_offset = offset
        self._render_chapter()
        self._schedule_save()
        if was_active:
            # 换章后继续朗读：从新章节当前位置接着读
            self.tts.start(self.book, ci, offset)
            self._set_tts_ui("playing")
    def _on_chapter_cb(self, event):
        try:
            idx = self.chapter_cb.current()
        except Exception:
            return
        if idx >= 0:
            self._goto_chapter(idx)
    def _on_chapter_pick(self, event):
        sel = self.chapter_list.curselection()
        if not sel:
            return
        self._goto_chapter(sel[0])
    def _toggle_toc(self):
        try:
            pane_paths = [str(p) for p in self._inner_paned.panes()]
        except Exception:
            pane_paths = []
        if str(self.chapter_panel) in pane_paths:
            self._inner_paned.remove(self.chapter_panel)
            self._chapter_panel_visible = False
        else:
            self._inner_paned.add(self.chapter_panel, weight=0)
            self._chapter_panel_visible = True
    def _handle_tts_event(self, evt):
        t = evt["type"]
        if t == "sentence_start":
            ci, off, text = evt["chapter_idx"], evt["char_offset"], evt["text"]
            if ci != self.chapter_idx or self.book is None:
                self.chapter_idx = ci
                self.char_offset = off
                self._render_chapter()
            self._highlight_sentence(ci, off, text)
        elif t == "sentence_done":
            ci, off = evt["chapter_idx"], evt["char_offset"]
            self.chapter_idx = ci
            self.char_offset = off
            self._update_status()
            self._schedule_save()
        elif t == "finished":
            self._set_tts_ui("stopped")
            self._clear_highlight()
            if self.book:
                self.char_offset = len(self.book.chapters[self.chapter_idx].content)
                self._update_status()
                self._save_now()
        elif t == "stopped":
            if not self.tts.is_active():
                self._set_tts_ui("stopped")
                self._clear_highlight()
        elif t == "error":
            # 非阻塞提示。注意：Edge 联网失败会回退本地语音并继续朗读，
            # 此时不能把按钮重置为“开始朗读”，真正的结束由 “stopped” 事件统一处理。
            self._flash_status(evt.get("message", "朗读出错"))
    def _transformed_pos(self, content, off):
        """把原始正文的字符偏移映射到渲染文本（压缩空行后）中的 (行, 列)。

        空行模式会改变换行数量，导致按原文计算的 行/列 在渲染文本中错位；
        朗读高亮、滚动定位都必须经过此映射才能落在正确位置。
        """
        before = content[:off]
        nl_before = before.count("\n")
        mode = int(self.settings.get("paragraph_mode", 1))
        if mode == 3:
            # 清理所有行：所有换行被删除，正文只有第 1 行；加上标题占的行数
            return self._body_start_line, off - nl_before
        line = nl_before + 1
        last_nl = before.rfind("\n")
        col = off - (last_nl + 1) if last_nl >= 0 else off
        if mode == 2:
            # 合并为一行：每个段落间多一个空行，原始第 L 段 → 渲染第 2L-1 行
            line = line * 2 - 1
        # 加上章节标题占的行数，得到显示文本中的行号
        return line + self._body_start_line - 1, col
    def _highlight_sentence(self, ci, off, text):
        content = self.book.chapters[ci].content
        line, col = self._transformed_pos(content, off)
        start = f"{line}.{col}"
        # 结束位置：直接从 start 在显示文本中向后读取，遇到句末标点即停。
        # （显示文本保留标点/空白，纯净朗读文本去除了它们，长度不一致，
        #   不能直接用 len(text) 定位结束。）
        end = start
        try:
            total_len = len(text) + 30  # 显示文本可能多出标点，留足余量
            line_end = self.text.index(f"{start} lineend")
            window_end = self.text.index(f"{start}+{total_len}c")
            # 取 start 到（行尾与 start+len 的较小者）之间的字符
            probe_end = self.text.index(f"{start} lineend")
            probe_end = self.text.index(f"{start}+{total_len}c") \
                if self.text.compare(f"{start}+{total_len}c", "<", line_end) \
                else line_end
            window = self.text.get(start, probe_end)
            for i, ch in enumerate(window):
                if ch in "。！？.!?；;\n":
                    end = self.text.index(f"{start}+{i+1}c")
                    break
        except Exception:
            end = start
        self.text.tag_remove("tts", "1.0", "end")
        self.text.tag_add("tts", start, end)
        self._highlight_index = (ci, start)
        self._pin_highlight_top(start)
    def _pin_highlight_top(self, start):
        """把高亮所在显示行钉到窗口第一行（兼容所有空行模式）。

        模式1/2（每段独占一行）直接用 Tk 原生 yview(index) 整行滚动，天然精确；
        模式3（清理所有行）全文只有一行，按行号滚动永远停在文首、高亮无法跟随，
        改用「先 see 保证可见 → dlineinfo 测该行像素偏移 → yview fraction 换算
        滚动量」，把高亮所在显示行钉到窗口第一行。
        """
        try:
            mode = int(self.settings.get("paragraph_mode", 1))
            if mode != 3:
                top = self.text.index(f"{start} linestart")
                self.text.yview(top)
                return
            self.text.see(start)
            dline = self.text.dlineinfo(start)
            if not dline:
                return
            pady = 0
            try:
                pady = int(float(self.text.cget("pady")))
            except Exception:
                pady = 0
            delta = dline[1] - pady  # 该显示行距视口顶部还需上滚的像素
            if delta <= 0:
                return
            first, last = self.text.yview()
            span = last - first
            viewport_px = self.text.winfo_height()
            if span <= 0 or viewport_px <= 0:
                return
            total_px = viewport_px / span  # 全文像素总高度
            self.text.yview_moveto(max(0.0, min(1.0, first + delta / total_px)))
        except Exception:
            try:
                self.text.see(start)
            except Exception:
                pass
    def _repin_reading(self):
        """朗读中窗口/字号变化后，重新把当前高亮钉回窗口第一行。"""
        if not self.book or not self.tts.is_playing():
            return
        hi = getattr(self, "_highlight_index", None)
        if hi and hi[0] == self.chapter_idx and hi[1]:
            self._pin_highlight_top(hi[1])
    def _clear_highlight(self):
        self._highlight_index = None
        self._resize_timer = None
        self._pending_seek_pct = None
        self._status_scroll_timer = None
        try:
            self.text.tag_remove("tts", "1.0", "end")
        except Exception:
            pass
    def _shortcut_read_from_paragraph(self):
        """快捷键：从当前屏顶部所在段落起点开始朗读。"""
        if not self.book:
            return
        try:
            idx = self.text.index("@0,0")
            self._ctx_index = idx
        except Exception:
            return
        self._read_from_paragraph()
