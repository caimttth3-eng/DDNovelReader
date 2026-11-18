# -*- coding: utf-8 -*-
"""阅读统计入口：设置面板「阅读统计」+ 统计弹窗（今日 / 本周 / 累计）。

口径见 novelreader/stats.py：朗读时长（播放中秒数）、软件开启时长、阅读字数、
连续天数与本周周报。数据只存本机 `stats.json`，不参与 WebDAV 同步。
"""
import time
import tkinter as tk
from tkinter import ttk

from .i18n import T as _T
from .constants import UI_THEMES
from .stats import StatsStore, split_duration, split_chars

SPEAK_TICK_MS = 5000        # 朗读心跳：5 秒一次
APP_TICK_MS = 60000         # 开启心跳：60 秒一次


def fmt_duration(seconds):
    """秒 → 「3 小时 20 分」；不足 1 分钟给「0 分钟」。"""
    h, m = split_duration(seconds)
    if h and m:
        return "%d %s %d %s" % (h, _T("小时"), m, _T("分钟"))
    if h:
        return "%d %s" % (h, _T("小时"))
    return "%d %s" % (m, _T("分钟"))


def fmt_chars(n):
    """字数 → 「1234 字」/「1.2 万字」。"""
    w, rest = split_chars(n)
    if not w:
        return "%d %s" % (int(rest), _T("字"))
    if rest:
        return "%.1f %s" % (w + rest / 10000.0, _T("万字"))
    return "%d %s" % (w, _T("万字"))


