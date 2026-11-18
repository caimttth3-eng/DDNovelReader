# -*- coding: utf-8 -*-
"""数据同步：云端 WebDAV + 局域网本地同步（v2.2.9）。

- 云端：保持原有 WebDAV 行为（书架信息 / 阅读进度 / 书签 / 书源文件）
- 局域网：软件内嵌 WebDAV 服务端（LanServer），随软件启停，手机端按「配对信息」四项填写
- 两条通道共用同一个 SyncEngine 合并逻辑，**串行执行**（_RUN_LOCK），避免并发落盘
- 局域网模式下不再需要"每 10 分钟问远端"：手机写入后由服务端置脏标记，Tk 侧 2 秒轮询取走并触发一次合并
"""
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .i18n import T as _T
from .constants import UI_THEMES, make_scrollbar
from .storage import data_dir
from .lan_server import (
    LanServer, DEFAULT_PORT, DEFAULT_USER, DEFAULT_REMOTE_DIR as LAN_REMOTE_DIR,
    LAN_ROOT_DIRNAME, new_token, ST_RUNNING, ST_STOPPED, ST_STARTING, ST_ERROR,
)
from .sync_engine import (
    SyncEngine,
    ensure_device,
    default_device_name,
    DEFAULT_REMOTE_DIR,
)

AUTO_INTERVAL_MS = 10 * 60 * 1000   # 云端自动同步间隔：10 分钟
LAN_WATCH_MS = 2000                 # 局域网脏标记轮询间隔（本地标志位，不产生网络请求）

# 全局单飞锁：云端定时 / 云端手动 / 局域网手动 / 局域网事件 / 书架一键 全部串行
_RUN_LOCK = threading.Lock()

LAN_SETTING_KEYS = ("lan_enabled", "lan_port", "lan_user", "lan_token", "lan_dir", "lan_auto_merge")


