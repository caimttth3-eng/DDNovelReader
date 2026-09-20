# -*- coding: utf-8 -*-
"""语音引擎设置（从主设置菜单进入）。

内置 Edge / 系统语音为默认；额外提供「外部 TTS 服务」接入：
VOICEVOX / vits-simple-api（含 GPT-SoVITS）/ Google TTS / 火山引擎 /
OpenAI 兼容 / 自定义 HTTP。软件本身不安装这些引擎，只探测用户已在
本地运行的服务并调用；外部引擎仅支持实时朗读，不支持整本语音缓存。
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.parse

from .i18n import T as _T
from .constants import UI_THEMES, make_scrollbar
from .tts_engine import list_voicevox_speakers, list_vits_speakers


# 引擎顺序与显示名（运行时再 _T）
_ENGINES = [
    ("builtin", "默认（Edge 在线 + Windows 系统语音）"),
    ("voicevox", "VOICEVOX（本地，免费日文 TTS）"),
    ("vits", "vits-simple-api（本地音色克隆）"),
    ("google", "Google TTS（在线，免 Key）"),
    ("volcano", "火山引擎 TTS（需 API Key）"),
    ("openai", "OpenAI 兼容 TTS（需 API Key）"),
    ("custom", "自定义 HTTP TTS"),
    ("gptsovits", "GPT-SoVITS（本地自训练音色）"),
]

_GOOGLE_LANGS = [
    ("zh-CN", "中文（普通话）"),
    ("zh-TW", "中文（台湾）"),
    ("zh-HK", "中文（粤语）"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
]


class _EngineDlg:
    """语音引擎设置弹窗（由 EngineMixin._show_engines_dialog 创建）。"""

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(_T("语音引擎"))
        self.top.geometry("1280x720")
        self.top.minsize(1100, 640)
        self.top.transient(app.root)
        app._center_window(self.top)
        self._bg = UI_THEMES.get(
            app.settings.get("ui_theme", "D·原生微调"), UI_THEMES["D·原生微调"])["bg"]
        self.top.configure(bg=self._bg)

        self.cfg = dict(app.settings.get("tts_engine_cfg") or {})
        self._rows = {}        # kind -> (Entry/StringVar 字典)
        self._vits_speakers = []
        self._vv_speakers = []

        self._build_top()
        self._build_body()
        self._build_bottom()
        self._select_kind(self.cfg.get("kind", "builtin"))
        self.top.focus_set()

    # ---------- 顶部说明 ----------
    def _build_top(self):
        wrap = tk.Frame(self.top, bg=self._bg)
        wrap.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(wrap, justify="left", wraplength=620, bg=self._bg, fg="#555555",
                 font=("微软雅黑", 9),
                 text=_T("默认使用内置 Edge 在线语音 + 系统本地语音。以下引擎需要你自行安装对应软件/服务后，软件会自动调用——软件本身不附带它们。外部引擎也支持整本语音缓存下载（首次缓存需联网/启动对应服务）。"),
                 ).pack(anchor="w")

    # ---------- 主体：左列表 + 右配置 ----------
    def _build_body(self):
        body = tk.Frame(self.top, bg=self._bg)
        body.pack(fill="both", expand=True, padx=16, pady=6)

        left = tk.Frame(body, bg=self._bg)
        left.pack(side="left", fill="y")
        self.engine_lb = tk.Listbox(left, width=38, height=16, activestyle="dotbox",
                                    font=("微软雅黑", 10), exportselection=False)
        sb = make_scrollbar(left, self.engine_lb.yview)
        self.engine_lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.engine_lb.pack(side="left", fill="y")
        for _, label in _ENGINES:
            self.engine_lb.insert("end", _T(label))
        self.engine_lb.bind("<<ListboxSelect>>", self._on_select)

        self.right = tk.Frame(body, bg="#FFFFFF",
                              highlightbackground="#DDE3EC", highlightthickness=1)
        self.right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # 右侧可滚动：内容超出高度时出现滚动条
        self._cfg_canvas = tk.Canvas(self.right, bg="#FFFFFF", highlightthickness=0)
        sb_r = make_scrollbar(self.right, self._cfg_canvas.yview)
        sb_r.pack(side="right", fill="y")
        self._cfg_canvas.pack(side="left", fill="both", expand=True)
        self._cfg_canvas.configure(yscrollcommand=sb_r.set)

        self._cfg_inner = tk.Frame(self._cfg_canvas, bg="#FFFFFF")
        self._cfg_win = self._cfg_canvas.create_window(
            (0, 0), window=self._cfg_inner, anchor="nw")

        def _on_inner_config(_e):
            self._cfg_canvas.configure(scrollregion=self._cfg_canvas.bbox("all"))
        self._cfg_inner.bind("<Configure>", _on_inner_config)

        def _on_canvas_config(e):
            self._cfg_canvas.itemconfigure(self._cfg_win, width=e.width)
        self._cfg_canvas.bind("<Configure>", _on_canvas_config)

        # 鼠标滚轮只在悬停右侧时生效
        def _on_enter(_e):
            self._cfg_canvas.bind_all("<MouseWheel>", _on_wheel)
        def _on_leave(_e):
            self._cfg_canvas.unbind_all("<MouseWheel>")
        def _on_wheel(e):
            self._cfg_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self._cfg_canvas.bind("<Enter>", _on_enter)
        self._cfg_canvas.bind("<Leave>", _on_leave)

    # ---------- 底部按钮 ----------
    def _build_bottom(self):
        bar = tk.Frame(self.top, bg=self._bg)
        bar.pack(fill="x", padx=16, pady=(0, 12))
        tk.Label(bar, fg="#b26a00", bg=self._bg, font=("微软雅黑", 9),
                 text=_T("⚠ 外部引擎仅支持实时朗读，不支持整本语音缓存")).pack(side="left")
        ttk.Button(bar, text=_T("取消"), command=self.top.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(bar, text=_T("保存并使用"), command=self._on_save).pack(side="right")

    # ---------- 配置区切换 ----------
    def _clear_right(self):
        for w in self._cfg_inner.winfo_children():
            w.destroy()
        self._rows = {}

    def _row(self, parent, label, value=""):
        tk.Label(parent, text=label, bg="#FFFFFF", font=("微软雅黑", 9)).pack(anchor="w", pady=(8, 2))
        var = tk.StringVar(value=value)
        tk.Entry(parent, textvariable=var, width=52, font=("微软雅黑", 10)).pack(anchor="w", fill="x")
        return var

    def _note(self, parent, lines):
        tk.Label(parent, text=lines, bg="#FFFFFF", fg="#8a8a8a", justify="left",
                 wraplength=440, font=("微软雅黑", 8)).pack(anchor="w", pady=(10, 0))

    def _proxy_row(self, parent, c):
        """每个引擎独立的代理开关：勾选后填地址才生效，留空则直连。"""
        use = tk.BooleanVar(value=bool(c.get("proxy")))
        addr = tk.StringVar(value=c.get("proxy", ""))
        row = tk.Frame(parent, bg="#FFFFFF")
        row.pack(anchor="w", pady=(10, 0))

        def _toggle():
            e.configure(state="normal" if use.get() else "disabled")

        tk.Checkbutton(row, variable=use, bg="#FFFFFF",
                       text=_T("使用代理（海外引擎需要）"),
                       command=_toggle, font=("微软雅黑", 9)).pack(side="left")
        e = tk.Entry(row, textvariable=addr, width=22, font=("微软雅黑", 9))
        e.pack(side="left", padx=(8, 0))
        tk.Label(row, text="http://127.0.0.1:7890", bg="#FFFFFF",
                 fg="#aaaaaa", font=("微软雅黑", 8)).pack(side="left", padx=(4, 0))
        if not use.get():
            e.configure(state="disabled")
        self._rows["proxy_use"] = use
        self._rows["proxy_addr"] = addr

    def _on_select(self, _e=None):
        sel = self.engine_lb.curselection()
        if not sel:
            return
        self._select_kind(_ENGINES[sel[0]][0])

    def _select_kind(self, kind):
        for i, (k, _l) in enumerate(_ENGINES):
            if k == kind:
                self.engine_lb.selection_clear(0, "end")
                self.engine_lb.selection_set(i)
                self.engine_lb.activate(i)
                break
        self._clear_right()
        inner = self._cfg_inner
        c = self.cfg.get(kind, {}) or {}

        if kind == "builtin":
            self._note(inner, _T("内置引擎：Edge 神经语音（联网免费、音色自然）+ Windows 系统语音（离线）。\n在主界面音色下拉直接切换即可。"))
            return

        if kind == "voicevox":
            self._rows["base"] = self._row(inner, _T("服务地址"), c.get("base", "http://127.0.0.1:50021"))
            self._rows["speaker_name"] = tk.StringVar()
            row = tk.Frame(inner, bg="#FFFFFF")
            row.pack(fill="x", pady=(8, 2))
            self.speaker_cb = ttk.Combobox(row, state="readonly", width=34)
            self.speaker_cb.pack(side="left")
            ttk.Button(row, text=_T("探测音色"), command=lambda: self._probe("voicevox")).pack(side="left", padx=6)
            self._note(inner, _T(
                "安装教程：\n1. 到 https://voicevox.hiroshiba.jp 下载安装并启动（出现托盘图标即运行中）\n"
                "2. 点「探测音色」，从下拉里选一个\n"
                "3. 主要是日文音色；选好后到主界面点朗读即可试听"))
            self._proxy_row(inner, c)
            if c.get("speaker_id"):
                self.speaker_cb["values"] = [c.get("speaker_name", "")]
                self.speaker_cb.set(c.get("speaker_name", ""))
                self._vv_speakers = [(c.get("speaker_name", ""), c.get("speaker_id", ""))]
            return

        if kind == "vits":
            self._rows["base"] = self._row(inner, _T("服务地址"), c.get("base", "http://127.0.0.1:9880"))
            self._rows["lang"] = self._row(inner, _T("语言（zh/en/ja/ko）"), c.get("lang", "zh"))
            row = tk.Frame(inner, bg="#FFFFFF")
            row.pack(fill="x", pady=(8, 2))
            self.speaker_cb = ttk.Combobox(row, state="readonly", width=34)
            self.speaker_cb.pack(side="left")
            ttk.Button(row, text=_T("探测音色"), command=lambda: self._probe("vits")).pack(side="left", padx=6)
            self._note(inner, _T(
                "安装教程：\n1. 安装 vits-simple-api（按作者 README，通常 pip 安装后 python app.py 启动，默认端口 9880）\n"
                "2. 点「探测音色」选择你已下载的克隆音色\n"
                "3. 兼容 GPT-SoVITS 等模型；仅实时朗读"))
            self._proxy_row(inner, c)
            if c.get("speaker_id"):
                self.speaker_cb["values"] = [c.get("speaker_name", "")]
                self.speaker_cb.set(c.get("speaker_name", ""))
                self._vits_speakers = [(c.get("speaker_name", ""), c.get("speaker_id", ""))]
            return

        if kind == "google":
            self._rows["lang_var"] = tk.StringVar(value=c.get("lang", "zh-CN"))
            tk.Label(inner, text=_T("语言"), bg="#FFFFFF", font=("微软雅黑", 9)).pack(anchor="w", pady=(8, 2))
            self.lang_cb = ttk.Combobox(inner, state="readonly", width=30)
            self.lang_cb["values"] = [f"{n} ({k})" for k, n in _GOOGLE_LANGS]
            cur = next((f"{n} ({k})" for k, n in _GOOGLE_LANGS if k == c.get("lang", "zh-CN")), None)
            if cur:
                self.lang_cb.set(cur)
            self.lang_cb.pack(anchor="w")
            self._proxy_row(inner, c)
            self._note(inner, _T(
                "免费、免安装、免 Key。需联网；国内访问 Google 需要代理。\n"
                "音质偏机械，仅作备用。"))
            return

        if kind == "volcano":
            self._rows["appid"] = self._row(inner, _T("AppID"), c.get("appid", ""))
            self._rows["token"] = self._row(inner, _T("Access Token"), c.get("token", ""))
            self._rows["voice"] = self._row(inner, _T("音色 ID（如 zh_female_shuangkuaisisi_moon_bigtts）"), c.get("voice", ""))
            self._rows["cluster"] = self._row(inner, _T("Cluster"), c.get("cluster", "volcano_tts"))
            self._proxy_row(inner, c)
            self._note(inner, _T(
                "安装教程：到火山引擎控制台开通「语音合成大模型」，创建应用拿到 AppID / Access Token，\n"
                "在音色广场选一个音色 ID 填到上面。"))
            return

        if kind == "openai":
            self._rows["base"] = self._row(inner, _T("Base URL"), c.get("base", "https://api.openai.com/v1"))
            self._rows["key"] = self._row(inner, _T("API Key"), c.get("key", ""))
            self._rows["model"] = self._row(inner, _T("模型"), c.get("model", "tts-1"))
            self._rows["voice"] = self._row(inner, _T("音色（alloy/echo/…）"), c.get("voice", "alloy"))
            self._proxy_row(inner, c)
            self._note(inner, _T("任何兼容 OpenAI /audio/speech 接口的服务都可填这里（含国内中转）。"))
            return

        if kind == "custom":
            self._rows["url"] = self._row(inner, _T("POST 地址"), c.get("url", ""))
            self._rows["voice"] = self._row(inner, _T("音色参数值（POST 表单 voice 字段）"), c.get("voice", ""))
            self._proxy_row(inner, c)
            self._note(inner, _T(
                "你的服务需接受 POST 表单 text=…&voice=…，响应体直接返回音频（mp3/wav）。"))
            return

        if kind == "gptsovits":
            self._rows["base"] = self._row(
                inner, _T("服务地址（一般不用改）"), c.get("base", "http://127.0.0.1:9880"))
            # 音色名不手填，选模型文件时自动从文件名提取
            self._rows["voice"] = tk.StringVar(value=c.get("voice", ""))

            def _pick(title, exts, key, initialdir=None):
                from tkinter import filedialog
                kw = dict(title=title, filetypes=exts)
                if initialdir:
                    kw["initialdir"] = initialdir
                fn = filedialog.askopenfilename(**kw)
                if fn:
                    self._rows[key].delete(0, "end")
                    self._rows[key].insert(0, fn)
                    # 选模型文件时自动提取音色名（文件名去掉 -e数字.ckpt 等后缀）
                    if key in ("gpt", "sovits"):
                        base = os.path.splitext(os.path.basename(fn))[0]
                        name = re.sub(r"[-_][evsEVS]\d+.*$", "", base)
                        if name and not self._rows["voice"].get():
                            self._rows["voice"].set(name)

            def _path_row(label, val, exts, key):
                tk.Label(inner, text=label, bg="#FFFFFF",
                         font=("微软雅黑", 9)).pack(anchor="w", pady=(6, 2))
                fr = tk.Frame(inner, bg="#FFFFFF")
                fr.pack(fill="x", anchor="w")
                e = tk.Entry(fr, width=46, font=("微软雅黑", 10))
                e.pack(side="left", padx=(0, 6))
                e.insert(0, val)
                tk.Button(fr, text=_T("浏览…"),
                          command=lambda: _pick(label, exts, key),
                          font=("微软雅黑", 9)).pack(side="left")
                return e

            self._rows["gpt"] = _path_row(
                _T("音色模型文件①（.ckpt，在 GPT-SoVITS 文件夹的 GPT_weights_v2Pro 里）"),
                c.get("gpt", ""),
                [("GPT 模型", "*.ckpt")], "gpt")
            self._rows["sovits"] = _path_row(
                _T("音色模型文件②（.pth，在 SoVITS_weights_v2Pro 里）"),
                c.get("sovits", ""),
                [("SoVITS 模型", "*.pth")], "sovits")
            self._rows["ref"] = _path_row(
                _T("一段参考语音（在 GPT-SoVITS\logs\你起的音色名\5-wav32k 里挑一段）"),
                c.get("ref", ""),
                [("音频", "*.wav *.mp3")], "ref")
            self._rows["pt"] = self._row(
                inner, _T("这段参考语音里说的原话（一字不差填进去，音色才像）"),
                c.get("pt", ""))
            self._rows["speed"] = self._row(
                inner, _T("语速（1.0 = 正常，0.5 慢一倍，2.0 快一倍）"),
                c.get("speed", "1.0"))

            def _copy_cmd():
                cmd = "runtime\python.exe api.py -p 9880"
                self.top.clipboard_clear()
                self.top.clipboard_append(cmd)
                from tkinter import messagebox
                messagebox.showinfo(_T("已复制"), _T("启动命令已复制，去 GPT-SoVITS 文件夹地址栏输入 cmd 回车后粘贴即可"))

            btn_fr = tk.Frame(inner, bg="#FFFFFF")
            btn_fr.pack(anchor="w", pady=(8, 0))
            ttk.Button(btn_fr, text=_T("复制启动命令"), command=_copy_cmd).pack(side="left")
            tk.Label(btn_fr, text="  runtime\python.exe api.py -p 9880",
                     bg="#FFFFFF", fg="#888888", font=("Consolas", 9)).pack(side="left")

            self._note(inner, _T(
                "怎么用（第一次照做，以后不用再看）：\n"
                "① 先启动语音服务：打开你的 GPT-SoVITS 文件夹，在地址栏输入 cmd 回车，\n"
                "   弹出黑窗口后输入 runtime\python.exe api.py -p 9880 回车，\n"
                "   等出现 Uvicorn running on http://127.0.0.1:9880 就好了，黑窗口别关。\n"
                "② 填音色名（你训练时起的名字）。\n"
                "③ 用浏览按钮选两个模型文件：.ckpt 在 GPT_weights_v2Pro 文件夹，.pth 在 SoVITS_weights_v2Pro。\n"
                "④ 用浏览按钮选一段参考语音：在 logs\你起的音色名\5-wav32k 里挑一段清晰的。\n"
                "⑤ 把这段语音里说的原话一字不差填到最下面那栏。\n"
                "⑥ 点保存并使用，就能用你自己训练的音色朗读了。\n"
                "这是本地服务，不用联网也不用代理。外部音色也支持整本缓存。\n"
                "注意：每次开机想用这个音色，都要先做第①步启动服务。"))
            return

        if kind == "custom":
            self._rows["url"] = self._row(inner, _T("POST 地址"), c.get("url", ""))
            self._rows["voice"] = self._row(inner, _T("音色参数值（POST 表单 voice 字段）"), c.get("voice", ""))
            self._proxy_row(inner, c)
            self._note(inner, _T(
                "你的服务需接受 POST 表单 text=…&voice=…，响应体直接返回音频（mp3/wav）。"))
            return




    # ---------- 探测音色 ----------
    def _probe(self, kind):
        base = self._rows["base"].get().strip().rstrip("/")
        if not base:
            messagebox.showinfo(_T("提示"), _T("先填服务地址"))
            return
        messagebox.showinfo(_T("提示"), _T("正在探测…"))

        def work():
            try:
                _u = self._rows.get("proxy_use")
                _a = self._rows.get("proxy_addr")
                proxy = None
                if _u is not None and _u.get() and _a is not None and _a.get().strip():
                    proxy = _a.get().strip()
                if kind == "voicevox":
                    sps = list_voicevox_speakers(base, proxy=proxy)
                else:
                    sps = list_vits_speakers(base, proxy=proxy)
            except Exception as e:
                self.top.after(0, lambda: messagebox.showerror(_T("探测失败"), str(e)))
                return

            def done():
                if kind == "voicevox":
                    self._vv_speakers = sps
                else:
                    self._vits_speakers = sps
                self.speaker_cb["values"] = [n for n, _i in sps]
                if sps:
                    self.speaker_cb.current(0)
                else:
                    messagebox.showinfo(_T("提示"), _T("没有探测到音色"))
            self.top.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    # ---------- 保存 ----------
    def _current_kind(self):
        sel = self.engine_lb.curselection()
        return _ENGINES[sel[0]][0] if sel else "builtin"

    def _on_save(self):
        kind = self._current_kind()
        s = self.app.settings
        eng_cfg = dict(s.get("tts_engine_cfg") or {})

        if kind == "builtin":
            # 恢复内置音色：保留各引擎已填配置，只切换 enabled/kind
            cfg = dict(eng_cfg)
            cfg["enabled"] = False
            cfg["kind"] = "builtin"
            s["tts_engine_cfg"] = cfg
            s["tts_voice"] = eng_cfg.get("builtin_voice", s.get("tts_voice", ""))
            self.app.storage.set_setting("tts_engine_cfg", s["tts_engine_cfg"])
            self.app.storage.set_setting("tts_voice", s["tts_voice"])
            self.app.tts.set_voice(s["tts_voice"])
            self.top.destroy()
            self._refresh_voice_dropdown()
            return

        # 备份当前内置 voice（仅在尚未备份时）
        if not eng_cfg.get("builtin_voice"):
            eng_cfg["builtin_voice"] = s.get("tts_voice", "")

        payload = {}
        label = ""
        if kind == "voicevox":
            base = self._rows["base"].get().strip()
            names = self.speaker_cb.get()
            sid = next((i for n, i in self._vv_speakers if n == names), "")
            if not sid:
                messagebox.showinfo(_T("提示"), _T("请先点「探测音色」并选一个"))
                return
            payload = {"base": base, "speaker": sid,
                       "speaker_id": sid, "speaker_name": names}
            label = f"VOICEVOX·{names}"
        elif kind == "vits":
            base = self._rows["base"].get().strip()
            lang = self._rows["lang"].get().strip() or "zh"
            names = self.speaker_cb.get()
            sid = next((i for n, i in self._vits_speakers if n == names), "")
            if not sid:
                messagebox.showinfo(_T("提示"), _T("请先点「探测音色」并选一个"))
                return
            payload = {"base": base, "id": sid, "lang": lang,
                       "speaker_id": sid, "speaker_name": names}
            label = f"vits·{names}"
        elif kind == "google":
            sel_txt = self.lang_cb.get()
            lang = next((k for k, n in _GOOGLE_LANGS if sel_txt.startswith(n)), "zh-CN")
            payload = {"lang": lang}
            label = f"Google·{lang}"
        elif kind == "volcano":
            for key in ("appid", "token", "voice"):
                if not self._rows[key].get().strip():
                    messagebox.showinfo(_T("提示"), _T("请完整填写火山引擎 AppID / Token / 音色 ID"))
                    return
            payload = {k: self._rows[k].get().strip() for k in ("appid", "token", "voice", "cluster")}
            label = "火山TTS"
        elif kind == "openai":
            for key in ("base", "key"):
                if not self._rows[key].get().strip():
                    messagebox.showinfo(_T("提示"), _T("请填写 Base URL 和 API Key"))
                    return
            payload = {k: self._rows[k].get().strip() for k in ("base", "key", "model", "voice")}
            label = "OpenAI·TTS"
        elif kind == "gptsovits":
            payload = {k: self._rows[k].get().strip()
                       for k in ("base", "gpt", "sovits", "ref", "pt", "speed")}
            payload["pl"] = "zh"
            payload["tl"] = "zh"
            label = "GPT-SoVITS·" + self._rows["voice"].get()

        elif kind == "custom":
            if not self._rows["url"].get().strip():
                messagebox.showinfo(_T("提示"), _T("请填写服务地址"))
                return
            payload = {"url": self._rows["url"].get().strip(),
                       "voice": self._rows["voice"].get().strip()}
            label = "自定义TTS"

        _up = self._rows.get("proxy_use")
        _ap = self._rows.get("proxy_addr")
        if _up is not None and _up.get() and _ap is not None and _ap.get().strip():
            payload["proxy"] = _ap.get().strip()
        voice_id = "ext:" + kind + ":" + urllib.parse.urlencode(payload)
        cfg = dict(eng_cfg)
        cfg["enabled"] = True
        cfg["kind"] = kind
        cfg["builtin_voice"] = eng_cfg.get("builtin_voice", "")
        cfg["label"] = label
        cfg["voice_id"] = voice_id
        cfg[kind] = payload
        s["tts_engine_cfg"] = cfg
        s["tts_voice"] = voice_id
        self.app.storage.set_setting("tts_engine_cfg", s["tts_engine_cfg"])
        self.app.storage.set_setting("tts_voice", voice_id)
        self.app.tts.set_voice(voice_id)
        self.top.destroy()
        self._refresh_voice_dropdown()

    def _refresh_voice_dropdown(self):
        try:
            names = self.app._friendly_voices()
            self.app.voice_cb["values"] = names
            vid = self.app.settings.get("tts_voice", "")
            try:
                idx = self.app._voice_ids.index(vid)
                self.app.voice_cb.current(idx)
            except ValueError:
                self.app.voice_cb.current(0)
        except Exception:
            pass


class EngineMixin:
    """语音引擎设置入口（设置菜单里的「语音引擎」）。"""
    def _show_engines_dialog(self):
        _EngineDlg(self)