WEEK_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class StatsMixin:
    """统计心跳（朗读 / 开启 / 字数）+ 设置面板入口。"""

    # ---------- 生命周期 ----------
    def _stats_init(self):
        """主窗口构建完成后调用：建库、挂两个心跳、记录起始位置。"""
        try:
            self.stats = StatsStore()
        except Exception:
            self.stats = None
            return
        self._stats_pos = None       # (bid, chapter_idx, char_offset)
        self._stats_speak_id = None
        self._stats_app_id = None
        # 心跳按真实时间差累加（不是固定 5/60 秒），退出时不足一个周期的尾段也能补上
        self._stats_speak_ref = time.time()
        self._stats_app_ref = time.time()
        try:
            self._stats_speak_id = self.root.after(SPEAK_TICK_MS, self._stats_speak_tick)
            self._stats_app_id = self.root.after(APP_TICK_MS, self._stats_app_tick)
        except Exception:
            pass

    def _stats_speak_tick(self):
        """播放中才计入朗读时长（暂停/停止不计）。"""
        now = time.time()
        dt = now - float(getattr(self, "_stats_speak_ref", now))
        self._stats_speak_ref = now
        try:
            if self.stats is not None and 0 < dt <= 60 and self.tts.is_playing():
                self.stats.add("speak", dt)
                self.stats.flush(force=False)
        except Exception:
            pass
        try:
            self._stats_speak_id = self.root.after(SPEAK_TICK_MS, self._stats_speak_tick)
        except Exception:
            self._stats_speak_id = None

    def _stats_app_tick(self):
        """软件开启时长：不区分前台/后台，窗口活着就算。"""
        now = time.time()
        dt = now - float(getattr(self, "_stats_app_ref", now))
        self._stats_app_ref = now
        try:
            if self.stats is not None and 0 < dt <= 600:
                self.stats.add("app", dt)
                self.stats.flush(force=False)
        except Exception:
            pass
        try:
            self._stats_app_id = self.root.after(APP_TICK_MS, self._stats_app_tick)
        except Exception:
            self._stats_app_id = None

    def _stats_close(self):
        """退出前收尾：停心跳 + 补最后一段 + 强制落盘。"""
        for attr in ("_stats_speak_id", "_stats_app_id"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    self.root.after_cancel(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            if self.stats is None:
                return
            now = time.time()
            # 补开启尾段（最长一个心跳周期）
            dt_app = now - float(getattr(self, "_stats_app_ref", now))
            if 0 < dt_app <= 600:
                self.stats.add("app", dt_app)
            # 补朗读尾段（只在还处于播放状态时）
            dt_speak = now - float(getattr(self, "_stats_speak_ref", now))
            try:
                playing = self.tts.is_playing()
            except Exception:
                playing = False
            if playing and 0 < dt_speak <= 600:
                self.stats.add("speak", dt_speak)
            self.stats.flush(force=True)
        except Exception:
            pass

    # ---------- 字数 ----------
    def _stats_note_pos(self, bid=None, ci=None, off=None):
        """记录阅读位置前进量，累加为字数。

        - 同章：直接取偏移差；
        - 跨一章：本句剩余 + 新章已读；
        - 跨多章（跳章/拖进度）不计，避免把"跳过去"算成读过；
        - 单次超过 2 万字也丢弃（多半是按百分比跳转）。
        """
        if self.stats is None or not self.book:
            return
        bid = bid if bid is not None else self.current_bid
        ci = self.chapter_idx if ci is None else ci
        off = self.char_offset if off is None else off
        if not bid or ci is None or off is None:
            return
        prev = getattr(self, "_stats_pos", None)
        self._stats_pos = (bid, ci, off)
        if not prev or prev[0] != bid:
            return
        pci, poff = prev[1], prev[2]
        try:
            if ci == pci:
                delta = off - poff
            elif ci == pci + 1:
                delta = (len(self.book.chapters[pci].content) - poff) + off
            else:
                return
        except Exception:
            return
        if delta <= 0 or delta > 20000:
            return
        try:
            self.stats.add("chars", delta)
            self.stats.add_book_chars(bid, delta)
            self.stats.flush(force=False)
        except Exception:
            pass

    # ---------- 入口 ----------
    def _show_stats_dialog(self):
        _StatsDlg(self)


class _StatsDlg:
    """阅读统计弹窗：4 张今日卡片 + 本周柱状图 + 累计摘要。"""

    def __init__(self, app):
        self.app = app
        self._bg = UI_THEMES.get(
            app.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        self._card_bg = "#FFFFFF"
        self._border = "#DDE3EC"
        self._main_fg = "#1A1B1C"
        self._sub_fg = "#7A7F87"
        self._accent = "#4C7BD9"
        self._accent_light = "#C9D8F2"

        self.top = tk.Toplevel(app.root)
        self.top.title(_T("阅读统计"))
        self.top.geometry("860x792")
        self.top.minsize(800, 740)
        self.top.transient(app.root)
        app._center_window(self.top)
        self.top.configure(bg=self._bg)

        self._build()
        self.refresh()
        # 记一份引用，便于重复打开时置顶同一窗口 / 外部刷新
        try:
            app._stats_dlg = self
            self.top.bind("<Destroy>", lambda _e: setattr(app, "_stats_dlg", None))
        except Exception:
            pass
        self.top.focus_set()

    # ---------- 构建 ----------
    def _build(self):
        pad = {"padx": 20}
        head = tk.Frame(self.top, bg=self._bg)
        head.pack(fill="x", **pad, pady=(16, 6))
        tk.Label(head, text=_T("阅读统计"), font=("微软雅黑", 13, "bold"),
                 bg=self._bg, fg=self._main_fg).pack(anchor="w")
        tk.Label(
            head,
            text=_T("朗读时长 / 阅读字数 / 软件开启时长 / 连续天数，数据只存本机，不参与数据同步"),
            font=("微软雅黑", 9), fg=self._sub_fg, bg=self._bg,
            wraplength=800, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # 今日 4 张卡片
        cards = tk.Frame(self.top, bg=self._bg)
        cards.pack(fill="x", **pad, pady=(12, 4))
        self._card_values = {}
        for i, key in enumerate(("speak", "chars", "app", "streak")):
            cards.columnconfigure(i, weight=1)
            card = tk.Frame(cards, bg=self._card_bg, highlightbackground=self._border,
                            highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            val = tk.Label(card, text="—", font=("微软雅黑", 16, "bold"),
                           bg=self._card_bg, fg=self._main_fg)
            val.pack(anchor="w", padx=14, pady=(12, 2))
            ttl = tk.Label(card, text="", font=("微软雅黑", 9),
                           bg=self._card_bg, fg=self._sub_fg)
            ttl.pack(anchor="w", padx=14, pady=(0, 12))
            self._card_values[key] = (val, ttl)

        # 本周柱状图
        chart_box = tk.Frame(self.top, bg=self._card_bg, highlightbackground=self._border,
                             highlightthickness=1)
        chart_box.pack(fill="x", **pad, pady=(12, 6))
        tk.Label(chart_box, text=_T("本周朗读时长"), font=("微软雅黑", 10, "bold"),
                 bg=self._card_bg, fg=self._main_fg).pack(anchor="w", padx=14, pady=(10, 0))
        self.canvas = tk.Canvas(chart_box, bg=self._card_bg, height=196,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", padx=10, pady=(2, 10))

        # 累计摘要
        sum_box = tk.Frame(self.top, bg=self._bg)
        sum_box.pack(fill="x", **pad, pady=(2, 6))
        for i in range(3):
            sum_box.columnconfigure(i, weight=1)
        self._sum_labels = {}
        items = (
            ("week_speak", _T("本周朗读")),
            ("week_chars", _T("本周字数")),
            ("week_delta", _T("较上周")),
            ("total_speak", _T("累计朗读")),
            ("total_chars", _T("累计字数")),
            ("total_days", _T("有效阅读天数")),
            ("streak", _T("当前连续")),
            ("best_streak", _T("最长连续")),
            ("best_day", _T("单日最高字数")),
        )
        for idx, (key, title) in enumerate(items):
            r, c = divmod(idx, 3)
            cell = tk.Frame(sum_box, bg=self._bg)
            cell.grid(row=r, column=c, sticky="w", pady=4)
            tk.Label(cell, text=title, font=("微软雅黑", 9), bg=self._bg,
                     fg=self._sub_fg).pack(anchor="w")
            v = tk.Label(cell, text="—", font=("微软雅黑", 11, "bold"), bg=self._bg,
                         fg=self._main_fg)
            v.pack(anchor="w")
            self._sum_labels[key] = v

        btns = tk.Frame(self.top, bg=self._bg)
        btns.pack(fill="x", **pad, pady=(2, 14))
        ttk.Button(btns, text=_T("刷新"), command=self.refresh).pack(side="left")
        ttk.Button(btns, text=_T("关闭"), command=self.top.destroy).pack(side="right")

    # ---------- 刷新 ----------
    def refresh(self):
        try:
            snap = self.app.stats.snapshot() if getattr(self.app, "stats", None) else None
        except Exception:
            snap = None
        if not snap:
            return
        t = snap["today"]
        self._set_card("speak", fmt_duration(t["speak"]), _T("今日朗读时长"))
        self._set_card("chars", fmt_chars(t["chars"]), _T("今日阅读字数"))
        self._set_card("app", fmt_duration(t["app"]), _T("今日软件开启"))
        self._set_card("streak", "%d %s" % (snap["streak"], _T("天")), _T("连续阅读天数"))

        self._draw_week(snap)

        self._sum_labels["week_speak"].configure(text=fmt_duration(snap["week_speak"]))
        self._sum_labels["week_chars"].configure(text=fmt_chars(snap["week_chars"]))
        self._sum_labels["week_delta"].configure(text=self._delta_text(snap))
        self._sum_labels["total_speak"].configure(text=fmt_duration(snap["total_speak"]))
        self._sum_labels["total_chars"].configure(text=fmt_chars(snap["total_chars"]))
        self._sum_labels["total_days"].configure(
            text="%d %s" % (snap["total_days"], _T("天")))
        self._sum_labels["streak"].configure(
            text="%d %s" % (snap["streak"], _T("天")))
        self._sum_labels["best_streak"].configure(
            text="%d %s" % (snap["best_streak"], _T("天")))
        best_day = snap["best_day"]
        self._sum_labels["best_day"].configure(
            text=("%s（%s）" % (fmt_chars(snap["best_day_chars"]), best_day))
            if best_day else fmt_chars(snap["best_day_chars"]))

    def _set_card(self, key, value, title):
        val, ttl = self._card_values[key]
        val.configure(text=value)
        ttl.configure(text=title)

    def _delta_text(self, snap):
        cur = snap["week_speak"]
        prev = snap["last_week_speak"]
        if prev <= 0 and cur <= 0:
            return _T("持平")
        if prev <= 0:
            return _T("上周无记录")
        pct = (cur - prev) / prev * 100.0
        if abs(pct) < 3:
            return _T("持平")
        arrow = "↑" if pct > 0 else "↓"
        return "%s %.0f%%" % (arrow, abs(pct))

    def _draw_week(self, snap):
        c = self.canvas
        c.delete("all")
        try:
            w = c.winfo_width()
            h = c.winfo_height()
        except Exception:
            w, h = 0, 0
        if w <= 1:
            w, h = 800, 200
        pad_l, pad_r = 34, 18
        pad_t, pad_b = 28, 34
        plot_w = max(60, w - pad_l - pad_r)
        plot_h = max(40, h - pad_t - pad_b)
        week = snap["week"]
        today_idx = -1
        try:
            from datetime import date
            today_idx = date.today().weekday()
        except Exception:
            today_idx = -1
        mx = max([x["speak"] for x in week] + [0.0])
        # 基线
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill="#E6EAF1")
        if mx <= 0:
            c.create_text(w / 2, pad_t + plot_h / 2, text=_T("本周还没有朗读记录"),
                          fill=self._sub_fg, font=("微软雅黑", 9))
            for i, item in enumerate(week):
                cx = pad_l + plot_w * (i + 0.5) / 7.0
                c.create_text(cx, h - pad_b + 16, text=_T(WEEK_LABELS[item["weekday"]]),
                              fill=self._sub_fg, font=("微软雅黑", 8))
            return
        bar_w = min(40, plot_w / 7.0 * 0.52)
        for i, item in enumerate(week):
            cx = pad_l + plot_w * (i + 0.5) / 7.0
            mins = item["speak"] / 60.0
            bh = plot_h * (item["speak"] / mx) if mx else 0
            bar_color = self._accent if i == today_idx else self._accent_light
            if bh > 0:
                c.create_rectangle(cx - bar_w / 2, h - pad_b - bh,
                                   cx + bar_w / 2, h - pad_b,
                                   fill=bar_color, outline="")
                c.create_text(cx, h - pad_b - bh - 11,
                              text=("%.0f%s" % (mins, _T("分")) if mins >= 1
                                    else "%d%s" % (int(mins * 60), _T("秒"))),
                              fill=self._main_fg if i == today_idx else self._sub_fg,
                              font=("微软雅黑", 8))
            c.create_text(cx, h - pad_b + 16, text=_T(WEEK_LABELS[item["weekday"]]),
                          fill=self._main_fg if i == today_idx else self._sub_fg,
                          font=("微软雅黑", 8, "bold" if i == today_idx else "normal"))