class SyncMixin:
    """设置面板「数据同步」：云端 WebDAV + 局域网本地同步。"""

    # ---------------- 入口 ----------------
    def _show_sync_dialog(self):
        _SyncDlg(self)

    # ---------------- 设置默认值 ----------------
    def _lan_defaults(self):
        """补齐局域网相关设置（首次调用时生成配对码）。"""
        s = self.settings
        changed = False
        if "lan_enabled" not in s:
            s["lan_enabled"] = False
        if not s.get("lan_port"):
            s["lan_port"] = DEFAULT_PORT
        if not s.get("lan_user"):
            s["lan_user"] = DEFAULT_USER
        if not str(s.get("lan_token") or "").strip():
            s["lan_token"] = new_token()
            changed = True
        if not s.get("lan_dir"):
            s["lan_dir"] = LAN_REMOTE_DIR
        if "lan_auto_merge" not in s:
            s["lan_auto_merge"] = True
        if changed:
            for k in LAN_SETTING_KEYS:
                try:
                    self.storage.set_setting(k, s.get(k))
                except Exception:
                    pass
        return s

    def _lan_root(self):
        """内嵌服务的根目录：<数据目录>/lan（只暴露同步数据，绝不暴露 library.json）。"""
        return os.path.join(data_dir(), LAN_ROOT_DIRNAME)

    # ---------------- 服务端生命周期 ----------------
    def _lan_server_obj(self, recreate=False):
        """取/建 LanServer；端口、配对码变化时重建。"""
        s = self._lan_defaults()
        port = int(s.get("lan_port") or DEFAULT_PORT)
        user = str(s.get("lan_user") or DEFAULT_USER)
        token = str(s.get("lan_token") or "")
        rdir = str(s.get("lan_dir") or LAN_REMOTE_DIR)
        root = self._lan_root()
        cur = getattr(self, "_lan_srv", None)
        if cur is not None and not recreate:
            info = cur.pairing_info()
            same = (info.get("port") == port and info.get("user") == user
                    and info.get("pass") == token and info.get("dir") == rdir)
            if same:
                return cur
            try:
                cur.stop()
            except Exception:
                pass
        if cur is not None:
            try:
                cur.stop()
            except Exception:
                pass
        srv = LanServer(root, port=port, username=user, password=token, remote_dir=rdir)
        self._lan_srv = srv
        return srv

    def _lan_start(self, quiet=True):
        """启动内嵌服务；返回 (ok, message)。"""
        try:
            s = self._lan_defaults()
            srv = self._lan_server_obj()
            if srv.running():
                return True, ""
            ok, err = srv.start()
            if not ok:
                try:
                    self.settings["lan_last_error"] = err
                    self.storage.set_setting("lan_last_error", err)
                except Exception:
                    pass
                if not quiet:
                    messagebox.showerror(_T("局域网本地同步"), err)
            return ok, err
        except Exception as e:                                   # noqa: BLE001
            return False, str(e)

    def _lan_stop(self):
        srv = getattr(self, "_lan_srv", None)
        if srv is None:
            return True
        try:
            return srv.stop()
        except Exception:
            return False

    def _lan_status(self):
        """给界面用的状态快照（服务未创建时给出"未启用"）。"""
        srv = getattr(self, "_lan_srv", None)
        if srv is None:
            return {"state": ST_STOPPED, "port": (self.settings.get("lan_port") or DEFAULT_PORT),
                    "addresses": [], "uptime": 0.0, "requests": 0, "active": 0,
                    "last_request": "", "last_error": "", "error": "", "clients": {}}
        return srv.status()

    def _lan_auto_start(self):
        """软件启动后调用：开了局域网同步就自动开服并挂上"变更即合并"的看门狗。"""
        try:
            if not self.settings.get("lan_enabled", False):
                return
            self._lan_defaults()
            ok, err = self._lan_start(quiet=True)
            if ok:
                self._lan_schedule_watch()
            else:
                try:
                    self._flash_status(_T("局域网同步启动失败：{e}").format(e=err))
                except Exception:
                    pass
        except Exception:
            pass

    # ---------------- 事件驱动合并（局域网） ----------------
    def _lan_schedule_watch(self):
        if not getattr(self, "_lan_watch_id", None):
            try:
                self._lan_watch_id = self.root.after(LAN_WATCH_MS, self._lan_watch_tick)
            except Exception:
                pass

    def _lan_watch_tick(self):
        self._lan_watch_id = None
        try:
            srv = getattr(self, "_lan_srv", None)
            if srv is not None and srv.running() and srv.take_dirty():
                if self.settings.get("lan_auto_merge", True):
                    self._sync_run_channel("lan", silent=True)
            if self.settings.get("lan_enabled", False):
                self._lan_schedule_watch()
        except Exception:
            pass

    # ---------------- 云端自动同步（保持不变） ----------------
    def _sync_auto_start(self):
        try:
            if not self.storage.get_setting("sync_auto", False):
                return
            if not str(self.storage.get_setting("sync_server", "") or "").strip():
                return
            self._sync_run_channel("cloud", silent=True)
            self._sync_auto_schedule()
        except Exception:
            pass

    def _sync_auto_schedule(self):
        try:
            if not self.storage.get_setting("sync_auto", False):
                return
            self._sync_timer_id = self.root.after(AUTO_INTERVAL_MS, self._sync_auto_tick)
        except Exception:
            pass

    def _sync_auto_tick(self):
        self._sync_timer_id = None
        try:
            if self.storage.get_setting("sync_auto", False):
                self._sync_run_channel("cloud", silent=True)
                self._sync_auto_schedule()
        except Exception:
            pass

    # ---------------- 统一执行入口 ----------------
    def _lan_settings_overlay(self):
        """把局域网当成一个"本机 WebDAV 服务器"，复用同一套 SyncEngine。"""
        s = self._lan_defaults()
        port = int(self._lan_status().get("port") or s.get("lan_port") or DEFAULT_PORT)
        overlay = dict(self.settings)
        overlay.update({
            "sync_server": "http://127.0.0.1:%d/" % port,
            "sync_user": str(s.get("lan_user") or DEFAULT_USER),
            "sync_pass": str(s.get("lan_token") or ""),
            "sync_dir": str(s.get("lan_dir") or LAN_REMOTE_DIR),
            "sync_verify_tls": False,
        })
        return overlay

    def _sync_run_channel(self, channel, silent=False, on_done=None):
        """跑一次同步（channel = cloud / lan）。全局单飞：已在跑则直接返回 False。"""
        if not _RUN_LOCK.acquire(blocking=False):
            if on_done is not None and not silent:
                try:
                    self.root.after(0, lambda: on_done({"ok": False, "error": _T("已有同步任务在进行中")}))
                except Exception:
                    pass
            return False
        try:
            if channel == "lan":
                srv = getattr(self, "_lan_srv", None)
                if srv is None or not srv.running():
                    ok, err = self._lan_start(quiet=True)
                    if not ok:
                        _RUN_LOCK.release()
                        report = {"ok": False, "error": err, "kind": "lan"}
                        if not silent:
                            try:
                                self.root.after(0, lambda: self._flash_status(
                                    _T("局域网同步失败：{e}").format(e=err)))
                            except Exception:
                                pass
                        return False
                engine = SyncEngine(self.storage, self._lan_settings_overlay())
            else:
                engine = SyncEngine(self.storage, self.settings)
        except Exception as e:                                   # noqa: BLE001
            try:
                _RUN_LOCK.release()
            except Exception:
                pass
            if not silent:
                try:
                    self.root.after(0, lambda: self._flash_status(
                        _T("同步失败：{e}").format(e=e)))
                except Exception:
                    pass
            return False

        def _work():
            try:
                report = engine.sync()
            except Exception as e:                               # noqa: BLE001
                report = {"ok": False, "error": str(e)}
            report["kind"] = channel
            try:
                if report.get("imported") or report.get("pulled") or report.get("removed"):
                    self.root.after(0, self._after_sync_report, report)
                if on_done is not None:
                    self.root.after(0, lambda: on_done(report))
                elif not silent:
                    self.root.after(0, self._flash_sync_result, report)
            except Exception:
                pass
            self._store_sync_result(report)
            try:
                _RUN_LOCK.release()
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()
        return True

    def _sync_run_async(self, silent=False, on_done=None):
        """兼容旧调用：云端通道。"""
        return self._sync_run_channel("cloud", silent=silent, on_done=on_done)

    def _lan_merge_now(self, silent=False, on_done=None):
        return self._sync_run_channel("lan", silent=silent, on_done=on_done)

    # ---------------- 书架一键同步 ----------------
    def _sync_shelf_now(self):
        """书架底部「一键同步」：局域网优先，其次云端；串行，结果落状态栏。"""
        if _RUN_LOCK.locked():
            self._flash_status(_T("已有同步任务在进行中"))
            return
        lan_on = bool(self.settings.get("lan_enabled", False))
        cloud_on = bool(str(self.settings.get("sync_server") or "").strip())
        if not lan_on and not cloud_on:
            messagebox.showinfo(_T("数据同步"),
                                _T("还没有配置同步。请到「设置 → 数据同步」填写云端 WebDAV\n"
                                   "或开启局域网本地同步。"))
            return
        self._flash_status(_T("正在同步…"))

        def _after_lan(rep):
            if not cloud_on:
                return
            self._sync_run_channel("cloud", silent=True)

        if lan_on:
            if not self._lan_merge_now(silent=True, on_done=_after_lan) and cloud_on:
                self._sync_run_channel("cloud", silent=True)
        else:
            self._sync_run_channel("cloud", silent=True)

    def _flash_sync_result(self, report):
        if not isinstance(report, dict):
            return
        if report.get("ok"):
            self._flash_status(_T("同步完成：上传 {p} 本，下载 {d} 本").format(
                p=report.get("pushed", 0), d=report.get("pulled", 0)))
        else:
            self._flash_status(_T("同步失败：{e}").format(e=report.get("error") or ""))

    # ---------------- 同步结果落界面（保持不变） ----------------
    def _after_sync_report(self, report):
        if not isinstance(report, dict):
            return
        try:
            self._refresh_bookshelf()
        except Exception:
            pass
        for bid in (report.get("removed_bids") or []):
            try:
                if bid and bid == getattr(self, "current_bid", None):
                    self.current_bid = None
                    self.book = None
                    self._render_empty()
                    self._flash_status(_T("这本书已在其他设备删除，已从书架移除"))
            except Exception:
                pass
        applied = report.get("progress_applied") or {}
        bid_now = getattr(self, "current_bid", None)
        if bid_now and bid_now in applied:
            info = applied.get(bid_now) or {}
            try:
                self.adopt_remote_progress(
                    bid_now, info.get("progress") or {},
                    float(info.get("progress_at") or 0), quiet=False)
            except Exception:
                pass

    def _store_sync_result(self, report):
        try:
            self.settings["sync_last_result"] = {
                "ok": bool(report.get("ok")),
                "pushed": int(report.get("pushed", 0)),
                "pulled": int(report.get("pulled", 0)),
                "remote_only": len(report.get("remote_only") or []),
                "imported": int(report.get("imported", 0)),
                "removed": int(report.get("removed", 0)),
                "deleted": int(report.get("deleted", 0)),
                "channel": str(report.get("kind") or "cloud"),
                "error": str(report.get("error") or ""),
                "time": round(time.time(), 3),
            }
            self.storage.set_setting("sync_last_result", self.settings["sync_last_result"])
            if report.get("ok"):
                self.settings["sync_last_time"] = time.time()
                self.storage.set_setting("sync_last_time", self.settings["sync_last_time"])
        except Exception:
            pass


