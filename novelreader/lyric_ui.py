# -*- coding: utf-8 -*-
"""桌面朗读字幕窗（歌词式悬浮窗）：
独立无边框置顶小窗，跟随朗读逐句显示当前句；支持拖动、四边/四角拉伸缩放（最小 600x150）、
置顶、全屏、背景透明切换、锁定、播放/暂停、音量增减、静音、收藏当前句、字号调节（滚轮）。
主文字带反色描边 + 自动换行，当前句优先占满显示区（空间不足时自动隐藏副文字）。
图标为 64px 扁平填充美术资源（icons_base64 内嵌，运行时可换色），不使用 emoji / 系统图标。
设置持久化到 storage（lyric_* 键）。
"""
import base64
import io
import sys
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageOps, ImageTk
from .i18n import T as _T
from .icons_base64 import ICONS

MIN_W, MIN_H = 1000, 200
EDGE = 7  # 边缘拉伸热区宽度
ICON_VIEW = 44  # 按钮基准显示尺寸（触控友好）
DEF_GEO = "1000x200+0+0"  # 默认：1000x200，初始定位屏幕左上角

_N = "#D7DCE4"   # 常态（亮灰白）
_H = "#FFFFFF"   # hover（纯白）
_A = "#4FC3F7"   # 激活（强调蓝）

_icon_cache = {}


def _dpi_scale():
    """当前屏幕 DPI 缩放（1.0=100%，1.25=125%…）。PhotoImage 按物理像素渲染，避免系统拉伸毛边。"""
    root = tk._default_root
    if root is not None:
        try:
            return max(1.0, root.winfo_fpixels("1i") / 96.0)
        except Exception:
            pass
    return 1.0


def _icon_px():
    return max(ICON_VIEW, int(round(ICON_VIEW * _dpi_scale())))


def get_icon(name, color=_N, px=None):
    """256px 高清源 -> LANCZOS 缩放到物理像素尺寸的 PhotoImage（缓存复用）。"""
    px = px or _icon_px()
    key = (name, color, px)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    raw = base64.b64decode(ICONS[name])
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    r, g, b, a = img.split()
    # 用 alpha 通道染色：不透明像素(线条)→目标色，透明背景保持透明。
    # 兼容两种资源：旧白线条图标(alpha=线条) 与 用户黑线条白底套件(转透明后 alpha=线条)。
    colored = ImageOps.colorize(a, (0, 0, 0), color)
    colored.putalpha(a)
    colored = colored.resize((px, px), Image.LANCZOS)
    photo = ImageTk.PhotoImage(colored)
    _icon_cache[key] = photo
    return photo


def _icon(cv, name, color, size=None):
    """图片图标（美术资源）：256px 源 LANCZOS 缩放到 DPI 物理尺寸，画到画布中心。"""
    cv.delete("all")
    px = int(size or _icon_px())
    cv.configure(width=px, height=px)
    photo = get_icon(name, color, px)
    cv.create_image(px // 2, px // 2, image=photo)
    cv.photo = photo  # 保持引用


class _TextCanvas(tk.Canvas):
    """带反色描边 + 自动换行的文字画布。set() 后按内容高度自适应。"""

    def __init__(self, master, bg, fg, outline, font, **kw):
        super().__init__(master, bg=bg, highlightthickness=0, **kw)
        self._fg = fg
        self._outline = outline
        self._font = font
        self._text = ""

    def _wrap_w(self):
        w = self.winfo_width()
        return max(80, w - 10)

    def set(self, text):
        self._text = text or ""
        self.delete("all")
        if self._text:
            w = self._wrap_w()
            # anchor="n"：文字顶部中心对齐到 (w/2, 7)，单行与多行均水平居中
            kw = dict(text=self._text, font=self._font, width=w,
                      justify="center", anchor="n")
            # 12 方向双层描边（偏移 1px + 2px）：反色描边在任何背景下清晰可见
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1),
                           (-1, 1), (1, 1), (0, -2), (0, 2), (-2, 0), (2, 0)):
                self.create_text(w / 2 + 7 + dx, 7 + dy, fill=self._outline, **kw)
            self.create_text(w / 2 + 7, 7, fill=self._fg, **kw)
            bbox = self.bbox("all")
            hgt = (bbox[3] - bbox[1]) + 14 if bbox else 40
            self.configure(height=max(24, hgt))
        else:
            self.configure(height=24)

    def set_font(self, font):
        self._font = font
        if self._text:
            self.set(self._text)

    def set_colors(self, fg, outline):
        """透明主题换色（黑字白描边 / 原色）。"""
        self._fg, self._outline = fg, outline
        if self._text:
            self.set(self._text)

    def refit(self):
        """窗口宽度变化后重新换行。"""
        if self._text:
            self.set(self._text)


