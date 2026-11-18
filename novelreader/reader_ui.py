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
from .speech_word import align_to_display
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

class ReaderMixin:
    """阅读区：书籍加载 / 章节渲染 / 进度 / 滚动 / 高亮跟随 / 分页 / 定时保存"""
    def _load_book(self, path, force_reparse=False):
        """加载书籍：内存缓存 → 磁盘缓存(版本匹配) → 源文件(原路径/备份源) → 旧缓存(静默兜底)。

        源文件/备份缺失时直接继续用缓存阅读，不打断用户；
        依赖源文件的功能（如书架“复制原文件”）在各自入口提示。
        """
        bid = self.storage.book_id(path)
        if not force_reparse:
            if bid in self._cache:
                return self._cache[bid]
            cached = self.storage.read_cache(bid)
            if cached:
                # 缓存版本校验：旧版本缓存可能是坏数据（如段落换行丢失），
                # 版本不符时优先重新解析；解析失败才回退旧缓存
                if int(cached.get("v", 0)) == book_loader.CONTENT_CACHE_VERSION:
                    content = book_loader.BookContent.from_dict(cached)
                    self._cache[bid] = content
                    return content
        # 源文件：原路径不存在时回退到缓存目录里的备份源
        src = self._resolve_source_path(bid, path)
        if src:
            try:
                content = book_loader.parse_book(src)
                self.storage.write_cache(bid, content)
                self._cache[bid] = content
                return content
            except Exception:
                pass
        # 源文件与备份都不可用：静默继续用任意版本缓存（不弹窗，功能不可用处单独提示）
        cached = self.storage.read_cache(bid, any_version=True)
        if cached:
            try:
                content = book_loader.BookContent.from_dict(cached)
                self._cache[bid] = content
                return content
            except Exception:
                pass
        return None

    def _resolve_source_path(self, bid, orig_path):
        """确定可用的源文件路径：原路径优先，其次备份源。"""
        if orig_path and os.path.exists(orig_path):
            return orig_path
        meta = self.storage.get_book(bid) or {}
        bak = meta.get("source_bak", "")
        if bak and os.path.exists(bak):
            return bak
        return None

    def _ensure_source_backup(self, bid, meta):
        """老书迁移：原文件还在且无备份时，自动复制一份到缓存目录。"""
        try:
            if meta.get("source_bak") and os.path.exists(meta["source_bak"]):
                return
            path = meta.get("path", "")
            if path and os.path.exists(path):
                bak = self.storage.backup_source(bid, path)
                if bak:
                    meta["source_bak"] = bak
                    self.storage.save()
        except Exception:
            pass
    def open_book(self, bid):
        meta = self.storage.get_book(bid)
        if not meta:
            return
        self.tts.stop()
        self.current_bid = bid
        # 缓存一级目录用 content_key（跨端稳定）；meta 里没有就按首章现算并回写
        try:
            ckey = meta.get("content_key") or ""
            if not ckey:
                from .content_key import content_key_of_book
                ckey = content_key_of_book(self.storage, meta)
                meta["content_key"] = ckey
                self.storage.save()
        except Exception:
            ckey = ""
        self.tts.set_book_id(bid, ckey)
        self.book = self._load_book(meta.get("path", ""))
        if self.book is None:
            # 打开失败：状态栏提示并进入空状态
            try:
                self._flash_status(f"无法读取书籍文件：{os.path.basename(meta.get('path', ''))}")
            except Exception:
                pass
            self._render_empty()
            return
        # 老书迁移：原文件还在且无备份 → 自动复制备份
        self._ensure_source_backup(bid, meta)
        # 旧缓存布局懒迁移（<root>/<旧book_id>/<语音>/<200> → <content_key>/<语音>/<pct>）
        try:
            self.tts.ensure_book_cache(bid, self.book)
        except Exception:
            pass
        prog = meta.get("progress") or {}
        self.chapter_idx = max(0, min(int(prog.get("chapter_idx", 0)), len(self.book.chapters) - 1))
        self.char_offset = int(prog.get("char_offset", 0))
        self.char_offset = max(0, min(self.char_offset, len(self.book.chapters[self.chapter_idx].content)))
        # 记录本阅读器所持位置的版本时间：之后同步若拉来更新的进度，
        # _save_now 会先采纳它，而不是用这里的旧位置把它盖回去
        self._own_progress_at = float(meta.get("progress_at") or meta.get("last_read_at") or 0)
        self.root.title(f"{self.book.title} - " + _T("多多朗读 v{ver}").format(ver=__version__))
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
        title_text = ch.title.strip() if ch.title else _T("第 {n} 章").format(n=self.chapter_idx + 1)
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
    def _reprocess_current_book(self):
        """右键菜单：按最新分章器重新解析整本（后台线程+进度窗）。"""
        if not self.current_bid or not self.book:
            return
        info = self.storage.get_book(self.current_bid)
        if not info or not info.get("path"):
            messagebox.showinfo(_T("提示"), _T("找不到当前书籍路径"))
            return
        self._reprocess_book(info["path"], self.current_bid)

    def _render_empty(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.title_label.configure(text=_T("未打开书籍"))
        self.percent_label.configure(text="0.0%")
        self.pos_label.configure(text="")
        self.total_label.configure(text="")
        self.tts_cache_label.configure(text="")
        self.progress_var.set(0)
        self.root.title(_T("多多朗读 v{ver}").format(ver=__version__))
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
        self.pos_label.configure(text=_T("第 {n} 章 / 共 {total} 章").format(n=self.chapter_idx + 1, total=len(self.book.chapters)))
        self.total_label.configure(text=_T("总字数 {n:,}").format(n=self.book.total_chars))
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
            messagebox.showinfo(_T("提示"), _T("请先从书架打开一本书"))
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
            self._start_tts(self.book, self.chapter_idx, off)
            self._set_tts_ui("playing")
        self._flash_status(_T("已从该段开始朗读"))
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
        # 同步守卫：若存储里的进度版本比"本阅读器手里的位置"更新（别的设备/同步写进来的），
        # 说明本次保存的是过期位置——先采纳存储里的进度，绝不把它盖回去。
        try:
            stored = self.storage.get_book(self.current_bid) or {}
            stored_at = float(stored.get("progress_at") or 0)
            own_at = float(getattr(self, "_own_progress_at", 0) or 0)
            if stored_at > own_at + 0.5:
                self.adopt_remote_progress(self.current_bid, stored.get("progress") or {},
                                           stored_at, quiet=True)
                return
        except Exception:
            pass
        pct = self._compute_percent(self.chapter_idx, self.char_offset)
        # 位置 + 上次书 合并成一次落盘（原来分两次 = 两次全量 JSON 重写）
        self.storage.save_reading_state(
            self.current_bid,
            {
                "chapter_idx": self.chapter_idx,
                "char_offset": self.char_offset,
                "percent": round(pct, 3),
            },
            self.current_bid,
        )
        # 只更新书架里本书那一行，不再整表重建（重建会让主线程多花 50~60ms）
        self._update_shelf_progress(self.current_bid, pct)
        try:
            self._own_progress_at = float((self.storage.get_book(self.current_bid) or {}).get("progress_at") or 0)
        except Exception:
            pass
        # 阅读统计：按位置前进量累加字数（手动阅读与朗读都走这里）
        try:
            self._stats_note_pos(self.current_bid, self.chapter_idx, self.char_offset)
        except Exception:
            pass
    def adopt_remote_progress(self, bid, progress, progress_at=0.0, quiet=False):
        """把同步/远端拉到的进度接到正在阅读的这本书上。

        只移动阅读位置并重绘，不落盘（落盘会用自己的旧位置覆盖新进度）。
        """
        if not self.book or bid != self.current_bid:
            return False
        if not isinstance(progress, dict) or not progress:
            return False
        try:
            ci = max(0, min(int(progress.get("chapter_idx", 0)), len(self.book.chapters) - 1))
            off = int(progress.get("char_offset", 0) or 0)
            off = max(0, min(off, len(self.book.chapters[ci].content)))
        except Exception:
            return False
        self.chapter_idx = ci
        self.char_offset = off
        if not progress_at:
            try:
                progress_at = float((self.storage.get_book(bid) or {}).get("progress_at") or 0)
            except Exception:
                progress_at = 0.0
        self._own_progress_at = float(progress_at or 0)
        try:
            self._render_chapter()
        except Exception:
            pass
        if not quiet:
            self._flash_status(_T("已同步到其他设备的阅读位置：第 {n} 章").format(n=ci + 1))
        return True

    def _goto_chapter(self, ci, offset=0):
        if not self.book:
            return
        ci = max(0, min(ci, len(self.book.chapters) - 1))
        was_active = self.tts.is_active()
        if was_active:
            self.tts.stop()  # 结束旧会话，避免旧章节状态冲突
        self._jump_reading = False
        timer = getattr(self, "_jump_timer", None)
        if timer:
            try:
                self.root.after_cancel(timer)
            except Exception:
                pass
            self._jump_timer = None
        self.chapter_idx = ci
        self.char_offset = offset
        self._render_chapter()
        self._schedule_save()
        if was_active:
            # 换章后继续朗读：从新章节当前位置接着读
            self._start_tts(self.book, ci, offset)
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
        # 唯一权威：事件必须属于当前会话令牌。跳句 / 停止会推进令牌，旧会话
        # 回吐的开始/结束/停止事件天然作废——不需要 pending 窗口，也不需要
        # 拿位置或时间去猜"这条事件是不是旧的"。
        gen = evt.get("gen")
        if gen is not None:
            try:
                if int(gen) != int(self.tts.current_gen):
                    return
            except Exception:
                pass
        if t == "sentence_start":
            ci, off, text = evt["chapter_idx"], evt["char_offset"], evt["text"]
            if ci == self.chapter_idx and off < self.char_offset:
                # 位置只前进、不回拽：点按跳句时上一会话要等合成完才回吐 sentence_start，
                # 若照单全收，它会把用户刚选的位置拉回旧句，看起来就是"按键没反应/卡住"。
                return
            if ci != self.chapter_idx or self.book is None:
                self.chapter_idx = ci
                self.char_offset = off
                self._render_chapter()
            self._highlight_sentence(ci, off, text)
            lyr = getattr(self, "_lyric", None)
            if lyr is not None:
                try:
                    title = self.book.chapters[ci].title if self.book else ""
                    lyr.show_sentence(title, text)
                except Exception:
                    pass
        elif t == "sentence_done":
            ci, off = evt["chapter_idx"], evt["char_offset"]
            if ci == self.chapter_idx and off < self.char_offset:
                return
            self.chapter_idx = ci
            self.char_offset = off
            self._update_status()
            self._schedule_save()
        elif t == "finished":
            self._set_tts_ui("stopped")
            self._clear_highlight()
            lyr = getattr(self, "_lyric", None)
            if lyr is not None:
                try:
                    lyr.clear()
                except Exception:
                    pass
            if self.book:
                self.char_offset = len(self.book.chapters[self.chapter_idx].content)
                self._update_status()
                self._save_now()
        elif t == "stopped":
            # 旧会话的 stopped 已在令牌过滤处丢弃；能走到这里的停止事件属于当前会话，
            # 就是用户真的停止朗读（或章节结束）。
            if not self.tts.is_active():
                self._set_tts_ui("stopped")
                self._clear_highlight()
                lyr = getattr(self, "_lyric", None)
                if lyr is not None:
                    try:
                        lyr.clear()
                    except Exception:
                        pass
        elif t == "error":
            # 非阻塞提示。注意：Edge 联网失败会回退本地语音并继续朗读，
            # 此时不能把按钮重置为“开始朗读”，真正的结束由 “stopped” 事件统一处理。
            self._flash_status(evt.get("message", _T("朗读出错")))
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
        # 起点可能正好落在换行/空白上（段首缩进、空行模式），先跳过前导空白再取窗口，
        # 否则窗口只剩一个换行符，句子色只染一个字符、逐词对齐也全失败。
        anchor = start
        try:
            limit_walk = 0
            while limit_walk < 8:
                ch = self.text.get(anchor, f"{anchor}+1c")
                if not ch or not ch.isspace():
                    break
                nxt = self.text.index(f"{anchor}+1c")
                if self.text.compare(nxt, ">=", "end-1c"):
                    break
                anchor = nxt
                limit_walk += 1
        except Exception:
            anchor = start
        # 结束位置：直接从 anchor 在显示文本中向后读取，遇到句末标点即停。
        # （显示文本保留标点/空白，纯净朗读文本去除了它们，长度不一致，
        #   不能直接用 len(text) 定位结束。）
        end = anchor
        try:
            # 窗口长度用 len(text)+40 估算（显示文本多出标点/空白），并钳到正文末位，
            # 否则章末最后几句取窗会越界抛 TclError，整句落不上色。
            limit = len(text) + 40
            probe_end = self.text.index(f"{anchor}+{limit}c")
            if self.text.compare(probe_end, ">", "end-1c"):
                probe_end = "end-1c"
            window = self.text.get(anchor, probe_end)
            found = False
            for i, ch in enumerate(window):
                if ch in "。！？.!?；;\n":
                    end = self.text.index(f"{anchor}+{i+1}c")
                    found = True
                    break
            if not found and self.text.compare(probe_end, ">", anchor):
                end = probe_end
        except Exception:
            end = anchor
        if self.text.compare(end, "<=", anchor):
            # 兜底：窗口算不出有效范围时退回原始起点，至少句子色能落上
            anchor = start
            try:
                end = self.text.index(f"{start}+1c")
            except Exception:
                end = start
        self.text.tag_remove("tts", "1.0", "end")
        self.text.tag_add("tts", anchor, end)
        self._highlight_index = (ci, anchor)
        # 逐词高亮上下文：句子在显示文本里的起点 + 该句显示文本 + 朗读（清洗）文本
        disp = ""
        try:
            disp = self.text.get(anchor, end)
        except Exception:
            disp = ""
        self._word_ctx = {"ci": ci, "start": anchor, "clean": text or "",
                          "display": disp, "map": None}
        self._start_word_tick()
        self._pin_highlight_top(anchor)
    def _pin_highlight_top(self, start):
        """把高亮所在显示行钉到窗口第一行（兼容所有空行模式）。

        统一用「先 see 保证可见 → dlineinfo 测该行像素偏移 → yview fraction 换算
        滚动量」，把高亮所在显示行钉到窗口第一行。
        """
        try:
            self.text.see(start)
            self.text.update_idletasks()
            dline = self.text.dlineinfo(start)
            if not dline:
                return
            pady = 0
            try:
                pady = int(float(self.text.cget("pady")))
            except Exception:
                pady = 0
            delta = dline[1] - pady  # 该显示行距视口顶部还需滚动的像素
            if abs(delta) < 2:
                return
            first, last = self.text.yview()
            span = last - first
            viewport_px = self.text.winfo_height()
            if span <= 0 or viewport_px <= 0:
                return
            total_px = viewport_px / span  # 全文像素总高度
            new_first = max(0.0, min(1.0, first + delta / total_px))
            self.text.yview_moveto(new_first)
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
        self._stop_word_tick()
        self._clear_word_tag()
        try:
            self.text.tag_remove("tts", "1.0", "end")
        except Exception:
            pass

    # ---------------- 逐词高亮（仅 Edge 语音有词级时间轴） ----------------
    def _start_word_tick(self):
        """开始按播放位置刷新当前词（60ms 一次，只读 pygame 位置，开销极小）。"""
        try:
            if not self.settings.get("word_highlight", True):
                return
        except Exception:
            return
        self._stop_word_tick()
        try:
            self._word_after = self.root.after(60, self._word_tick)
        except Exception:
            self._word_after = None

    def _stop_word_tick(self):
        if getattr(self, "_word_after", None):
            try:
                self.root.after_cancel(self._word_after)
            except Exception:
                pass
            self._word_after = None

    def _clear_word_tag(self):
        try:
            self.text.tag_remove("tts_word", "1.0", "end")
        except Exception:
            pass

    def _word_map(self):
        """朗读文本下标 → 显示文本下标的映射（按句缓存，避免每 tick 重算）。"""
        ctx = getattr(self, "_word_ctx", None)
        if not ctx:
            return None
        if ctx.get("map") is None:
            try:
                ctx["map"] = align_to_display(ctx.get("clean") or "", ctx.get("display") or "")
            except Exception:
                ctx["map"] = []
        return ctx.get("map")

    def _word_tick(self):
        """定时把"当前正在读的词"叠色到句子高亮之上。"""
        self._word_after = None
        try:
            if not self.book or not self.tts.is_playing():
                self._clear_word_tag()
                return
            if not self.settings.get("word_highlight", True):
                self._clear_word_tag()
                return
            ctx = getattr(self, "_word_ctx", None)
            word = None
            try:
                word = self.tts.current_word()
            except Exception:
                word = None
            if word is not None and ctx and ctx.get("ci") == self.chapter_idx:
                mapping = self._word_map() or []
                s_i = word.text_start
                e_i = min(word.text_end, len(mapping))
                if 0 <= s_i < len(mapping) and mapping[s_i] >= 0:
                    ds = mapping[s_i]
                    if 0 < e_i <= len(mapping) and mapping[e_i - 1] >= 0:
                        de = mapping[e_i - 1] + 1
                    else:
                        de = ds + 1
                    if de > ds:
                        self._clear_word_tag()
                        start = ctx["start"]
                        self.text.tag_add("tts_word",
                                          "%s+%dc" % (start, ds), "%s+%dc" % (start, de))
            self._word_after = self.root.after(60, self._word_tick)
        except Exception:
            self._word_after = None

    def _toggle_word_highlight(self):
        """设置面板：逐词高亮 开 / 关（即时生效）。"""
        cur = True
        try:
            cur = bool(self.settings.get("word_highlight", True))
        except Exception:
            pass
        new = not cur
        self.settings["word_highlight"] = new
        try:
            self.storage.set_setting("word_highlight", new)
        except Exception:
            pass
        if new:
            self._start_word_tick()
            self._flash_status(_T("逐词高亮已开启"))
        else:
            self._stop_word_tick()
            self._clear_word_tag()
            self._flash_status(_T("逐词高亮已关闭"))
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