def _fmt_uptime(sec):
    sec = int(sec or 0)
    if sec < 60:
        return _T("{n} 秒").format(n=sec)
    if sec < 3600:
        return _T("{m} 分 {s} 秒").format(m=sec // 60, s=sec % 60)
    return _T("{h} 小时 {m} 分").format(h=sec // 3600, m=(sec % 3600) // 60)


class _SyncDlg:
    """数据同步设置弹窗：云端 WebDAV / 局域网本地同步 两个页签。"""

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(_T("数据同步"))
        self.top.geometry("860x760")
        self.top.minsize(800, 700)
        self.top.transient(app.root)
        app._center_window(self.top)
        self._bg = UI_THEMES.get(
            app.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        self.top.configure(bg=self._bg)

        self.q = queue.Queue()
        self._busy = False
        self._poll_id = None
        ensure_device(app.storage, app.settings)
        app._lan_defaults()

        s = app.settings
        self.var_server = tk.StringVar(value=str(s.get("sync_server") or ""))
        self.var_user = tk.StringVar(value=str(s.get("sync_user") or ""))
        self.var_pass = tk.StringVar(value=str(s.get("sync_pass") or ""))
        self.var_dir = tk.StringVar(value=str(s.get("sync_dir") or DEFAULT_REMOTE_DIR))
        self.var_device = tk.StringVar(value=str(s.get("sync_device_name") or default_device_name()))
        self.var_auto = tk.BooleanVar(value=bool(s.get("sync_auto", False)))
        self.var_tls = tk.BooleanVar(value=bool(s.get("sync_verify_tls", True)))

        self.var_lan_on = tk.BooleanVar(value=bool(s.get("lan_enabled", False)))
        self.var_lan_port = tk.StringVar(value=str(s.get("lan_port") or DEFAULT_PORT))
        self.var_lan_merge = tk.BooleanVar(value=bool(s.get("lan_auto_merge", True)))
        self.var_lan_user = tk.StringVar(value=str(s.get("lan_user") or DEFAULT_USER))
        self.var_lan_token = tk.StringVar(value=str(s.get("lan_token") or ""))
        self.var_lan_dir = tk.StringVar(value=str(s.get("lan_dir") or LAN_REMOTE_DIR))

        self._build()
        self._refresh_last()
        self._tick()
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)
        self.top.focus_set()

    # ---------------- 界面 ----------------
    def _build(self):
        pad = {"padx": 18}
        head = tk.Frame(self.top, bg=self._bg)
        head.pack(fill="x", **pad, pady=(14, 4))
        tk.Label(head, text=_T("数据同步"), font=("微软雅黑", 13, "bold"),
                 bg=self._bg).pack(anchor="w")
        tk.Label(
            head,
            text=_T("同步书架信息、阅读进度与书签；不同步正文缓存与音频缓存"),
            font=("微软雅黑", 9), fg="#7A7F87", bg=self._bg,
            wraplength=800, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        self.nb = ttk.Notebook(self.top)
        self.nb.pack(fill="both", expand=True, **pad, pady=(8, 0))
        tab_cloud = tk.Frame(self.nb, bg=self._bg)
        tab_lan = tk.Frame(self.nb, bg=self._bg)
        self.nb.add(tab_cloud, text=_T("云端 WebDAV"))
        self.nb.add(tab_lan, text=_T("局域网本地同步"))
        self._build_cloud(tab_cloud)
        self._build_lan(tab_lan)

        btns = tk.Frame(self.top, bg=self._bg)
        btns.pack(fill="x", **pad, pady=(6, 2))
        ttk.Button(btns, text=_T("保存"), command=self._save).pack(side="left")
        ttk.Button(btns, text=_T("关闭"), command=self._on_close).pack(side="right")
        self.lbl_status = tk.Label(
            self.top, text=_T("未同步"), font=("微软雅黑", 10), fg="#1A1B1C",
            bg=self._bg, anchor="w", justify="left")
        self.lbl_status.pack(fill="x", **pad, pady=(4, 2))

        logbox = tk.Frame(self.top, bg=self._bg)
        logbox.pack(fill="both", expand=False, **pad, pady=(0, 12))
        sb = make_scrollbar(logbox, None)
        sb.pack(side="right", fill="y")
        self.txt = tk.Text(
            logbox, height=6, wrap="word", font=("微软雅黑", 9),
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground="#DDE3EC", bg="#FFFFFF", fg="#3C4043",
            yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.txt.yview)
        self.txt.configure(state="disabled")

    def _build_cloud(self, parent):
        form = tk.Frame(parent, bg=self._bg)
        form.pack(fill="x", padx=6, pady=(10, 4))
        form.columnconfigure(1, weight=1)

        def _lab(r, text):
            tk.Label(form, text=text, font=("微软雅黑", 10), bg=self._bg,
                     width=10, anchor="e").grid(row=r, column=0, sticky="e", pady=6, padx=(0, 10))

        _lab(0, _T("服务器地址"))
        ttk.Entry(form, textvariable=self.var_server).grid(row=0, column=1, sticky="ew", pady=6)
        _lab(1, _T("账号"))
        ttk.Entry(form, textvariable=self.var_user).grid(row=1, column=1, sticky="ew", pady=6)
        _lab(2, _T("密码"))
        ttk.Entry(form, textvariable=self.var_pass, show="•").grid(row=2, column=1, sticky="ew", pady=6)
        _lab(3, _T("远端目录"))
        ttk.Entry(form, textvariable=self.var_dir).grid(row=3, column=1, sticky="ew", pady=6)
        _lab(4, _T("设备名称"))
        ttk.Entry(form, textvariable=self.var_device).grid(row=4, column=1, sticky="ew", pady=6)

        opts = tk.Frame(parent, bg=self._bg)
        opts.pack(fill="x", padx=6, pady=(2, 4))
        ttk.Checkbutton(opts, text=_T("启动时自动同步（每 10 分钟一次）"),
                        variable=self.var_auto).pack(anchor="w", pady=2)
        ttk.Checkbutton(opts, text=_T("校验 HTTPS 证书（自建服务器用自签名证书时取消勾选）"),
                        variable=self.var_tls).pack(anchor="w", pady=2)

        btns = tk.Frame(parent, bg=self._bg)
        btns.pack(fill="x", padx=6, pady=(8, 4))
        self.btn_test = ttk.Button(btns, text=_T("测试连接"), command=lambda: self._act("test"))
        self.btn_test.pack(side="left")
        self.btn_sync = ttk.Button(btns, text=_T("立即同步"), command=lambda: self._act("sync"))
        self.btn_sync.pack(side="left", padx=8)

    def _build_lan(self, parent):
        top = tk.Frame(parent, bg=self._bg)
        top.pack(fill="x", padx=6, pady=(10, 2))
        ttk.Checkbutton(top, text=_T("开启局域网本地同步（启动软件时自动开服，关闭软件即停止）"),
                        variable=self.var_lan_on,
                        command=self._toggle_lan).pack(anchor="w", pady=2)
        ttk.Checkbutton(top, text=_T("手机写入后自动合并（推荐）"),
                        variable=self.var_lan_merge).pack(anchor="w", pady=2)

        form = tk.Frame(parent, bg=self._bg)
        form.pack(fill="x", padx=6, pady=(6, 2))
        form.columnconfigure(1, weight=1)

        def _lab(r, text):
            tk.Label(form, text=text, font=("微软雅黑", 10), bg=self._bg,
                     width=10, anchor="e").grid(row=r, column=0, sticky="e", pady=5, padx=(0, 10))

        _lab(0, _T("服务端口"))
        e_port = ttk.Entry(form, textvariable=self.var_lan_port, width=10)
        e_port.grid(row=0, column=1, sticky="w", pady=5)
        _lab(1, _T("账号"))
        ttk.Entry(form, textvariable=self.var_lan_user, state="readonly").grid(
            row=1, column=1, sticky="ew", pady=5)
        _lab(2, _T("配对码（密码）"))
        ttk.Entry(form, textvariable=self.var_lan_token, state="readonly").grid(
            row=2, column=1, sticky="ew", pady=5)
        _lab(3, _T("远端目录"))
        ttk.Entry(form, textvariable=self.var_lan_dir, state="readonly").grid(
            row=3, column=1, sticky="ew", pady=5)

        btns = tk.Frame(parent, bg=self._bg)
        btns.pack(fill="x", padx=6, pady=(8, 2))
        ttk.Button(btns, text=_T("启动服务"), command=self._lan_start_click).pack(side="left")
        ttk.Button(btns, text=_T("停止服务"), command=self._lan_stop_click).pack(side="left", padx=8)
        ttk.Button(btns, text=_T("立即合并"), command=self._lan_merge_click).pack(side="left")
        ttk.Button(btns, text=_T("重新生成配对码"), command=self._lan_regen).pack(side="left", padx=8)

        box = tk.Frame(parent, bg=self._bg)
        box.pack(fill="x", padx=6, pady=(6, 2))
        tk.Label(box, text=_T("服务状态"), font=("微软雅黑", 10, "bold"),
                 bg=self._bg).pack(anchor="w")
        self.lbl_lan = tk.Label(box, text="", font=("微软雅黑", 9), fg="#1A1B1C",
                                bg=self._bg, anchor="w", justify="left", wraplength=780)
        self.lbl_lan.pack(anchor="w", pady=(2, 4))

        tk.Label(box, text=_T("手机端填写以下四项（安卓填写方法见《安卓互通指南》）"),
                 font=("微软雅黑", 10, "bold"), bg=self._bg).pack(anchor="w")
        self.txt_pair = tk.Text(box, height=5, wrap="none", font=("Consolas", 9),
                                relief="flat", borderwidth=0, highlightthickness=1,
                                highlightbackground="#DDE3EC", bg="#FFFFFF", fg="#1A1B1C")
        self.txt_pair.pack(fill="x", pady=(2, 4))
        self.txt_pair.configure(state="disabled")
        tk.Label(box,
                 text=_T("提示：首次开启如弹出防火墙提示，请选择「允许」（专用网络）；"
                         "手机与电脑需在同一局域网。"),
                 font=("微软雅黑", 9), fg="#7A7F87", bg=self._bg,
                 wraplength=780, justify="left").pack(anchor="w")

    # ---------------- 局域网动作 ----------------
    def _lan_state_text(self, st):
        state = st.get("state")
        if state == ST_RUNNING:
            return _T("运行中")
        if state == ST_STARTING:
            return _T("启动中")
        if state == ST_ERROR:
            return _T("出错")
        return _T("已停止")

    def _toggle_lan(self):
        on = bool(self.var_lan_on.get())
        self._collect()
        if on:
            ok, err = self.app._lan_start(quiet=False)
            if not ok:
                self.var_lan_on.set(False)
                self.app.settings["lan_enabled"] = False
            else:
                self.app._lan_schedule_watch()
                self._log(_T("局域网本地同步已开启"))
        else:
            self.app._lan_stop()
            self.app.settings["lan_enabled"] = False
            self._log(_T("局域网本地同步已关闭"))
        self._persist_lan()
        self._refresh_lan()

    def _lan_start_click(self):
        self._collect()
        self._persist_lan()
        ok, err = self.app._lan_start(quiet=False)
        if ok:
            self.app._lan_schedule_watch()
            self._log(_T("服务已启动"))
        else:
            self._log(_T("启动失败：{e}").format(e=err))
        self._refresh_lan()

    def _lan_stop_click(self):
        self.app._lan_stop()
        self._log(_T("服务已停止"))
        self._refresh_lan()

    def _lan_merge_click(self):
        if self._busy:
            return
        self._collect()
        self._persist_lan()
        if not self.app._lan_merge_now(silent=False, on_done=lambda rep: self._on_lan_result(rep)):
            self._status(_T("已有同步任务在进行中"))

    def _on_lan_result(self, report):
        self._busy = False
        if report.get("ok"):
            self._status(_T("局域网合并完成：上传 {p} 本，下载 {d} 本").format(
                p=report.get("pushed", 0), d=report.get("pulled", 0)))
        else:
            self._status(_T("局域网合并失败：{e}").format(e=report.get("error") or ""))

    def _lan_regen(self):
        if not messagebox.askyesno(
                _T("重新生成配对码"),
                _T("重新生成后，手机端需要重新填写新的配对码。继续？")):
            return
        token = new_token()
        self.var_lan_token.set(token)
        self.app.settings["lan_token"] = token
        try:
            self.app.storage.set_setting("lan_token", token)
        except Exception:
            pass
        was_running = self.app._lan_status().get("state") == ST_RUNNING
        self.app._lan_server_obj(recreate=True)
        if was_running or self.var_lan_on.get():
            self.app._lan_start(quiet=False)
        self._log(_T("配对码已更新"))
        self._refresh_lan()

    # ---------------- 状态刷新 ----------------
    def _refresh_lan(self):
        try:
            st = self.app._lan_status()
        except Exception:
            return
        servers = st.get("addresses") or []
        lines = [
            _T("状态：{s}").format(s=self._lan_state_text(st)),
            _T("监听端口：{p}").format(p=st.get("port") or self.var_lan_port.get()),
            _T("运行时长：{t}").format(t=_fmt_uptime(st.get("uptime"))),
            _T("请求数：{n}　活跃连接：{a}").format(n=st.get("requests", 0), a=st.get("active", 0)),
        ]
        if st.get("last_request"):
            lines.append(_T("最近请求：{r}").format(r=st["last_request"]))
        if st.get("last_error"):
            lines.append(_T("最近错误：{e}").format(e=st["last_error"]))
        if st.get("state") == ST_ERROR and st.get("error"):
            lines.append(_T("错误：{e}").format(e=st["error"]))
        try:
            self.lbl_lan.configure(text="\n".join(lines))
        except Exception:
            pass

        try:
            srv = getattr(self.app, "_lan_srv", None)
            info = srv.pairing_info() if srv is not None else None
        except Exception:
            info = None
        if not info:
            port = self.var_lan_port.get() or DEFAULT_PORT
            urls = ["http://<本机IP>:%s/" % port]
            info = {"urls": urls, "user": self.var_lan_user.get(),
                    "pass": self.var_lan_token.get(), "dir": self.var_lan_dir.get()}
        first = info["urls"][0] if info.get("urls") else "http://127.0.0.1/"
        more = info["urls"][1:4]
        block = [
            _T("服务器地址") + "：" + first,
        ]
        if more:
            block.insert(1, _T("其它可用地址") + "：" + "　".join(more))
        block += [
            _T("账号") + "：" + str(info.get("user") or ""),
            _T("密码（配对码）") + "：" + str(info.get("pass") or ""),
            _T("远端目录") + "：" + str(info.get("dir") or ""),
        ]
        try:
            self.txt_pair.configure(state="normal")
            self.txt_pair.delete("1.0", "end")
            self.txt_pair.insert("end", "\n".join(block))
            self.txt_pair.configure(state="disabled")
        except Exception:
            pass

    # ---------------- 数据 ----------------
    def _collect(self):
        s = self.app.settings
        s["sync_server"] = self.var_server.get().strip()
        s["sync_user"] = self.var_user.get().strip()
        s["sync_pass"] = self.var_pass.get()
        s["sync_dir"] = self.var_dir.get().strip().strip("/") or DEFAULT_REMOTE_DIR
        s["sync_device_name"] = self.var_device.get().strip() or default_device_name()
        s["sync_auto"] = bool(self.var_auto.get())
        s["sync_verify_tls"] = bool(self.var_tls.get())
        try:
            port = int(str(self.var_lan_port.get()).strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT
        port = max(1024, min(65535, port))
        s["lan_port"] = port
        s["lan_enabled"] = bool(self.var_lan_on.get())
        s["lan_user"] = self.var_lan_user.get().strip() or DEFAULT_USER
        s["lan_token"] = self.var_lan_token.get().strip()
        s["lan_dir"] = self.var_lan_dir.get().strip().strip("/") or LAN_REMOTE_DIR
        s["lan_auto_merge"] = bool(self.var_lan_merge.get())
        return s

    def _persist_lan(self):
        for k in LAN_SETTING_KEYS:
            try:
                self.app.storage.set_setting(k, self.app.settings.get(k))
            except Exception:
                pass

    def _save(self):
        self._collect()
        for k in ("sync_server", "sync_user", "sync_pass", "sync_dir",
                  "sync_device_name", "sync_auto", "sync_verify_tls"):
            try:
                self.app.storage.set_setting(k, self.app.settings.get(k))
            except Exception:
                pass
        self._persist_lan()
        if self.app.settings.get("sync_auto"):
            try:
                self.app._sync_auto_schedule()
            except Exception:
                pass
        if self.app.settings.get("lan_enabled"):
            self.app._lan_start(quiet=True)
            self.app._lan_schedule_watch()
        else:
            self.app._lan_stop()
        self._status(_T("已保存"))
        self._log(_T("配置已保存"))
        self._refresh_lan()

    def _refresh_last(self):
        last = self.app.storage.get_setting("sync_last_result") or {}
        if not isinstance(last, dict) or not last.get("time"):
            return
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(last.get("time"))))
        ch = _T("局域网") if last.get("channel") == "lan" else _T("云端")
        if last.get("ok"):
            self._status(_T("上次{ch}同步 {t}：↑{p} ↓{d}").format(
                ch=ch, t=t, p=last.get("pushed", 0), d=last.get("pulled", 0)))
        else:
            self._status(_T("上次{ch}同步 {t} 失败：{e}").format(
                ch=ch, t=t, e=last.get("error") or ""))

    # ---------------- 云端动作 ----------------
    def _act(self, kind):
        if self._busy:
            return
        self._collect()
        for k in ("sync_server", "sync_user", "sync_pass", "sync_dir",
                  "sync_device_name", "sync_auto", "sync_verify_tls"):
            try:
                self.app.storage.set_setting(k, self.app.settings.get(k))
            except Exception:
                pass
        if not self.app.settings.get("sync_server"):
            messagebox.showwarning(_T("数据同步"), _T("请先填写服务器地址"))
            return
        self._busy = True
        self.btn_test.configure(state="disabled")
        self.btn_sync.configure(state="disabled")
        self._status(_T("正在连接…") if kind == "test" else _T("正在同步…"))
        engine = SyncEngine(self.app.storage, self.app.settings, log=self.q.put)
        if kind == "test":
            threading.Thread(target=self._test_worker, args=(engine,), daemon=True).start()
        else:
            self._busy = False
            self.app._sync_run_channel("cloud", silent=False,
                                       on_done=lambda rep: self.q.put(dict(rep, kind="sync")))
            self._busy = True

    def _test_worker(self, engine):
        ok, msg = engine.test_connection()
        self.q.put({"kind": "test", "ok": ok, "msg": msg})

    # ---------------- 轮询 ----------------
    def _tick(self):
        try:
            self._drain()
            self._refresh_lan()
        except Exception:
            pass
        try:
            self._poll_id = self.top.after(1000, self._tick)
        except Exception:
            pass

    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, str):
                    self._log(item)
                    continue
                kind = item.get("kind")
                if kind == "test":
                    self._busy = False
                    self.btn_test.configure(state="normal")
                    self.btn_sync.configure(state="normal")
                    if item.get("ok"):
                        self._status(item.get("msg") or _T("连接成功"))
                        self._log(item.get("msg") or "")
                    else:
                        self._status(_T("连接失败：{e}").format(e=item.get("msg")))
                        self._log(_T("连接失败：{e}").format(e=item.get("msg")))
                elif kind == "sync":
                    self._busy = False
                    self.btn_test.configure(state="normal")
                    self.btn_sync.configure(state="normal")
                    for ln in item.get("lines") or []:
                        self._log(ln)
                    if item.get("ok"):
                        self._status(_T("同步完成：上传 {p} 本，下载 {d} 本").format(
                            p=item.get("pushed", 0), d=item.get("pulled", 0)))
                        try:
                            self.app._after_sync_report(item)
                        except Exception:
                            pass
                        for key, text in (
                                ("imported", _T("已从远端新增 {n} 本到书架")),
                                ("removed", _T("已按远端删除 {n} 本")),
                                ("deleted", _T("已上传删除事件 {n} 个"))):
                            n = int(item.get(key, 0))
                            if n:
                                self._log(text.format(n=n))
                        ro = item.get("remote_only") or []
                        if ro:
                            self._log(_T("远端有本机没有的 {n} 本：{names}").format(
                                n=len(ro), names="、".join(ro[:8]) + ("…" if len(ro) > 8 else "")))
                    else:
                        self._status(_T("同步失败：{e}").format(e=item.get("error") or ""))
                        self._log(_T("同步失败：{e}").format(e=item.get("error") or ""))
        except queue.Empty:
            pass
        except Exception:
            pass

    def _status(self, text):
        try:
            self.lbl_status.configure(text=text)
        except Exception:
            pass

    def _log(self, text):
        if not text:
            return
        try:
            ts = time.strftime("%H:%M:%S")
            self.txt.configure(state="normal")
            self.txt.insert("end", "[%s] %s\n" % (ts, text))
            self.txt.see("end")
            self.txt.configure(state="disabled")
        except Exception:
            pass

    def _on_close(self):
        try:
            if self._poll_id:
                self.top.after_cancel(self._poll_id)
                self._poll_id = None
        except Exception:
            pass
        self.top.destroy()