class LyricWindow:
    """桌面朗读字幕窗。host 为阅读器对象，需提供：
    tts.is_playing()/is_paused()/set_volume()、_tts_toggle()、_change_volume(delta)、
    _add_bookmark_text(text)、_flash_status(msg)、book/chapter_idx/char_offset。"""

    BG = "#1F2430"
    FG = "#FFFFFF"
    FG_SUB = "#C8CDD6"
    ACCENT = "#4FC3F7"
    BTN = "#3A4150"
    BTN_HOVER = "#4C5568"
    BTN_DIS = "#5A6272"
    EDGE_N = "#39414F"
    OUTLINE = "#101318"

    def __init__(self, root, host):
        self.root = root
        self.host = host
        self.settings = host.settings
        self.storage = host.storage
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=self.BG)
        self._locked = bool(self.settings.get("lyric_locked", False))
        self._on_top = bool(self.settings.get("lyric_on_top", False))
        self._transparent = bool(self.settings.get("lyric_transparent", False))
        self._invert = bool(self.settings.get("lyric_invert", False))
        self._fullscreen = False
        self._font_size = int(self.settings.get("lyric_font_size", 26))
        self._cur_text = ""
        self._drag_off = None
        self._resize_off = None
        self._resize_edge = None
        self._normal_geo = None
        self._muted = False
        self._mute_vol = 100
        self._btn_refs = {}

        self._build()
        geo = self.settings.get("lyric_geometry", "")
        try:
            self.win.geometry(geo if geo else DEF_GEO)
        except Exception:
            self.win.geometry(DEF_GEO)
        self.win.attributes("-topmost", self._on_top)
        self.win.deiconify()
        self._apply_bg_mode()
        self._refresh_play_icon()
        self._refresh_mute_icon()
        self._apply_lock_state()
        self._save_all()

    # ---------------- 布局 ----------------
    def _build(self):
        self.frame = tk.Frame(self.win, bg=self.BG)
        self.frame.pack(fill="both", expand=True, padx=0, pady=0)

        # 按钮条（未锁定可见）
        self.bar = tk.Frame(self.frame, bg=self.BG)
        self.bar.pack(fill="x", padx=10, pady=(EDGE + 2, 2))

        def icon_btn(name, cmd, tooltip="", active=False):
            px = _icon_px()
            f = tk.Frame(self.bar, bg=self.BG, width=px + 2, height=px + 2)
            f.pack_propagate(False)
            cv = tk.Canvas(f, width=px, height=px, bg=self.BG, highlightthickness=0)
            cv.pack(pady=1)
            color = self.ACCENT if active else self.FG_SUB
            _icon(cv, name, color)
            cv.bind("<Enter>", lambda e, c=cv, n=name, a=active: _icon(c, n, self.FG if not a else self.ACCENT))
            cv.bind("<Leave>", lambda e, c=cv, n=name, a=active: _icon(c, n, self.ACCENT if a else self.FG_SUB))
            cv.bind("<Button-1>", lambda e, fn=cmd: fn())
            if tooltip:
                f.bind("<Enter>", lambda e, t=tooltip: self._hint(t))
                f.bind("<Leave>", lambda e: self._hint(""))
                cv.bind("<Enter>", lambda e, t=tooltip: self._hint(t))
                cv.bind("<Leave>", lambda e: self._hint(""))
            f.pack(side="left", padx=2)
            return {"frame": f, "cv": cv, "active": active, "name": name}

        bar = self.bar
        # 第一位：透明+锁定 合并按钮（按下=透明+锁定，再按=解除透明+锁定）
        self._b_bglock = icon_btn("bglock", self._toggle_bg_lock, _T("透明 + 锁定"), active=self._transparent)
        self._b_invert = icon_btn("invert", self._toggle_invert, _T("文字颜色反转"), active=self._invert)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        self._b_play = icon_btn("play", self._toggle_play, _T("播放 / 暂停"))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        self._b_vold = icon_btn("vol_down", lambda: self._safe(lambda: self.host._change_volume(-10)), _T("音量减小"))
        self._b_volu = icon_btn("vol_up", lambda: self._safe(lambda: self.host._change_volume(10)), _T("音量增大"))
        self._b_mute = icon_btn("mute", self._toggle_mute, _T("静音"))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        self._b_book = icon_btn("bookmark", self._bookmark_cur, _T("收藏当前句"))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        self._b_fdec = icon_btn("font_dec", lambda: self._change_font(-2), _T("字号减小"))
        self._b_finc = icon_btn("font_inc", lambda: self._change_font(2), _T("字号增大"))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        self._b_pin = icon_btn("pin", self._toggle_top, _T("置顶"), active=self._on_top)
        self._b_full = icon_btn("fullscreen", self._toggle_fullscreen, _T("全屏"))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        self._b_close = icon_btn("close", self.close, _T("关闭桌面歌词"))
        self._hint_lbl = tk.Label(bar, text="", bg=self.BG, fg=self.FG_SUB, font=("微软雅黑", 9))
        self._hint_lbl.pack(side="right", padx=6)

        # 章节名
        self.chapter_lbl = tk.Label(self.frame, text="", bg=self.BG, fg=self.FG_SUB,
                                    font=("微软雅黑", 11), anchor="center")
        self.chapter_lbl.pack(fill="x", padx=16, pady=(4, 0))

        # 当前句（主文字：描边 + 自动换行，占满显示区）
        self.text_cv = _TextCanvas(self.frame, self.BG, self.FG, self.OUTLINE,
                                   ("微软雅黑", self._font_size, "bold"))
        self.text_cv.pack(fill="both", expand=True, padx=14, pady=(2, 0))
        self.text_cv.bind("<Configure>", lambda e: self.text_cv.refit())

        # 缩放手柄
        px = _icon_px()
        self.grip = tk.Canvas(self.frame, width=px, height=px, bg=self.BG, highlightthickness=0)
        _icon(self.grip, "grip", self.FG_SUB)
        self.grip.pack(side="bottom", anchor="se", padx=2, pady=(0, 2))
        self.grip.bind("<Button-1>", self._resize_start)
        self.grip.bind("<B1-Motion>", self._resize_move)
        self.grip.bind("<ButtonRelease-1>", self._resize_end)

        # 拖动：主区域
        for w in (self.frame, self.chapter_lbl, self.text_cv):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Double-Button-1>", self._on_double)
            w.bind("<Button-3>", self._context_menu)
        # 滚轮直接调字号（无需 Ctrl），锁定态忽略
        for w in (self.text_cv, self.chapter_lbl):
            w.bind("<MouseWheel>", self._on_wheel)

        # 四边 / 四角拉伸热区（未锁定可见）
        self._edge_frames = {}
        self._build_edges()

        # 高度不足时隐藏副文字（当前句优先）
        self.win.bind("<Configure>", self._auto_layout)
        self.win.bind("<Motion>", self._win_motion)

    def _build_edges(self):
        for _n, f in list(getattr(self, "_edge_frames", {}).items()):
            try:
                f.destroy()
            except Exception:
                pass
        self._edge_frames = {}
        c = self.EDGE_N
        sz = EDGE + 5

        def edge(name, cur, x, y, width=0, height=0, relwidth=0.0, relheight=0.0,
                 relx=0.0, rely=0.0, anchor="nw"):
            f = tk.Frame(self.win, bg=c)
            f.place(x=x, y=y, width=width, height=height,
                    relwidth=relwidth, relheight=relheight,
                    relx=relx, rely=rely, anchor=anchor)
            f.bind("<Enter>", lambda e, n=name: self._edge_enter(n))
            f.bind("<Leave>", lambda e, n=name: self._edge_leave(n))
            f.bind("<Button-1>", lambda e, n=name: self._edge_resize_start(e, n))
            f.bind("<B1-Motion>", self._edge_resize_move)
            f.bind("<ButtonRelease-1>", self._edge_resize_end)
            self._edge_frames[name] = f
            return f

        # place 精确定位四边/四角（anchor + rel 对齐，窗口 resize 自动跟随）
        edge("t", "sb_v_double_arrow", 0, 0, height=EDGE, relwidth=1.0)
        edge("b", "sb_v_double_arrow", 0, 0, height=EDGE, relwidth=1.0,
             relx=0.0, rely=1.0, anchor="sw")
        edge("l", "sb_h_double_arrow", 0, EDGE, width=EDGE, relheight=1.0)
        edge("r", "sb_h_double_arrow", 0, 0, width=EDGE, relheight=1.0,
             relx=1.0, anchor="ne")
        edge("tl", "size_nw_se", 0, 0, sz, sz)
        edge("tr", "size_ne_sw", 0, 0, sz, sz, relx=1.0, anchor="ne")
        edge("bl", "size_ne_sw", 0, 0, sz, sz, rely=1.0, anchor="sw")
        edge("br", "size_nw_se", 0, 0, sz, sz, relx=1.0, rely=1.0, anchor="se")

    _EDGE_CURSORS = {"t": "sb_v_double_arrow", "b": "sb_v_double_arrow",
                     "l": "sb_h_double_arrow", "r": "sb_h_double_arrow",
                     "tl": "size_nw_se", "tr": "size_ne_sw",
                     "bl": "size_ne_sw", "br": "size_nw_se"}

    def _edge_enter(self, name):
        if self._locked or self._transparent:
            return
        self.win.config(cursor=self._EDGE_CURSORS.get(name, ""))
        for n, f in self._edge_frames.items():
            f.configure(bg=self.ACCENT if n == name else self.EDGE_N)

    def _edge_leave(self, name):
        if self.win.cget("cursor"):
            self.win.config(cursor="")
        for n, f in self._edge_frames.items():
            f.configure(bg=self.EDGE_N)

    def _win_motion(self, e):
        """窗口级鼠标移动：靠近边缘时切换拉伸光标并高亮热区，移开恢复。"""
        if self._locked or self._transparent:
            if self.win.cget("cursor"):
                self.win.config(cursor="")
            return
        x, y = e.x, e.y
        W, H = self.win.winfo_width(), self.win.winfo_height()
        near = EDGE + 3
        cur, hit = "", None
        if x < near and y < near:
            cur, hit = "size_nw_se", "tl"
        elif x >= W - near and y < near:
            cur, hit = "size_ne_sw", "tr"
        elif x < near and y >= H - near:
            cur, hit = "size_ne_sw", "bl"
        elif x >= W - near and y >= H - near:
            cur, hit = "size_nw_se", "br"
        elif y < near:
            cur, hit = "sb_v_double_arrow", "t"
        elif y >= H - near:
            cur, hit = "sb_v_double_arrow", "b"
        elif x < near:
            cur, hit = "sb_h_double_arrow", "l"
        elif x >= W - near:
            cur, hit = "sb_h_double_arrow", "r"
        if hit:
            self.win.config(cursor=cur)
            for n, f in self._edge_frames.items():
                f.configure(bg=self.ACCENT if n == hit else self.EDGE_N)
        else:
            if self.win.cget("cursor"):
                self.win.config(cursor="")
            for n, f in self._edge_frames.items():
                f.configure(bg=self.EDGE_N)

    def _edge_resize_start(self, e, name):
        if self._locked:
            return
        self._resize_edge = name
        self._resize_off = (e.x_root, e.y_root)
        self._resize_geo = (self.win.winfo_x(), self.win.winfo_y(),
                            self.win.winfo_width(), self.win.winfo_height())

    def _edge_resize_move(self, e):
        if self._locked or not self._resize_edge:
            return
        x0, y0, w0, h0 = self._resize_geo
        dx = e.x_root - self._resize_off[0]
        dy = e.y_root - self._resize_off[1]
        x, y, w, h = x0, y0, w0, h0
        edge = self._resize_edge
        if "l" in edge:
            w = max(MIN_W, w0 - dx)
            x = x0 + (w0 - w)
        elif "r" in edge:
            w = max(MIN_W, w0 + dx)
        if "t" in edge:
            h = max(MIN_H, h0 - dy)
            y = y0 + (h0 - h)
        elif "b" in edge:
            h = max(MIN_H, h0 + dy)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def _edge_resize_end(self, e):
        if self._resize_edge:
            self._resize_edge = None
            self._resize_off = None
            self._save_all()

    def _auto_layout(self, e):
        """窗口尺寸变化时：当前句始终占满显示区（无副文字）。"""
        try:
            self.text_cv.refit()
        except Exception:
            pass

    # ---------------- 小工具 ----------------
    def _safe(self, fn):
        try:
            return fn()
        except Exception:
            return None

    def _hint(self, text):
        try:
            self._hint_lbl.configure(text=text)
        except Exception:
            pass

    def _context_menu(self, e):
        """右键菜单：透明+锁定合并状态显式解除入口，普通模式快捷操作。"""
        m = tk.Menu(self.win, tearoff=0)
        if self._transparent or self._locked:
            m.add_command(label=_T("解除透明 + 锁定"), command=self._toggle_bg_lock)
        else:
            m.add_command(label=_T("透明 + 锁定"), command=self._toggle_bg_lock)
        m.add_command(label=_T("置顶"), command=self._toggle_top)
        m.add_command(label=_T("退出桌面模式"), command=self.close)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _change_font(self, d):
        self._font_size = max(14, min(64, self._font_size + d))
        self.text_cv.set_font(("微软雅黑", self._font_size, "bold"))
        self._save_all()

    def _on_wheel(self, e):
        if self._locked:
            return
        d = 2 if e.delta > 0 else -2
        self._change_font(d)

    # ---------------- 播放 / 音量 ----------------
    def _toggle_play(self):
        self._safe(self.host._tts_toggle)
        self.root.after(120, self._refresh_play_icon)

    def _refresh_play_icon(self):
        try:
            name = "pause" if self._safe(self.host.tts.is_playing) else "play"
        except Exception:
            name = "play"
        if name != self._b_play["name"]:
            self._b_play["name"] = name
            _icon(self._b_play["cv"], name, self.FG_SUB)

    def _toggle_mute(self):
        try:
            cur = int(self._safe(self.host.tts.get_volume) or 0)
        except Exception:
            cur = int(self.settings.get("volume", 100))
        if cur > 0:
            self._mute_vol = cur
            self._safe(lambda: self.host.tts.set_volume(0))
            self._muted = True
        else:
            self._safe(lambda: self.host.tts.set_volume(self._mute_vol))
            self._muted = False
        self._set_mute_icon()

    def _set_mute_icon(self):
        col = self.ACCENT if self._muted else self.FG_SUB
        _icon(self._b_mute["cv"], "mute", col)
        self._b_mute["active"] = self._muted

    def _refresh_mute_icon(self):
        try:
            cur = int(self._safe(self.host.tts.get_volume) or 0)
            self._muted = cur <= 0
        except Exception:
            self._muted = bool(self.settings.get("volume", 100) <= 0)
        self._set_mute_icon()

    # ---------------- 置顶 / 全屏 / 背景 / 锁定 ----------------
    def _toggle_top(self):
        self._on_top = not self._on_top
        self.win.attributes("-topmost", self._on_top)
        self._b_pin["active"] = self._on_top
        _icon(self._b_pin["cv"], "pin", self.ACCENT if self._on_top else self.FG_SUB)
        self._save_all()

    def _toggle_bg(self):
        self._transparent = not self._transparent
        self._apply_bg_mode()
        self._hint(_T("背景透明") if self._transparent else _T("背景不透明"))
        if self._transparent:
            try:
                self.host._flash_status(_T("右键或双击可恢复背景"))
            except Exception:
                pass
        self._save_all()

    def _on_double(self, e):
        """双击：透明+锁定 合并状态→同时解除（解锁并恢复背景）。"""
        if self._locked or self._transparent:
            self._toggle_bg_lock()

    def _toggle_bg_lock(self):
        """透明+锁定 合并按钮：按下=同时透明+锁定，再按=同时解除。"""
        self._transparent = not self._transparent
        self._locked = not self._locked
        self._apply_bg_mode()
        self._apply_lock_state()
        self._b_bglock["active"] = self._transparent
        _icon(self._b_bglock["cv"], "bglock", self.ACCENT if self._transparent else self.FG_SUB)
        self._save_all()

    def _apply_bg_mode(self):
        """背景透明：Windows 用颜色键控真透明（背景消失、留正文+按钮），
        macOS/Linux 无透明色键控，降级为整窗半透明近似。"""
        if self._transparent:
            if sys.platform.startswith("win"):
                try:
                    self.win.attributes("-transparentcolor", self.BG)
                    self.win.attributes("-alpha", 1.0)
                except Exception:
                    self.win.attributes("-alpha", 0.40)
            else:
                self.win.attributes("-alpha", 0.40)
            # 背景消失后保留按钮条 / 边缘 / 手柄：图标与文字浮在透明背景上，
            # 合并按钮（透明+锁定）就在第一位，随时可点解除
            self._apply_theme()
        else:
            if sys.platform.startswith("win"):
                try:
                    self.win.attributes("-transparentcolor", "")
                    self.win.attributes("-alpha", 0.96)
                except Exception:
                    self.win.attributes("-alpha", 0.96)
            else:
                self.win.attributes("-alpha", 0.96)
            # 恢复按钮 / 边缘（按锁定态决定显示）
            self._apply_theme()
            self._apply_lock_state()
        self._b_bglock["active"] = self._transparent
        _icon(self._b_bglock["cv"], "bglock", self.ACCENT if self._transparent else self.FG_SUB)

    def _apply_theme(self):
        """文字/描边主题：由"文字颜色反转"按钮决定。
        invert=黑字白描边；否则白字黑描边。背景永远不变（普通模式深色、透明模式键控色），
        图标颜色按模式：透明模式下用深色图标（浅壁纸可见），普通模式浅色图标。"""
        if self._invert:
            fg, outline, sub = "#000000", "#FFFFFF", "#000000"
        else:
            fg, outline, sub = self.FG, self.OUTLINE, self.FG_SUB
        btn = "#20252E" if self._transparent else self.FG_SUB
        self.text_cv.set_colors(fg, outline)
        self.chapter_lbl.configure(fg=sub)
        self._hint_lbl.configure(fg=sub)
        for b in self._btn_list():
            if b is self._b_bglock:
                continue  # bglock 按钮颜色由 _apply_bg_mode 单独维护
            name = b["name"]
            if name == "lock":
                name = "unlock" if self._locked else "lock"
            col = self.ACCENT if b["active"] else btn
            _icon(b["cv"], name, col)
        if getattr(self, "_lock_cv", None):
            _icon(self._lock_cv, "lock", btn)

    def _toggle_invert(self):
        """文字颜色反转开关：黑字白描边 / 白字黑描边，用户自行选择。"""
        self._invert = not self._invert
        self._apply_theme()
        self._b_invert["active"] = self._invert
        _icon(self._b_invert["cv"], "invert", self.ACCENT if self._invert else self.FG_SUB)
        self._save_all()

    def _btn_list(self):
        return (self._b_bglock, self._b_invert, self._b_play, self._b_vold, self._b_volu,
                self._b_mute, self._b_book, self._b_fdec, self._b_finc, self._b_pin,
                self._b_full, self._b_close)

    def _toggle_fullscreen(self):
        if not self._fullscreen:
            self._normal_geo = self.win.geometry()
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            self.win.geometry(f"{sw}x{sh}+0+0")
            self.win.attributes("-alpha", 0.90)
            self.text_cv.set_font(("微软雅黑", self._font_size + 10, "bold"))
            self._b_full["active"] = True
            _icon(self._b_full["cv"], "fullscreen", self.ACCENT)
        else:
            if self._normal_geo:
                self.win.geometry(self._normal_geo)
            self._apply_bg_mode()
            self.text_cv.set_font(("微软雅黑", self._font_size, "bold"))
            self._b_full["active"] = False
            _icon(self._b_full["cv"], "fullscreen", self.FG_SUB)
        self._fullscreen = not self._fullscreen
        self._save_all()

    def _toggle_lock(self):
        if not self._locked:
            self._normal_geo = self.win.geometry()
            self._save_all()
        self._locked = not self._locked
        self._apply_lock_state()
        self._save_all()

    def _apply_lock_state(self):
        locked = self._locked
        btns = self._btn_list()
        # 锁定态：仅保留第一位"透明+锁定"合并按钮（点击即同时解除），其余隐藏
        for b in btns:
            if b is self._b_bglock:
                continue
            b["frame"].pack_forget()
        for n, f in self._edge_frames.items():
            f.place_forget()
        if locked:
            self.grip.pack_forget()
            self._b_bglock["active"] = True
            _icon(self._b_bglock["cv"], "bglock", self.ACCENT)
        else:
            for b in btns:
                if b is self._b_bglock:
                    continue
                b["frame"].pack(side="left", padx=2)
            self._build_edges()
            self.grip.pack(side="bottom", anchor="se", padx=2, pady=(0, 2))
        # 双击解除（透明+锁定合并状态）
        for w in (self.frame, self.chapter_lbl, self.text_cv):
            w.unbind("<Double-Button-1>")
            w.bind("<Double-Button-1>", self._on_double)
        self._refresh_play_icon()

    # ---------------- 拖动 / 缩放 ----------------
    def _drag_start(self, e):
        if self._locked:
            return
        self._drag_off = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e):
        if self._locked or not self._drag_off:
            return
        x = e.x_root - self._drag_off[0]
        y = e.y_root - self._drag_off[1]
        self.win.geometry(f"+{x}+{y}")

    def _drag_end(self, e):
        if self._drag_off:
            self._drag_off = None
            self._save_all()

    def _resize_start(self, e):
        if self._locked:
            return
        self._resize_off = (e.x_root, e.y_root)

    def _resize_move(self, e):
        if self._locked or not self._resize_off:
            return
        dx = e.x_root - self._resize_off[0]
        dy = e.y_root - self._resize_off[1]
        w = max(MIN_W, self.win.winfo_width() + dx)
        h = max(MIN_H, self.win.winfo_height() + dy)
        self.win.geometry(f"{w}x{h}")
        self._resize_off = (e.x_root, e.y_root)

    def _resize_end(self, e):
        if self._resize_off:
            self._resize_off = None
            self._save_all()

    # ---------------- 数据更新（由 reader 事件驱动） ----------------
    def show_sentence(self, chapter_title, text):
        self._cur_text = text or ""
        self.chapter_lbl.configure(text=chapter_title or "")
        self.text_cv.set(self._cur_text)
        self._refresh_play_icon()

    def clear(self):
        self._cur_text = ""
        self.chapter_lbl.configure(text="")
        self.text_cv.set("")
        self._refresh_play_icon()

    def _bookmark_cur(self):
        if not self._cur_text:
            self._safe(lambda: self.host._flash_status(_T("当前没有正在朗读的句子")))
            return
        self._safe(lambda: self.host._add_bookmark_text(self._cur_text))
        self._safe(lambda: self.host._flash_status(_T("已收藏当前句")))

    # ---------------- 设置持久化 ----------------
    def _save_all(self):
        try:
            self.settings["lyric_geometry"] = self.win.geometry()
            self.settings["lyric_font_size"] = self._font_size
            self.settings["lyric_on_top"] = bool(self._on_top)
            self.settings["lyric_locked"] = bool(self._locked)
            self.settings["lyric_transparent"] = bool(self._transparent)
            self.settings["lyric_invert"] = bool(self._invert)
            self.storage.save()
        except Exception:
            pass

    def close(self):
        try:
            self._save_all()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
