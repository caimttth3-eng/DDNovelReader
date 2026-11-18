# -*- coding: utf-8 -*-
"""数据同步（WebDAV）：设置入口 + 手动/自动同步（从 gui.py 拆分的 Mixin 之一）。

只同步书架信息、阅读进度、书签；正文解析缓存与整本音频缓存不上传。
远端布局见 novelreader/sync_engine.py。
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .i18n import T as _T
from .constants import UI_THEMES, make_scrollbar
from .sync_engine import (
    SyncEngine,
    ensure_device,
    default_device_name,
    DEFAULT_REMOTE_DIR,
)

AUTO_INTERVAL_MS = 10 * 60 * 1000   # 自动同步间隔：10 分钟


class SyncMixin:
    """设置面板第 6 项「数据同步」：WebDAV 配置 + 手动同步 + 定时自动同步。"""

    # ---------- 入口 ----------
    def _show_sync_dialog(self):
        _SyncDlg(self)

    # ---------- 自动同步 ----------
    def _sync_auto_start(self):
        """启动后调用：开了自动同步且已配置服务器时，后台跑一次并挂上定时器。"""
        try:
            if not self.storage.get_setting("sync_auto", False):
                return
            if not str(self.storage.get_setting("sync_server", "") or "").strip():
                return
            self._sync_run_async(silent=True)
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
                self._sync_run_async(silent=True)
                self._sync_auto_schedule()
        except Exception:
            pass

    def _sync_run_async(self, silent=False, on_done=None):
        """后台线程跑一次双向同步；on_done(report) 在 UI 线程回调。"""
        engine = SyncEngine(self.storage, self.settings)

        def _work():
            report = engine.sync()
            if report.get("imported") or report.get("pulled") or report.get("removed"):
                try:
                    self.root.after(0, self._after_sync_report, report)
                except Exception:
                    pass
            if on_done is not None:
                try:
                    self.root.after(0, lambda: on_done(report))
                except Exception:
                    pass
            elif not silent:
                pass
            self._store_sync_result(report)

        threading.Thread(target=_work, daemon=True).start()

    def _after_sync_report(self, report):
        """同步结果落到界面：刷新书架 + 让打开中的书跟上远端进度。

        不这么做的后果：进度已经写进 library.json，但书架列表和不刷新、
        阅读器还停在旧位置，下一次滚动又会把旧位置写回去 —— 表现为
        「安卓的进度同步不到 PC」。
        """
        if not isinstance(report, dict):
            return
        try:
            self._refresh_bookshelf()
        except Exception:
            pass
        # 对端删掉了正在读的这本书：关掉阅读区，别留一本已不在书架的书
        for bid in (report.get("removed_bids") or []):
            try:
                if bid and bid == getattr(self, "current_bid", None):
                    self.current_bid = None
                    self.book = None
                    self._render_empty()
                    self._flash_status(_T("这本书已在其他设备删除，已从书架移除"))
            except Exception:
                pass
        # 拉到了更新的进度：若正打开同一本书，直接跳到该位置
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
        """把上次同步结果记到 settings（供设置面板回显）。"""
        try:
            self.settings["sync_last_result"] = {
                "ok": bool(report.get("ok")),
                "pushed": int(report.get("pushed", 0)),
                "pulled": int(report.get("pulled", 0)),
                "remote_only": len(report.get("remote_only") or []),
                "imported": int(report.get("imported", 0)),
                "removed": int(report.get("removed", 0)),
                "deleted": int(report.get("deleted", 0)),
                "error": str(report.get("error") or ""),
                "time": round(time.time(), 3),
            }
            self.storage.set_setting("sync_last_result", self.settings["sync_last_result"])
            if report.get("ok"):
                self.settings["sync_last_time"] = time.time()
                self.storage.set_setting("sync_last_time", self.settings["sync_last_time"])
        except Exception:
            pass


class _SyncDlg:
    """数据同步设置弹窗。"""

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(_T("数据同步"))
        self.top.geometry("820x660")
        self.top.minsize(760, 600)
        self.top.transient(app.root)
        app._center_window(self.top)
        self._bg = UI_THEMES.get(
            app.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        self.top.configure(bg=self._bg)

        self.q = queue.Queue()
        self._busy = False
        ensure_device(app.storage, app.settings)

        self.var_server = tk.StringVar(value=str(app.settings.get("sync_server") or ""))
        self.var_user = tk.StringVar(value=str(app.settings.get("sync_user") or ""))
        self.var_pass = tk.StringVar(value=str(app.settings.get("sync_pass") or ""))
        self.var_dir = tk.StringVar(value=str(app.settings.get("sync_dir") or DEFAULT_REMOTE_DIR))
        self.var_device = tk.StringVar(
            value=str(app.settings.get("sync_device_name") or default_device_name()))
        self.var_auto = tk.BooleanVar(value=bool(app.settings.get("sync_auto", False)))
        self.var_tls = tk.BooleanVar(value=bool(app.settings.get("sync_verify_tls", True)))

        self._build()
        self._refresh_last()
        self._poll()
        self.top.focus_set()

    # ---------- 界面 ----------
    def _build(self):
        pad = {"padx": 18}
        head = tk.Frame(self.top, bg=self._bg)
        head.pack(fill="x", **pad, pady=(16, 6))
        tk.Label(head, text=_T("WebDAV 数据同步"), font=("微软雅黑", 13, "bold"),
                 bg=self._bg).pack(anchor="w")
        tk.Label(
            head,
            text=_T("同步书架信息、阅读进度与书签；不同步正文缓存、音频缓存与源文件"),
            font=("微软雅黑", 9), fg="#7A7F87", bg=self._bg,
            wraplength=760, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        form = tk.Frame(self.top, bg=self._bg)
        form.pack(fill="x", **pad, pady=(10, 4))
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

        opts = tk.Frame(self.top, bg=self._bg)
        opts.pack(fill="x", **pad, pady=(2, 4))
        ttk.Checkbutton(opts, text=_T("启动时自动同步（每 10 分钟一次）"),
                        variable=self.var_auto).pack(anchor="w", pady=2)
        ttk.Checkbutton(opts, text=_T("校验 HTTPS 证书（自建服务器用自签名证书时取消勾选）"),
                        variable=self.var_tls).pack(anchor="w", pady=2)

        btns = tk.Frame(self.top, bg=self._bg)
        btns.pack(fill="x", **pad, pady=(8, 4))
        self.btn_test = ttk.Button(btns, text=_T("测试连接"), command=lambda: self._act("test"))
        self.btn_test.pack(side="left")
        self.btn_sync = ttk.Button(btns, text=_T("立即同步"), command=lambda: self._act("sync"))
        self.btn_sync.pack(side="left", padx=8)
        ttk.Button(btns, text=_T("保存"), command=self._save).pack(side="left")
        ttk.Button(btns, text=_T("关闭"), command=self.top.destroy).pack(side="right")

        self.lbl_status = tk.Label(
            self.top, text=_T("未同步"), font=("微软雅黑", 10), fg="#1A1B1C",
            bg=self._bg, anchor="w", justify="left",
        )
        self.lbl_status.pack(fill="x", **pad, pady=(8, 4))

        logbox = tk.Frame(self.top, bg=self._bg)
        logbox.pack(fill="both", expand=True, **pad, pady=(0, 14))
        sb = make_scrollbar(logbox, None)
        sb.pack(side="right", fill="y")
        self.txt = tk.Text(
            logbox, height=8, wrap="word", font=("微软雅黑", 9),
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground="#DDE3EC", bg="#FFFFFF", fg="#3C4043",
            yscrollcommand=sb.set,
        )
        self.txt.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.txt.yview)
        self.txt.configure(state="disabled")

    # ---------- 数据 ----------
    def _collect(self):
        """把界面上的输入写回 settings（不落盘，由 _save 决定）。"""
        s = self.app.settings
        s["sync_server"] = self.var_server.get().strip()
        s["sync_user"] = self.var_user.get().strip()
        s["sync_pass"] = self.var_pass.get()
        s["sync_dir"] = self.var_dir.get().strip().strip("/") or DEFAULT_REMOTE_DIR
        s["sync_device_name"] = self.var_device.get().strip() or default_device_name()
        s["sync_auto"] = bool(self.var_auto.get())
        s["sync_verify_tls"] = bool(self.var_tls.get())
        return s

    def _save(self):
        self._collect()
        for k in ("sync_server", "sync_user", "sync_pass", "sync_dir",
                  "sync_device_name", "sync_auto", "sync_verify_tls"):
            try:
                self.app.storage.set_setting(k, self.app.settings.get(k))
            except Exception:
                pass
        if self.app.settings.get("sync_auto"):
            try:
                self.app._sync_auto_schedule()
            except Exception:
                pass
        self._status(_T("已保存"))
        self._log(_T("配置已保存"))

    def _refresh_last(self):
        last = self.app.storage.get_setting("sync_last_result") or {}
        if not isinstance(last, dict) or not last.get("time"):
            return
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(last.get("time"))))
        if last.get("ok"):
            self._status(
                _T("上次同步 {t}：↑{p} ↓{d}").format(t=t, p=last.get("pushed", 0), d=last.get("pulled", 0))
            )
        else:
            self._status(_T("上次同步 {t} 失败：{e}").format(t=t, e=last.get("error") or ""))

    # ---------- 动作 ----------
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
            threading.Thread(target=self._sync_worker, args=(engine,), daemon=True).start()

    def _test_worker(self, engine):
        ok, msg = engine.test_connection()
        self.q.put({"kind": "test", "ok": ok, "msg": msg})

    def _sync_worker(self, engine):
        report = engine.sync()
        report["kind"] = "sync"
        self.q.put(report)

    def _poll(self):
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
                    self.app._store_sync_result(item)
                    for ln in item.get("lines") or []:
                        self._log(ln)
                    if item.get("ok"):
                        self._status(
                            _T("同步完成：上传 {p} 本，下载 {d} 本").format(
                                p=item.get("pushed", 0), d=item.get("pulled", 0))
                        )
                        try:
                            self.app._after_sync_report(item)
                        except Exception:
                            # 界面刷新失败不影响同步本身的结果
                            pass
                        im = int(item.get("imported", 0))
                        if im:
                            self._log(_T("已从远端新增 {n} 本到书架").format(n=im))
                        rm = int(item.get("removed", 0))
                        if rm:
                            self._log(_T("已按远端删除 {n} 本").format(n=rm))
                        dl = int(item.get("deleted", 0))
                        if dl:
                            self._log(_T("已上传删除事件 {n} 个").format(n=dl))
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
        try:
            self.top.after(120, self._poll)
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
