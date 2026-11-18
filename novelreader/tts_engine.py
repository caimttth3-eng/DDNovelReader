# -*- coding: utf-8 -*-
"""TTS 朗读引擎（v3）。

v3 新增双后端：
- backend="sapi"：Windows 系统语音（pyttsx3/SAPI5），离线可用，音质一般；
- backend="edge"：Edge 神经语音（edge-tts，免费、无需密钥、音质接近豆包级），
  朗读时需联网。通过 set_voice 传入的语音 ID 自动识别后端。

架构（v2 起保持不变）：
- 全程只有一个常驻工作线程，语音引擎/音频只在该线程内使用；
- GUI 线程只通过状态标志（idle/playing/paused）+ 条件变量通信；
- 朗读按句推进；暂停/停止由工作线程内打断；
- edge 后端用「预取下一句」隐藏网络生成延迟，句间不卡顿；
- edge 生成失败自动回退到系统语音。
"""
import os
import json
import hashlib
import subprocess

import queue
import re
import tempfile
import threading
import urllib.parse
import urllib.request
import time
import sys

# 跨端稳定命名：缓存一级目录用 content_key，语速目录用合成百分比。
# 实现只在 content_key.py 一份（WebDAV 同步的 content_key 与它同源）。
from .content_key import (
    compute_content_key,
    rate_dir_key,
    content_key_of_chapters,
    content_key_of_book,
)

# 惰性导入：pyttsx3 / edge_tts / pycaw 都是"用到才需要"的重依赖
# （edge_tts 含 aiohttp 实测 438ms、pycaw 131ms，pyttsx3 走 COM 也不轻）。
# 启动路径只建 SpeechController，不朗读也不出声，因此一律推迟到首次使用时导入。
# PyInstaller 仍会静态收集这些包（import 语句在函数体内同样被分析）。
pyttsx3 = None
edge_tts = None
_HAS_PYCAW = None  # None=未探测 / True / False
_AudioUtilities = None
_ISimpleAudioVolume = None


def _load_pyttsx3():
    global pyttsx3
    if pyttsx3 is None:
        try:
            import pyttsx3 as _m
        except Exception:
            return None
        pyttsx3 = _m
    return pyttsx3


def _load_edge_tts():
    global edge_tts
    if edge_tts is None:
        try:
            import edge_tts as _m
        except Exception:
            return None
        edge_tts = _m
    return edge_tts


def _load_pycaw():
    """探测 pycaw（Windows 进程音量）。返回 (可用?, AudioUtilities, ISimpleAudioVolume)。"""
    global _HAS_PYCAW, _AudioUtilities, _ISimpleAudioVolume
    if _HAS_PYCAW is None:
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
            _AudioUtilities = AudioUtilities
            _ISimpleAudioVolume = ISimpleAudioVolume
            _HAS_PYCAW = True
        except Exception:
            _HAS_PYCAW = False
    return _HAS_PYCAW, _AudioUtilities, _ISimpleAudioVolume

import ctypes
from ctypes import wintypes

from .textproc import clean_to_orig, orig_to_clean
from .audio_bus import AudioBus, SessionClock

_SENT_END = re.compile(r"(?<=[。！？!?；;])")

_MAX_CHUNK = 160

# 免费优质中文 Edge 语音（音色自然，接近豆包级）
EDGE_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓·女声·温柔"),
    ("zh-CN-XiaoyiNeural", "晓伊·女声·活泼"),
    ("zh-CN-YunxiNeural", "云希·男声·阳光"),
    ("zh-CN-YunjianNeural", "云健·男声·沉稳"),
    ("zh-CN-YunyangNeural", "云扬·男声·新闻"),
    ("zh-CN-YunxiaNeural", "云夏·童声"),
    ("zh-TW-HsiaoChenNeural", "晓臻·女声·台湾腔"),
    ("zh-HK-HiuMaanNeural", "晓曼·女声·粤语"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北·女声·东北腔"),
]

# 免费优质英文 Edge 语音（美音 + 英音，英文界面默认备选）
EDGE_VOICES_EN = [
    # 美音 US
    ("en-US-AriaNeural", "Aria · Female · Natural (US)"),
    ("en-US-JennyNeural", "Jenny · Female · Friendly (US)"),
    ("en-US-MichelleNeural", "Michelle · Female · Warm (US)"),
    ("en-US-GuyNeural", "Guy · Male · Calm (US)"),
    ("en-US-EricNeural", "Eric · Male · Calm (US)"),
    ("en-US-DavisNeural", "Davis · Male · Young (US)"),
    ("en-US-ChristopherNeural", "Christopher · Male · Deep (US)"),
    ("en-US-SteffanNeural", "Steffan · Male · Young (US)"),
    # 英音 UK
    ("en-GB-SoniaNeural", "Sonia · Female · British · Elegant (UK)"),
    ("en-GB-LibbyNeural", "Libby · Female · British · Warm (UK)"),
    ("en-GB-MaisieNeural", "Maisie · Female · British · Cheerful (UK)"),
    ("en-GB-RyanNeural", "Ryan · Male · British (UK)"),
    ("en-GB-ThomasNeural", "Thomas · Male · British (UK)"),
]

# 日语 Edge 语音
EDGE_VOICES_JA = [
    ("ja-JP-NanamiNeural", "Nanami · Female · Natural (JP)"),
    ("ja-JP-MayuNeural", "Mayu · Female · Cheerful (JP)"),
    ("ja-JP-ShioriNeural", "Shiori · Female · Soft (JP)"),
    ("ja-JP-KeitaNeural", "Keita · Male · Natural (JP)"),
    ("ja-JP-DaichiNeural", "Daichi · Male · Deep (JP)"),
]

# 韩语 Edge 语音
EDGE_VOICES_KO = [
    ("ko-KR-SunHiNeural", "SunHi · Female · Warm (KR)"),
    ("ko-KR-JiMinNeural", "JiMin · Female · Cheerful (KR)"),
    ("ko-KR-InJoonNeural", "InJoon · Male · Calm (KR)"),
    ("ko-KR-HyunsuNeural", "Hyunsu · Male · Young (KR)"),
]


def split_sentences(text):
    """把正文切成朗读用的小句：优先按句末标点，长句按逗号/换行二次切分。"""
    parts = _SENT_END.split(text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for sub in re.split(r"\n+", p):
            sub = sub.strip()
            if not sub:
                continue
            out.extend(_hard_split(sub))
    return out


def _hard_split(s):
    if len(s) <= _MAX_CHUNK:
        return [s]
    out = []
    while len(s) > _MAX_CHUNK:
        cut = s.rfind("，", 0, _MAX_CHUNK)
        if cut < _MAX_CHUNK // 2:
            cut = s.rfind(",", 0, _MAX_CHUNK)
        if cut < _MAX_CHUNK // 2:
            cut = _MAX_CHUNK
        out.append(s[:cut])
        s = s[cut:]
    out.append(s)
    return out


def _sanitize_name(s):
    """把语音 ID 等转成安全的文件名片段。"""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(s)) or "default"


def synth_audio(text, voice, rate=200):
    """用 Edge 神经语音把一句文本合成为 MP3 字节（独立事件循环，线程安全）。"""
    import asyncio  # 惰性导入：只有 Edge 朗读真正出声时才需要（启动路径不需要事件循环）

    mod = _load_edge_tts()
    if mod is None:
        raise RuntimeError("edge-tts 未安装")
    rate_adj = int((max(50, min(400, int(rate))) - 200) / 2)
    buf = bytearray()

    async def _gen():
        c = mod.Communicate(text, voice, rate=f"{rate_adj:+d}%")
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_gen())
    finally:
        loop.close()
    return bytes(buf)


# ---------- 外部 TTS 引擎（VOICEVOX / vits-simple-api / Google / 火山 / OpenAI 兼容 / 自定义） ----------
# voice_id 编码：ext:<kind>:<urlencoded query>

def _http_read(req, timeout=30, proxy=None):
    import http.client
    try:
        if proxy:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy}))
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except http.client.IncompleteRead as e:
        # GPT-SoVITS 等服务 chunked 结尾不规范，已收到的音频数据照样可用
        return bytes(e.partial)


# ---------- GPT-SoVITS 本地服务：播放/缓存前自动启动 ----------

_gptsovits_proc = None


def _gptsovits_port_open(port=9880, timeout=0.4):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _gptsovits_find_python(root_dir):
    # 三平台同一个 api.py：Windows 集成包用 runtime\python.exe；
    # Mac/Linux 集成包是 runtime/bin/python3（或 runtime/python）；
    # 源码部署没有 runtime，回退 PATH 里的系统 python3 / python。
    if os.name == "nt":
        cands = [os.path.join(root_dir, "runtime", "python.exe")]
    else:
        cands = [
            os.path.join(root_dir, "runtime", "bin", "python3"),
            os.path.join(root_dir, "runtime", "bin", "python"),
            os.path.join(root_dir, "runtime", "python"),
        ]
    for p in cands:
        if os.path.isfile(p):
            return p
    import shutil
    for exe in ("python3", "python"):
        p = shutil.which(exe)
        if p:
            return p
    return None


def ensure_gptsovits_service(settings):
    """确保 GPT-SoVITS 本地服务可用（端口 9880）。

    填过根目录：端口没开就自动后台拉起服务并等待就绪（最长 60 秒）。
    没填根目录：返回 (False, "no_root")，由 UI 引导去设置里选择。
    返回 (ok, reason)：ok=True 时 reason ∈ port_open / started；
    ok=False 时 reason ∈ no_root / no_python / launch_error / timeout。
    """
    global _gptsovits_proc
    if _gptsovits_port_open():
        return True, "port_open"
    root = (settings or {}).get("gptsovits_root", "")
    if not root or not os.path.isdir(root):
        return False, "no_root"
    py = _gptsovits_find_python(root)
    if not py:
        return False, "no_python"
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        _gptsovits_proc = subprocess.Popen(
            [py, "api.py", "-p", "9880"], cwd=root, creationflags=flags)
    except Exception:
        return False, "launch_error"
    deadline = time.time() + 60
    while time.time() < deadline:
        if _gptsovits_port_open():
            return True, "started"
        time.sleep(1)
    return False, "timeout"


def ensure_gptsovits_service_async(settings, on_done):
    """后台线程运行 ensure_gptsovits_service，完成后回调 on_done(ok, why)。

    用于 UI 线程：启动最长可能 60 秒，同步调用会假死界面。
    回调在后台线程执行，UI 侧需要用 root.after(0, ...) 切回主线程再操作 Tk。
    """
    def _run():
        ok, why = False, "launch_error"
        try:
            ok, why = ensure_gptsovits_service(settings)
        except Exception:
            ok, why = False, "launch_error"
        try:
            on_done(ok, why)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def is_gptsovits_voice(voice_id):
    """判断 voice_id 是否指向 GPT-SoVITS 引擎（ext:gptsovits:…）。"""
    return str(voice_id or "").split(":", 2)[1:2] == ["gptsovits"]


def synth_external(text, voice_id, rate=200):
    """外部 TTS 引擎合成一句文本，返回 (audio_bytes, ext)。失败抛异常。"""
    parts = str(voice_id).split(":", 2)
    kind = parts[1] if len(parts) > 1 else ""
    qs = dict(urllib.parse.parse_qsl(parts[2] if len(parts) > 2 else ""))
    if kind == "voicevox":
        return _synth_voicevox(text, qs)
    if kind == "vits":
        return _synth_vits(text, qs)
    if kind == "google":
        return _synth_google(text, qs)
    if kind == "volcano":
        return _synth_volcano(text, qs)
    if kind == "openai":
        return _synth_openai(text, qs)
    if kind == "custom":
        return _synth_custom(text, qs)
    if kind == "gptsovits":
        return _synth_gptsovits(text, qs)
    raise ValueError("unknown external tts engine: " + kind)


def _synth_voicevox(text, qs):
    base = qs.get("base", "http://127.0.0.1:50021").rstrip("/")
    speaker = qs.get("speaker", "1")
    proxy = qs.get("proxy") or None
    data = urllib.parse.urlencode({"text": text, "speaker": speaker}).encode("utf-8")
    req = urllib.request.Request(base + "/audio_query", data=data, method="POST")
    query = json.loads(_http_read(req, proxy=proxy).decode("utf-8"))
    req2 = urllib.request.Request(
        base + "/synthesis?" + urllib.parse.urlencode({"speaker": speaker}),
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return _http_read(req2, proxy=proxy), "wav"


def _synth_vits(text, qs):
    base = qs.get("base", "http://127.0.0.1:9880").rstrip("/")
    sid = qs.get("id", "0")
    lang = qs.get("lang", "zh")
    url = base + "/tts?" + urllib.parse.urlencode(
        {"text": text, "id": sid, "lang": lang, "format": "mp3"})
    req = urllib.request.Request(url)
    return _http_read(req, proxy=qs.get("proxy") or None), "mp3"


def _synth_google(text, qs):
    from io import BytesIO
    from gtts import gTTS
    buf = BytesIO()
    proxy = qs.get("proxy") or None
    if proxy:
        # gTTS proxy 参数要求 host:port，剥掉 scheme
        hp = proxy.replace("http://", "").replace("https://", "").rstrip("/")
        gTTS(text=text, lang=qs.get("lang", "zh-CN"), proxy=hp).write_to_fp(buf)
    else:
        gTTS(text=text, lang=qs.get("lang", "zh-CN")).write_to_fp(buf)
    return buf.getvalue(), "mp3"


def _synth_volcano(text, qs):
    import base64
    body = {
        "app": {"appid": qs["appid"], "token": qs["token"],
                "cluster": qs.get("cluster", "volcano_tts")},
        "user": {"uid": "ddnr"},
        "audio": {"voice_type": qs["voice"], "encoding": "mp3"},
        "request": {"reqid": str(time.time()), "text": text,
                    "text_type": "plain", "operation": "query"},
    }
    req = urllib.request.Request(
        "https://openspeech.bytedance.com/api/v1/tts",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer;" + qs["token"]}, method="POST")
    resp = json.loads(_http_read(req, proxy=qs.get("proxy") or None).decode("utf-8"))
    if resp.get("code") != 3000:
        raise RuntimeError("volcano tts: " + str(resp.get("message")))
    return base64.b64decode(resp["data"]), "mp3"


def _synth_openai(text, qs):
    base = qs["base"].rstrip("/")
    body = {"model": qs.get("model", "tts-1"), "input": text,
            "voice": qs.get("voice", "alloy"), "response_format": "mp3"}
    req = urllib.request.Request(
        base + "/audio/speech", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + qs["key"]}, method="POST")
    return _http_read(req, proxy=qs.get("proxy") or None), "mp3"


def _synth_custom(text, qs):
    data = urllib.parse.urlencode(
        {"text": text, "voice": qs.get("voice", "")}).encode("utf-8")
    req = urllib.request.Request(qs["url"], data=data)
    return _http_read(req, proxy=qs.get("proxy") or None), "mp3"


_gptsovits_current = None  # 上次已加载的模型签名，避免每句都重新 set_model


def _synth_gptsovits(text, qs):
    """GPT-SoVITS 原生 api.py（GET / 根路由 + /set_model 切音色）。"""
    global _gptsovits_current
    base = qs.get("base", "http://127.0.0.1:9880").rstrip("/")
    proxy = qs.get("proxy") or None
    gpt = qs.get("gpt", "")
    sovits = qs.get("sovits", "")
    sig = (gpt, sovits)
    if (gpt or sovits) and sig != _gptsovits_current:
        q = urllib.parse.urlencode({"gpt_model_path": gpt, "sovits_model_path": sovits})
        try:
            _http_read(base + "/set_model?" + q, timeout=180, proxy=proxy)
            _gptsovits_current = sig
        except Exception:
            _gptsovits_current = None
    p = {
        "text": text,
        "text_language": qs.get("tl", "zh"),
        "refer_wav_path": qs.get("ref", ""),
        "prompt_text": qs.get("pt", ""),
        "prompt_language": qs.get("pl", "zh"),
        "speed": qs.get("speed", "1.0"),
    }
    req = urllib.request.Request(base + "/?" + urllib.parse.urlencode(p))
    return _http_read(req, timeout=180, proxy=proxy), "wav"


def list_voicevox_speakers(base, proxy=None):
    """探测 VOICEVOX 音色，返回 [(显示名, speaker_id)]。"""
    req = urllib.request.Request(base.rstrip("/") + "/speakers")
    arr = json.loads(_http_read(req, timeout=5, proxy=proxy).decode("utf-8"))
    out = []
    for sp in arr:
        for st in sp.get("styles", []):
            out.append((f"{sp.get('name','')} ({st.get('name','')})", str(st.get("id"))))
    return out


def list_vits_speakers(base, proxy=None):
    """探测 vits-simple-api 音色，返回 [(显示名, id)]。"""
    req = urllib.request.Request(base.rstrip("/") + "/voice_infos")
    arr = json.loads(_http_read(req, timeout=5, proxy=proxy).decode("utf-8"))
    out = []
    for it in arr:
        out.append((it.get("name") or str(it.get("id")), str(it.get("id"))))
    return out


class _EdgePrefetch:
    """后台批量预取后续句子的音频，缓存最多 MAX_AHEAD 句，句间零卡顿。

    生产线程从 start_off 起按句子顺序合成，放入有界队列（上限 MAX_AHEAD）。
    队列满则生产者阻塞，天然把预取量限制在 MAX_AHEAD 内，避免过量占用网络与内存。
    当前章节句子取完后自动跨到下一章继续预取——保证「跨章第一句」也有缓存，不卡顿。
    """

    MAX_AHEAD = 30

    def __init__(self, synth_fn, chapters, ci, content, start_off, limit=MAX_AHEAD):
        self._synth = synth_fn
        self._chapters = chapters
        self._ci = ci
        self._content = content
        self._limit = limit
        self._queue = queue.Queue(maxsize=limit)
        self._lock = threading.Lock()
        self._next_off = start_off
        self.consumed_ci = ci         # 已被播放取走的句子所在章节
        self.consumed_off = start_off   # 已被播放取走的句子结束位置（= 下一句起点）
        self._written_off = start_off   # 生产者已推进到的位置
        self._written_ci = ci
        self._stopped = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def stopped(self):
        return self._stopped.is_set()

    def _run(self):
        try:
            ci = self._ci
            content = self._content
            off = self._next_off
            while not self._stopped.is_set():
                text, nxt, _ = SpeechController._next_chunk(content, off)
                if not text or nxt <= off:
                    # 当前章节取完 → 跨到下一章继续预取（下一章开头不再现场合成）
                    if ci + 1 < len(self._chapters):
                        ci += 1
                        content, _cmap = self._chapters[ci].tts_content()
                        off = 0
                        self._written_ci = ci
                        continue
                    break
                start = off
                off = nxt
                self._written_off = nxt
                self._written_ci = ci
                try:
                    audio = self._synth(text)
                except Exception:
                    audio = None
                if audio is None:
                    continue
                item = (text, audio, start, nxt, ci)
                while not self._stopped.is_set():
                    try:
                        self._queue.put(item, timeout=0.5)
                        break
                    except queue.Full:
                        continue
        except Exception:
            pass

    def get(self, timeout=None):
        """取一句已预取的音频，返回 (text, audio, 句起点, 句终点, 章节)；超时返回 None。"""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        try:
            self.consumed_off = item[3]
            self.consumed_ci = item[4]
        except Exception:
            pass
        return item

    def close(self):
        self._stopped.set()


class WholeBookCacher:
    """整本语音缓存：后台多线程把全书逐句合成并落盘。

    设计要点（保证「缓存期间不影响正常朗读」）：
    - 完全独立于朗读工作线程：不使用 pygame / pyttsx3，不占用朗读音频资源；
    - 并发默认 6 线程（下载管理器可设置 3~12，超过 6 易触发微软限流导致失败重试反而更慢）；
    - 文件先写临时名再 os.replace 原子发布，朗读线程永远读不到半截文件；
    - 朗读侧按 (章节, 句偏移, 语音, 语速, 书籍) 命中缓存则直接播放，整本缓存完成后朗读零网络延迟；
    - 支持暂停/继续、章节选择、续传（已缓存文件自动跳过）、容量统计、完成后自动关机。
    """

    WORKERS = 6  # 默认并发下载线程数（可在下载管理器设置 3~12）
    SAVE_INTERVAL = 50  # 每完成 N 个任务保存一次进度

    def __init__(self, book, book_id, cache_root, voice, rate, chapter_indices=None,
                 bump_cb=None, size_cb=None, flush_cb=None, preset_tasks=None,
                 workers=None, is_external=False, cache_key=None):
        if workers:
            self.WORKERS = workers
        self._book = book
        self._book_id = str(book_id or "book")  # 书架 id：只用于 GUI 索引，不参与缓存路径
        # 缓存一级目录键 = content_key（跨端/跨机器稳定）；缺省回退书架 id，保持旧调用可用
        self._cache_key = str(cache_key or self._book_id)
        self._is_external = is_external
        # 每本书一个顶层目录：<cache_root>/<content_key>/<语音>/<语速百分比>/
        # 语速目录名 = 实际合成百分比（rate_dir_key），与安卓端对齐；
        # external voice_id 很长（ext:gptsovits:...），用 hash 缩短
        _vdir = hashlib.md5(voice.encode("utf-8")).hexdigest()[:16] if is_external else _sanitize_name(voice)
        self._rate_dir = rate_dir_key(rate)
        self._dir = os.path.join(
            cache_root, self._cache_key, _vdir, self._rate_dir
        )
        self._voice = voice
        self._rate = int(rate)
        self._bump_cb = bump_cb   # 写文件后回调（大小索引增量）
        self._size_cb = size_cb   # 查询该书已缓存大小（读索引）
        self._flush_cb = flush_cb # 完成/暂停/取消时回调（大小索引落盘）
        self._chapter_indices = chapter_indices  # set[int] 或 None=全部
        self._tasks = []  # [(ci, off, text)]，由后台线程构建
        self._total = 0
        self._done = 0
        self._next = 0
        self._completed = set()  # 已完成任务索引（持久化）
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._pause_evt = threading.Event()
        self._pause_evt.set()  # 默认运行
        self._state = "idle"  # idle / building / caching / paused / done / cancelled
        self._auto_shutdown = False
        self._shutdown_posted = False
        self._last_save = 0
        self._bytes_written = 0  # 本轮实际写入的音频字节（持久化大小用）
        self._build_ready = threading.Event()  # 任务列表构建完成信号
        self._build_error = None
        if preset_tasks is not None:
            # 验证补全模式：直接使用预置的缺失任务列表，不重新构建
            with self._lock:
                self._tasks = list(preset_tasks)
                self._total = len(self._tasks)
            self._build_ready.set()
        else:
            # 任务列表在后台线程构建：百万字长篇小说逐章切句耗时，不能阻塞 GUI 线程
            threading.Thread(target=self._build_tasks_bg, daemon=True).start()

    def _progress_path(self):
        return os.path.join(self._dir, "progress.json")

    def _load_progress(self):
        """加载持久化进度，返回 (done_count, completed_set) 或 None。

        键名双向兼容：PC 旧文件写 book_id（整数语速），安卓写 bookId（倍率语速）；
        迁移到 content_key 后，两者都按「content_key + 语速百分比」校验。
        """
        try:
            p = self._progress_path()
            if not os.path.exists(p):
                return None
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 只有任务总数匹配时才复用进度（章节选择变化时重置）
            if data.get("total") != self._total:
                return None
            key_in_file = str(data.get("book_id") or data.get("bookId") or "")
            # 接受两种键：新 content_key，以及旧布局的书架 id（尚未迁移的 progress.json）
            if key_in_file and key_in_file not in (self._cache_key, self._book_id):
                return None
            if data.get("voice") != self._voice:
                return None
            rate_in_file = data.get("rate")
            if rate_in_file is not None:
                try:
                    if isinstance(rate_in_file, (int, float)) and not isinstance(rate_in_file, bool):
                        # 迁移前的整数标度（200 = 正常速）
                        ok = int(rate_in_file) == self._rate
                    else:
                        ok = str(rate_in_file) == self._rate_dir
                except Exception:
                    ok = False
                if not ok:
                    return None
            completed = set(data.get("completed", []))
            done = data.get("done", len(completed))
            return done, completed
        except Exception:
            return None

    def _save_progress(self, state=None):
        """保存当前进度到 progress.json。"""
        try:
            os.makedirs(self._dir, exist_ok=True)
            with self._lock:
                done = self._done
                completed = sorted(self._completed)
                total = self._total
                cur_state = state or self._state
            data = {
                # 一份文件同时写两种键名：PC 读 book_id，安卓读 bookId，值都是 content_key
                "book_id": self._cache_key,
                "bookId": self._cache_key,
                "voice": self._voice,
                # 语速存合成百分比字符串（如 "0" / "50" / "-50"），与目录名一致
                "rate": self._rate_dir,
                "total": total,
                "done": done,
                "completed": completed,
                "state": cur_state,
                "updated_at": time.time(),
            }
            tmp = self._progress_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._progress_path())
        except Exception:
            pass

        if self._flush_cb is not None:
            try:
                self._flush_cb()
            except Exception:
                pass

    def _build_tasks(self):
        for ci, ch in enumerate(self._book.chapters):
            if self._chapter_indices is not None and ci not in self._chapter_indices:
                continue
            # 与朗读侧一致：在「纯净文本」上切句合成（去连续标点/空白/颜文字），
            # 文件仍按「原文偏移」命名，保证朗读时缓存可命中。
            clean_text, cmap = ch.tts_content()
            orig_len = len(ch.content)
            # 一次性切分整章全部句子（O(n)），避免逐句调用 _next_chunk
            # 导致的对剩余文本反复切片（O(n²)，百万字书会卡几十秒）。
            # _next_chunk 的切分规则与 split_sentences 完全一致，结果可互相命中。
            clean_off = 0
            for text in split_sentences(clean_text):
                if not text:
                    continue
                start = clean_text.find(text, clean_off)
                if start < 0:
                    start = clean_off
                orig_off = clean_to_orig(cmap, start, orig_len)
                self._tasks.append((ci, orig_off, text))
                clean_off = start + len(text)

    def _build_tasks_bg(self):
        """后台构建任务列表（逐章切句），完成后置 build_ready 信号。"""
        try:
            self._build_tasks()
            with self._lock:
                self._total = len(self._tasks)
        except Exception as e:
            with self._lock:
                self._build_error = e
        finally:
            self._build_ready.set()

    def start(self, resume=False):
        """开始缓存。立即返回（不阻塞 GUI），后台等待任务构建完成后再开跑。

        resume=True 时从持久化进度继续（跳过已完成任务）。
        """
        with self._lock:
            if self._state == "caching":
                return
            if self._state == "done":
                return
            self._state = "caching"
            self._cancelled.clear()
            self._pause_evt.set()
        os.makedirs(self._dir, exist_ok=True)
        threading.Thread(target=self._init_and_run, args=(resume,), daemon=True).start()

    def _init_and_run(self, resume):
        """在后台线程中：等待任务构建完成 → 初始化进度 → 启动工作线程。"""
        self._build_ready.wait()
        with self._lock:
            if self._build_error is not None:
                self._state = "cancelled"
                return
            total = self._total
        if total == 0:
            with self._lock:
                self._state = "done"
            self._save_progress("done")
            return

        if resume:
            prog = self._load_progress()
            if prog is not None:
                done, completed = prog
                with self._lock:
                    self._done = done
                    self._completed = set(completed)
                # 校验 completed：标记完成但实际文件缺失的任务剔除（断网/失败误标，允许补齐）
                if self._completed:
                    bad = []
                    for idx in list(self._completed):
                        ci, off, _text = self._tasks[idx]
                        if not self._file_exists(ci, off):
                            bad.append(idx)
                    for idx in bad:
                        with self._lock:
                            self._completed.discard(idx)
                            if self._done > 0:
                                self._done -= 1
                with self._lock:
                    # 找到第一个未完成的任务索引
                    self._next = 0
                    while self._next < total and self._next in self._completed:
                        self._next += 1
                # 如果全部完成，直接标记 done
                with self._lock:
                    if self._next >= total:
                        self._state = "done"
                if self._next >= total:
                    self._save_progress("done")
                    return
            else:
                # 无持久化进度：从头开始，但仍通过 _file_exists 跳过已缓存文件
                with self._lock:
                    self._done = 0
                    self._next = 0
                    self._completed = set()
        else:
            # 非 resume：从头开始（仍通过 _file_exists 跳过已缓存文件）
            with self._lock:
                self._done = 0
                self._next = 0
                self._completed = set()

        self._spawn_workers()

    def _spawn_workers(self):
        # 固定 WORKERS 个工作线程；任务不足时 worker 会自动退出
        for _ in range(self.WORKERS):
            threading.Thread(target=self._worker, daemon=True).start()

    def pause(self):
        self._pause_evt.clear()
        with self._lock:
            if self._state == "caching":
                self._state = "paused"
        self._save_progress("paused")

    def resume(self):
        if self._state != "paused":
            return self.status()
        self._state = "caching"
        self._pause_evt.set()
        self._spawn_workers()
        return self.status()

    def cancel(self):
        self._cancelled.set()
        self._pause_evt.set()
        with self._lock:
            if self._state in ("caching", "paused"):
                self._state = "cancelled"
        self._save_progress("cancelled")

    def set_auto_shutdown(self, on):
        self._auto_shutdown = bool(on)

    def _worker(self):
        # 等待任务列表构建完成（构建失败则直接退出）
        self._build_ready.wait()
        with self._lock:
            if self._build_error is not None:
                return
        while True:
            if self._cancelled.is_set():
                return
            if not self._pause_evt.wait(timeout=0.2):
                return  # 暂停：退出，等待 resume 重新拉线程
            with self._lock:
                if self._next >= len(self._tasks):
                    break
                idx = self._next
                self._next += 1
            ci, off, text = self._tasks[idx]
            # 已在持久化进度中标记完成的任务直接跳过
            with self._lock:
                already = idx in self._completed
            if already:
                continue
            if self._file_exists(ci, off):
                # 文件已存在（可能是之前缓存的），标记完成
                with self._lock:
                    self._completed.add(idx)
                    self._done += 1
                self._maybe_save()
                continue
            try:
                if self._is_external:
                    audio, _ext = synth_external(text, self._voice, self._rate)
                else:
                    audio = synth_audio(text, self._voice, self._rate)
                if not audio:
                    continue  # 合成失败/空：不标记完成，留待续传重试
                self._write(ci, off, audio)
            except Exception:
                continue  # 网络/合成失败：不标记完成，留待续传重试（避免 progress 谎报完成）
            with self._lock:
                self._completed.add(idx)
                self._done += 1
            self._maybe_save()
        # 检查是否全部完成（多 worker 竞态：任一 worker 收尾时已全部完成则无条件置 done）
        with self._lock:
            all_done = self._done >= self._total
        if all_done and not self._cancelled.is_set():
            with self._lock:
                self._state = "done"
            self._save_progress("done")
        elif self._state in ("caching", "building") and not self._cancelled.is_set():
            # 有任务因合成失败未完成：转暂停，进度保留，可续传补齐
            with self._lock:
                self._state = "paused"
            self._save_progress("paused")
        if (
            self._state == "done"
            and self._auto_shutdown
            and not self._shutdown_posted
        ):
            self._shutdown_posted = True
            self._post_shutdown()

    def _maybe_save(self):
        """每完成 SAVE_INTERVAL 个任务保存一次进度。"""
        with self._lock:
            done = self._done
        if done - self._last_save >= self.SAVE_INTERVAL:
            self._last_save = done
            self._save_progress()

    def _post_shutdown(self):
        """缓存完成后自动关机：60 秒倒计时，运行 `shutdown /a` 可取消。"""
        try:
            os.system("shutdown /s /t 60")
        except Exception:
            pass

    def _file_exists(self, ci, off):
        try:
            p = self._path(ci, off)
            return os.path.exists(p) and os.path.getsize(p) > 0
        except Exception:
            return False

    def _write(self, ci, off, audio):
        try:
            final = self._path(ci, off)
            tmp = final + ".tmp"
            with open(tmp, "wb") as f:
                f.write(audio)
            os.replace(tmp, final)
            with self._lock:
                self._bytes_written += len(audio)
            if self._bump_cb is not None:
                try:
                    self._bump_cb(self._cache_key, self._voice, self._rate_dir, len(audio))
                except Exception:
                    pass
        except Exception:
            pass

    def _path(self, ci, off):
        return os.path.join(self._dir, f"{int(ci):04d}_{int(off):08d}.mp3")

    def disk_used(self):
        """当前书籍缓存目录占用字节数（不含 progress.json）。

        优先读大小索引（不扫盘）；无索引回调时才退化为遍历目录。
        """
        if self._size_cb is not None:
            try:
                return int(self._size_cb(self._cache_key) or 0)
            except Exception:
                pass
        total = 0
        try:
            for root, _, files in os.walk(self._dir):
                for f in files:
                    if f == "progress.json" or f.endswith(".tmp"):
                        continue
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    def status(self):
        with self._lock:
            return {"state": self._state, "done": self._done, "total": self._total,
                    "bytes_written": self._bytes_written}


class SpeechController:
    def __init__(self):
        self._cv = threading.Condition()
        self._state = "idle"  # idle / playing / paused
        self._engine = None
        self._thread = None
        self._ready = None
        self._book = None
        self._ci = 0
        self._off = 0
        self._gen = 0  # 兼容保留：会话代号（真实权威见 _clock）
        # 重写后的链路：令牌时钟是唯一的"第几次开读"权威，音频走唯一输出通道。
        self._clock = SessionClock()
        self._bus = AudioBus(
            _player_open, _player_play, _player_pause, _player_resume,
            _player_stop, _player_close, _player_playing, _player_set_volume,
        )
        self._queue = queue.Queue()
        self._pending_voice = None  # SAPI 语音
        self._applied_voice = None
        self._applied_rate = None
        self._rate = 200
        self._sentence_gap = 0.10
        self._backend = "sapi"
        self._edge_voice = "zh-CN-XiaoxiaoNeural"
        self._external_voice = None
        self._edge_prefetch = None
        self._edge_fail_posted = False
        self._volume = 100  # 音量权威值（0-100）；bus 播放时按它下发
        self._book_id = "book"  # 当前书 id（整本缓存/统计用；start 前必须已就位）
        # 当前书的缓存目录键（content_key）；为空时回退 _book_id，兼容未接线的调用
        self._cache_key = ""
        # 书架 id → content_key 映射（按书统计/清理/大小索引共用，避免每次重算指纹）
        self._bid_keys = {}
        # 旧目录懒迁移锁（按书 rename，一次只动一本；不阻塞、不扫描全盘）
        self._migrate_lock = threading.Lock()
        # 多人有声（句子级音色切换）：仅 Edge 后端生效
        self._multi_roles = None       # {角色名: voice_id}；None=未启用
        self._multi_dialogues = None   # 当前章对白区间表 {ci: [[start,end,role],...]}
        self._cur_voice = None         # 当前句音色（多人有声按句覆盖，None=用默认语音）
        self._tts_cache_dir = None
        self._cache_workers = 6  # 整本缓存并发下载线程数（gui 启动时从设置读取）
        # 整本语音缓存：按 book_id 管理，每本书独立缓存器，互不阻塞（支持多书同时缓存）
        self._book_cachers = {}
        self._book_cachers_lock = threading.Lock()
        # 音频缓存大小索引（避免反复遍历数万细碎 mp3 文件）
        self._size_entries = None   # 懒加载：{key: {"size","t"}}
        self._size_dirty = False
        self._size_bumps = 0
        self._size_lock = threading.RLock()  # 可重入：bump/load 嵌套 with 不锁死
        # 整本缓存「完成态」文件数校验：后台单线程逐本执行（主线程只读集合，绝不扫描磁盘）
        self._hist_done_checked = set()
        self._hist_done_downgraded = set()
        self._hist_check_lock = threading.Lock()
        self._hist_check_queue = []
        self._hist_check_worker = None

    # ---------- 事件 ----------
    def _post(self, evt, gen=None):
        """所有事件都带会话令牌：UI 只接受当前令牌的事件，旧会话的事件自然作废。"""
        try:
            evt.setdefault("gen", int(gen) if gen is not None else self._clock.current)
        except Exception:
            evt["gen"] = gen
        self._queue.put(evt)

    @property
    def current_gen(self):
        """当前有效的会话令牌（UI / 事件过滤用）。"""
        return self._clock.current

    def drain(self):
        out = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    # ---------- 状态 ----------
    def is_playing(self):
        with self._cv:
            return self._state == "playing"

    def is_paused(self):
        with self._cv:
            return self._state == "paused"

    def is_active(self):
        with self._cv:
            return self._state in ("playing", "paused")

    def is_stopped(self):
        with self._cv:
            return self._state == "idle"

    # ---------- 对外设置（只存值，工作线程应用） ----------
    def set_voice(self, voice_id):
        if not voice_id:
            return
        with self._cv:
            if str(voice_id).startswith("ext:"):
                # 外部 TTS 引擎（VOICEVOX / vits / Google / 火山 / OpenAI / 自定义）
                self._backend = "external"
                self._external_voice = voice_id
                return
            if "HKEY" in voice_id or "TTS_MS" in voice_id or "SOFTWARE" in voice_id:
                # 系统 SAPI 语音
                self._backend = "sapi"
                self._pending_voice = voice_id
                self._external_voice = None
            else:
                # Edge 神经语音（zh-CN-XiaoxiaoNeural 等）
                self._backend = "edge"
                self._edge_voice = voice_id
                self._external_voice = None

    def set_rate(self, rate):
        with self._cv:
            self._rate = int(rate)

    def is_gptsovits_active(self):
        """当前选中的外部语音是否是 GPT-SoVITS（GUI 线程调用，只读）。"""
        with self._cv:
            return (self._backend == "external"
                    and is_gptsovits_voice(self._external_voice))

    # ---------- 多人有声（句子级音色切换，仅 Edge 后端） ----------
    def set_multi_voice(self, roles_map, dialogues):
        """启用多人有声朗读。

        roles_map: {角色名: Edge voice_id}；未列出的角色/旁白用默认语音。
        dialogues: 当前章节的对白区间表 {ci: [[start, end, role], ...]}（原文偏移）。
        """
        with self._cv:
            self._multi_roles = roles_map or None
            self._multi_dialogues = dialogues or {}
            self._cur_voice = None

    def clear_multi_voice(self):
        """关闭多人有声，恢复单语音朗读。"""
        with self._cv:
            self._multi_roles = None
            self._multi_dialogues = None
            self._cur_voice = None

    def _resolve_cur_voice(self, ci, orig_off):
        """多人有声：按句子原文偏移解析该句角色音色；未启用/未命中返回 None（用默认语音）。"""
        with self._cv:
            roles = self._multi_roles
            dia = self._multi_dialogues
            default_voice = self._edge_voice
        if not roles or not dia:
            return None
        spans = dia.get(ci)
        if not spans:
            return None
        role = None
        for start, end, r in spans:
            if start <= orig_off < end:
                role = r
                break
        if not role or role == "路人":
            return None
        voice = roles.get(role)
        if not voice or voice == default_voice:
            return None
        return voice

    def set_sentence_gap(self, gap):
        """句子之间的停顿间隔（秒），默认 0.10。"""
        with self._cv:
            self._sentence_gap = max(0.0, float(gap))

    def set_volume(self, volume):
        """设置本程序音量（0-100），即时应用到当前进程音频会话。"""
        self._volume = max(0, min(100, int(volume)))
        # 优先使用 Core Audio（pycaw），失败则回退 MCI
        if not _set_process_volume(self._volume):
            try:
                _mci_set_volume(self._volume)
            except Exception:
                pass
        try:
            self._bus.set_volume(self._volume)
        except Exception:
            pass

    def get_volume(self):
        return self._volume

    def prefetch_progress(self):
        """返回 Edge 后端当前预取缓存进度 (已缓存句数, 上限)；非 Edge 或未朗读返回 None。"""
        with self._cv:
            if self._backend != "edge":
                return None
            pf = self._edge_prefetch
        if pf is None:
            return None
        try:
            return (pf._queue.qsize(), _EdgePrefetch.MAX_AHEAD)
        except Exception:  # pragma: no cover
            return None

    # ---------- 整本语音缓存 ----------
    def set_cache_workers(self, n):
        """设置整本缓存并发下载线程数（1~32，界面限制 3~12）。"""
        try:
            self._cache_workers = max(1, min(32, int(n)))
        except Exception:
            self._cache_workers = 6

    def set_tts_cache_dir(self, path):
        self._tts_size_persist()  # 切换前把脏索引落盘到旧目录（转移时随目录带走）
        self._tts_cache_dir = path
        with self._size_lock:
            self._size_entries = None  # 目录切换后重新懒加载
            self._size_dirty = False

    def set_book_id(self, book_id, cache_key=None):
        """设置当前书：书架 id 只做 GUI 索引；传了 cache_key 才参与缓存路径。"""
        self._book_id = str(book_id or "book")
        key = str(cache_key or "").strip()
        self._cache_key = key or self._book_id
        if key:
            self._bid_keys[self._book_id] = key

    def set_book_cache_keys(self, mapping):
        """批量注册 书架id → content_key（启动时从书架元数据来，避免逐次重算指纹）。"""
        try:
            for bid, key in (mapping or {}).items():
                if bid and key:
                    self._bid_keys[str(bid)] = str(key)
        except Exception:
            pass

    def cache_key_for(self, book_id=None):
        """书架 id → 缓存目录键；未知则回退原 id（旧缓存目录仍可统计）。"""
        bid = str(book_id or self._book_id or "book")
        return self._bid_keys.get(bid, bid)

    def _ensure_cache_key(self, bid, book=None):
        """取（并按需计算）某本书的 content_key；算过就缓存，后续零成本。"""
        key = self._bid_keys.get(bid)
        if key:
            # 映射可能来自启动注册：仍要保证旧目录已迁移（有标记文件时只多一次 exists）
            self._lazy_migrate_book_dir(bid, key)
            return key
        if book is not None and getattr(book, "chapters", None):
            try:
                key = content_key_of_chapters(
                    getattr(book, "title", "") or "",
                    book.chapters,
                )
                self._bid_keys[bid] = key
                self._lazy_migrate_book_dir(bid, key)
                return key
            except Exception:
                pass
        return bid

    LAYOUT_MARK = ".tts_layout_v2"  # 每本书一份迁移标记：有它就不再动该目录

    def _lazy_migrate_book_dir(self, bid, key):
        """按书懒迁移旧缓存目录：只对本机已有的这一本做 rename，不扫描全盘、不复制文件。

        <root>/<旧book_id>            → <root>/<content_key>
        <root>/<key>/<语音>/<200…>    → <root>/<key>/<语音>/<语速百分比>
        同卷目录 rename 与内部文件数无关（几万个 mp3 也是瞬时），
        因此 PC 存量缓存不需要一次性全量迁移，打开哪本才迁哪本。
        """
        if not self._tts_cache_dir or not key or key == bid:
            return
        old = os.path.join(self._tts_cache_dir, str(bid))
        new = os.path.join(self._tts_cache_dir, str(key))
        mark = os.path.join(new, self.LAYOUT_MARK)
        with self._migrate_lock:
            if os.path.exists(mark):
                return  # 该书已迁移过：幂等，绝不再动目录名
            moved = False
            try:
                if os.path.isdir(old) and not os.path.exists(new):
                    os.renames(old, new)
                    moved = True
            except Exception:
                return
            if not os.path.isdir(new):
                return
            # 语速目录：迁移前是 PC 整数标度（200），迁移后是合成百分比（0 / 50 / -50）
            try:
                for voice in os.listdir(new):
                    vd = os.path.join(new, voice)
                    if not os.path.isdir(vd) or voice.startswith("."):
                        continue
                    for rname in os.listdir(vd):
                        rd = os.path.join(vd, rname)
                        if not os.path.isdir(rd):
                            continue
                        old_rate = self._legacy_rate_of_dir(rd)
                        if old_rate is None:
                            continue  # 已是新命名（或无法判定）：保持原样
                        target = rate_dir_key(old_rate)
                        if target == rname:
                            continue
                        tgt_dir = os.path.join(vd, target)
                        if os.path.exists(tgt_dir):
                            continue  # 目标已存在：留给后台合并，避免误覆盖
                        try:
                            os.renames(rd, tgt_dir)
                            self._rewrite_progress_rate(tgt_dir, key, target)
                            moved = True
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                with open(mark, "w", encoding="utf-8") as f:
                    json.dump({"v": 2, "at": time.time()}, f)
            except Exception:
                pass
        # 目录改名后旧索引键全部失效：清掉本书条目并只重算这一本（纯 stat，很快），
        # 其余书的索引条目原样保留，避免书架里其它书的大小显示成 0。
        if moved:
            try:
                self.tts_cache_invalidate(bid)
                # calibrate 按"目录名"扫盘，传 content_key（此时映射已登记）
                self.tts_cache_calibrate(self.cache_key_for(bid))
            except Exception:
                pass

    @staticmethod
    def _legacy_rate_of_dir(rd):
        """判定某语速目录名是否属于迁移前的 PC 整数标度；是则返回旧整数。

        依据：progress.json 的 rate 字段类型（旧文件是 int，新文件是 "-50" 这样的字符串）。
        缺 progress.json 时只能用范围：pct 最大 100，故 >100 必为旧标度；
        50..100 与 pct 重叠，保守按新命名处理，绝不破坏已迁移的缓存。
        """
        name = os.path.basename(rd)
        try:
            val = int(name)
        except Exception:
            return None
        pp = os.path.join(rd, "progress.json")
        if os.path.isfile(pp):
            try:
                with open(pp, "r", encoding="utf-8") as f:
                    r = json.load(f).get("rate")
                return val if isinstance(r, int) and not isinstance(r, bool) else None
            except Exception:
                return None
        return val if val > 100 else None

    @staticmethod
    def _rewrite_progress_rate(dir_path, key, rate_dir):
        """把迁移前 progress.json 的整数 rate 改写成目录名，供安卓端直接读取。"""
        p = os.path.join(dir_path, "progress.json")
        if not os.path.isfile(p):
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["book_id"] = key
            data["bookId"] = key
            data["rate"] = rate_dir
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass

    def ensure_book_cache(self, bid, book=None):
        """把书对象算出的 content_key 落到当前会话，并顺带触发旧目录懒迁移。

        open_book 时调用：meta 里已有 content_key 的书也要走这里，
        否则升级前留下的 <root>/<旧book_id> 目录永远不会被改名。
        """
        key = self.cache_key_for(bid)
        if book is not None:
            key = self._ensure_cache_key(bid, book)
        if str(bid) == self._book_id:
            self._cache_key = key
        return key

    def _cacher_for(self, book_id=None):
        """按 book_id 取缓存器；未指定时用当前书 id。"""
        bid = str(book_id or self._book_id or "book")
        with self._book_cachers_lock:
            return self._book_cachers.get(bid)

    def _cached_audio(self, ci, off):
        """按 (章节, 句偏移, 语音, 语速, 书籍) 查找整本缓存中的 MP3；未命中返回 None。"""
        if not self._tts_cache_dir:
            return None
        with self._cv:
            is_ext = (self._backend == "external")
            if not is_ext and self._backend != "edge":
                return None
            voice = self._external_voice if is_ext else (self._cur_voice or self._edge_voice)
            rate = int(self._rate)
        key = self._cache_key or self._book_id or "book"
        _vdir = hashlib.md5(voice.encode("utf-8")).hexdigest()[:16] if is_ext else _sanitize_name(voice)
        d = os.path.join(self._tts_cache_dir, key, _vdir, rate_dir_key(rate))
        p = os.path.join(d, f"{int(ci):04d}_{int(off):08d}.mp3")
        try:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                with open(p, "rb") as f:
                    return f.read()
        except Exception:
            pass
        return None

    def start_book_cache(self, book, book_id, chapter_indices=None, resume=False):
        """开启整本语音缓存（仅 Edge 后端），可按章节子集缓存。

        多书独立：每本书一个缓存器（keyed by book_id），第一本书缓存进行中
        也可以同时缓存第二本，互不干扰。resume=True 从持久化进度继续。
        返回状态 dict。
        """
        if not self._tts_cache_dir or not book or not book.chapters:
            return {"state": "unavailable"}
        bid = str(book_id or "book")
        self._book_id = bid  # 同步当前书 id，保证后续查询/控制落在本书
        with self._cv:
            is_external = (self._backend == "external")
            voice = self._external_voice if is_external else self._edge_voice
            rate = int(self._rate)
        with self._book_cachers_lock:
            cacher = self._book_cachers.get(bid)
            if cacher is not None:
                st = cacher.status()
                # 同书正在缓存：幂等返回，不重复启动
                if st["state"] == "caching":
                    return st
                same_sel = self._same_chapter_sel(cacher._chapter_indices, chapter_indices)
                # 同书已暂停且要求续传且章节选择未变：直接恢复（保留内存进度）
                if st["state"] == "paused" and resume and same_sel:
                    return cacher.resume()
                # 同书已完成且章节选择未变：幂等返回完成状态
                if st["state"] == "done" and same_sel:
                    return st
                # 其余情况（章节选择变化 / 暂停后显式重来 / 取消后重来）：重建 cacher，
                # 保证任务列表与本次选择一致（旧 done 任务列表会阻断新选择）
            cacher = WholeBookCacher(
                book, bid, self._tts_cache_dir, voice, rate, chapter_indices,
                bump_cb=self._tts_size_bump, size_cb=self.tts_cache_size,
                flush_cb=self._tts_size_persist,
                workers=self._cache_workers,
                is_external=is_external,
                cache_key=self._ensure_cache_key(bid, book),
            )
            self._book_cachers[bid] = cacher
        cacher.start(resume=resume)
        return cacher.status()

    @staticmethod
    def _same_chapter_sel(a, b):
        """章节选择是否相同：None 表示全选，与全选集合等价。"""
        a = a if a is None else set(a)
        b = b if b is None else set(b)
        return a == b

    def get_book_cache_progress(self, book, book_id):
        """返回缓存进度（优先读活跃 cacher 真实状态，无活跃任务时回退持久化进度）。"""
        bid = str(book_id or "")
        # 优先：当前有活跃 cacher（正在缓存/暂停/完成），直接读真实状态
        active = self._book_cachers.get(bid)
        if active is not None:
            st = active.status()
            return {"done": st.get("done", 0), "total": st.get("total", 0),
                    "state": st.get("state", "paused")}
        # 回退：读 progress.json（已暂停的历史任务）
        if not self._tts_cache_dir or not book:
            return None
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        try:
            cacher = WholeBookCacher(book, book_id, self._tts_cache_dir, voice, rate, None,
                                    cache_key=self._ensure_cache_key(bid, book))
            cacher._build_ready.wait(timeout=60)
            prog = cacher._load_progress()
            if prog is None:
                return None
            done, completed = prog
            return {"done": done, "total": cacher._total, "state": "paused"}
        except Exception:
            return None

    def verify_book_cache(self, book, book_id):
        """验证整本语音缓存完整性：按当前分章/语音/语速重建任务列表，
        扫描磁盘 mp3，返回缺失任务明细（供补全按钮使用）。

        返回 dict: {total, missing_count, missing(list[(ci,off,text)]),
                    chapters{ci:缺数}, complete, dir}，异常时含 error。
        """
        if not self._tts_cache_dir or not book or not book.chapters:
            return {"error": "unavailable"}
        bid = str(book_id or "book")
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        root = os.path.join(self._tts_cache_dir, self._ensure_cache_key(bid, book),
                            _sanitize_name(voice), rate_dir_key(rate))
        # 与 WholeBookCacher._build_tasks 完全一致的任务构建（同一套切分/偏移规则）
        tasks = []
        for ci, ch in enumerate(book.chapters):
            clean_text, cmap = ch.tts_content()
            orig_len = len(ch.content)
            clean_off = 0
            for text in split_sentences(clean_text):
                if not text:
                    continue
                start = clean_text.find(text, clean_off)
                if start < 0:
                    start = clean_off
                orig_off = clean_to_orig(cmap, start, orig_len)
                tasks.append((ci, orig_off, text))
                clean_off = start + len(text)
        missing = []
        ch_counts = {}
        for ci, off, text in tasks:
            try:
                p = os.path.join(root, "%04d_%08d.mp3" % (int(ci), int(off)))
                if os.path.isfile(p) and os.path.getsize(p) > 0:
                    continue
            except Exception:
                pass
            missing.append((ci, off, text))
            ch_counts[ci] = ch_counts.get(ci, 0) + 1
        return {
            "total": len(tasks),
            "missing_count": len(missing),
            "missing": missing,
            "chapters": ch_counts,
            "complete": not missing,
            "dir": root,
        }

    def fill_missing_cache(self, book, book_id, missing_tasks):
        """只缓存验证发现的缺失任务（补全模式），不重新下载已缓存部分。

        使用 preset_tasks 让缓存器直接处理缺失任务列表。返回状态 dict。
        """
        if self._backend != "edge":
            return {"state": "unsupported"}
        if not self._tts_cache_dir or not book or not book.chapters or not missing_tasks:
            return {"state": "unavailable"}
        bid = str(book_id or "book")
        self._book_id = bid
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        with self._book_cachers_lock:
            cacher = self._book_cachers.get(bid)
            if cacher is not None and cacher.status()["state"] in ("caching", "building"):
                return cacher.status()  # 补全任务已在跑，幂等返回
            cacher = WholeBookCacher(
                book, bid, self._tts_cache_dir, voice, rate,
                bump_cb=self._tts_size_bump, size_cb=self.tts_cache_size,
                flush_cb=self._tts_size_persist, preset_tasks=missing_tasks,
                cache_key=self._ensure_cache_key(bid, book),
            )
            self._book_cachers[bid] = cacher
        cacher.start(resume=False)
        return cacher.status()

    def pause_book_cache(self, book_id=None):
        cacher = self._cacher_for(book_id)
        if cacher is not None:
            return cacher.pause()
        return None

    def resume_book_cache(self, book_id=None):
        cacher = self._cacher_for(book_id)
        if cacher is not None:
            return cacher.resume()
        return None

    def cancel_book_cache(self, book_id=None):
        cacher = self._cacher_for(book_id)
        if cacher is not None:
            cacher.cancel()
            return cacher.status()
        return None

    def pause_all_book_cache(self):
        """关闭程序时暂停所有正在进行的缓存，保留进度供下次续传。"""
        with self._book_cachers_lock:
            cachers = list(self._book_cachers.values())
        for c in cachers:
            try:
                c.pause()
            except Exception:
                pass
        self._tts_size_persist()

    def set_book_cache_auto_shutdown(self, on, book_id=None):
        cacher = self._cacher_for(book_id)
        if cacher is not None:
            cacher.set_auto_shutdown(bool(on))

    def book_cache_disk_used(self, book_id=None):
        """当前书籍音频缓存占用字节数（读大小索引，不扫盘）。"""
        return self.tts_cache_size(book_id)
    # ---------- 音频缓存大小索引（避免反复遍历数万细碎 mp3） ----------
    def _tts_size_load(self):
        with self._size_lock:
            if self._size_entries is None:
                from .storage import load_tts_size_index
                root = self._tts_cache_dir or ""
                self._size_entries = load_tts_size_index(root)
            return self._size_entries

    def _tts_size_persist(self):
        with self._size_lock:
            if self._size_entries is not None and self._size_dirty:
                from .storage import save_tts_size_index
                save_tts_size_index(self._tts_cache_dir or "", self._size_entries)
                self._size_dirty = False

    def _tts_size_bump(self, bid, voice, rate, delta):
        """缓存写文件成功后增量累加大小（每 50 次落盘一次）。"""
        try:
            with self._size_lock:
                entries = self._tts_size_load()
                key = f"{str(bid)}/{voice}/{rate_dir_key(rate)}"
                e = entries.get(key) or {"size": 0, "t": time.time()}
                e["size"] = int(e.get("size", 0)) + int(delta)
                e["t"] = time.time()
                entries[key] = e
                self._size_dirty = True
                self._size_bumps += 1
                if self._size_bumps % 50 == 0:
                    self._tts_size_persist()
        except Exception:
            pass

    def tts_cache_size(self, book_id=None):
        """返回音频缓存大小（字节）。

        book_id 为空 = 全部书之和；给定 book_id = 该书所有语音/语速之和。
        纯读索引，不扫盘；由启动时的后台校准 + 缓存中增量维护保证准确性。
        """
        try:
            with self._size_lock:
                entries = self._tts_size_load()
                if book_id is not None:
                    key = self.cache_key_for(book_id) + "/"
                    total = sum(
                        int(e.get("size", 0))
                        for k, e in entries.items()
                        if k.startswith(key)
                    )
                else:
                    total = sum(int(e.get("size", 0)) for e in entries.values())
            return total
        except Exception:
            return 0

    def tts_cache_calibrate(self, book_id=None):
        """后台一次性校准音频缓存大小（扫盘）并写索引。

        返回 {bid: size}。仅用于启动迁移后 / 切换缓存目录 / 转移缓存后，
        正常使用中由增量维护保持准确，不重复扫盘。
        """
        try:
            from .storage import calibrate_tts_size
            result = calibrate_tts_size(self._tts_cache_dir or "", book_id)
            with self._size_lock:
                self._size_entries = None  # 强制下次懒加载新索引
            return result
        except Exception:
            return {}

    def tts_cache_invalidate(self, book_id=None):
        """清除某本书（或全部）的音频缓存大小索引条目（删除/转移缓存后调用）。

        条目清空后直接删除索引文件本身（连同 .tmp），避免残留几百字节的
        .tts_sizes.json 配置文件。
        """
        try:
            with self._size_lock:
                entries = self._tts_size_load()
                if book_id is not None:
                    key = self.cache_key_for(book_id) + "/"
                    for k in [k for k in list(entries) if k.startswith(key)]:
                        entries.pop(k, None)
                else:
                    entries.clear()
                if not entries:
                    # 索引已空：删除索引文件本身，目录保持干净，不写空索引
                    from .storage import tts_size_index_path
                    root = self._tts_cache_dir or ""
                    for p in (tts_size_index_path(root),
                              tts_size_index_path(root) + ".tmp"):
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception:
                            pass
                    self._size_entries = {}  # 内存置空，避免下次 load 读到旧数据
                    self._size_dirty = False
                else:
                    self._size_dirty = True
                    self._tts_size_persist()
        except Exception:
            pass

    # ---------- 整本语音缓存 ----------

    def book_cache_status(self, book_id=None):
        cacher = self._cacher_for(book_id)
        if cacher is None:
            return None
        return cacher.status()

    def book_cache_history(self, book_id=None):
        """返回该书的历史缓存进度（读 progress.json 索引，不扫 mp3 文件）。

        即使没有正在运行/刚启动的缓存任务（如重启软件、任务已结束），
        也能返回上次缓存的 done/total/state，供下载管理器显示"已下载 xx%"。
        无任何历史进度时返回 None。
        """
        bid = str(book_id or self._book_id or "book")
        # 优先：当前有活跃 cacher（正在缓存/暂停/刚完成），直接返回真实状态，不读 progress.json
        active = self._book_cachers.get(bid)
        if active is not None:
            st = active.status()
            return {
                "state": st.get("state", "paused"),
                "done": int(st.get("done", 0)),
                "total": int(st.get("total", 0)),
                "bytes_written": int(st.get("bytes_written", 0)),
                "history": False,
            }
        root = os.path.join(self._tts_cache_dir or "", self.cache_key_for(bid))
        best = None
        best_dir = None
        try:
            if os.path.isdir(root):
                for v in os.listdir(root):
                    vd = os.path.join(root, v)
                    if not os.path.isdir(vd):
                        continue
                    for r in os.listdir(vd):
                        pp = os.path.join(vd, r, "progress.json")
                        if not os.path.isfile(pp):
                            continue
                        try:
                            with open(pp, "r", encoding="utf-8") as f:
                                d = json.load(f)
                            if (best is None
                                    or (d.get("updated_at") or 0) > (best.get("updated_at") or 0)):
                                best = d
                                best_dir = os.path.join(vd, r)
                        except Exception:
                            pass
        except Exception:
            return None
        if best is None:
            return None
        # 无 active 任务时不可能真的在缓存/构建：归一化为 paused
        st = best.get("state", "paused")
        if st in ("caching", "building", "idle"):
            st = "paused"
        if st == "done":
            # 完成态校验完全在后台单线程执行；主线程只读结果集合，绝不扫描磁盘
            if best_dir in self._hist_done_downgraded:
                st = "paused"  # 后台已确认文件不足 → 降级可续传
            elif best_dir not in self._hist_done_checked:
                # 首次遇到：标记已计划校验 + 入队后台校验，本次保守返回 paused（允许用户点继续触发补齐）
                with self._hist_check_lock:
                    if best_dir not in self._hist_done_checked:
                        self._hist_done_checked.add(best_dir)
                        self._hist_check_queue.append(best_dir)
                        self._ensure_hist_check_worker()
                st = "paused"
        return {
            "state": st,
            "done": int(best.get("done", 0)),
            "total": int(best.get("total", 0)),
            "bytes_written": 0,
            "history": True,
        }

    def _ensure_hist_check_worker(self):
        """确保完成态校验后台线程在跑（单线程逐本校验，串行不拉满硬盘）。"""
        if self._hist_check_worker is not None and self._hist_check_worker.is_alive():
            return
        self._hist_check_worker = threading.Thread(target=self._hist_check_loop, daemon=True)
        self._hist_check_worker.start()

    def _hist_check_loop(self):
        """后台单线程：逐本统计 mp3 文件数，与 progress.done 对比，不足则记入降级集合。"""
        while True:
            with self._hist_check_lock:
                if not self._hist_check_queue:
                    return
                best_dir = self._hist_check_queue.pop(0)
            try:
                if not os.path.isdir(best_dir):
                    continue
                nfiles = sum(1 for f in os.listdir(best_dir) if f.endswith(".mp3"))
                # 读该目录 progress.json 的 done 作对比
                pp = os.path.join(best_dir, "progress.json")
                done = 0
                if os.path.isfile(pp):
                    try:
                        with open(pp, "r", encoding="utf-8") as f:
                            done = int(json.load(f).get("done", 0) or 0)
                    except Exception:
                        done = 0
                if done and nfiles < done * 0.98:
                    with self._hist_check_lock:
                        self._hist_done_downgraded.add(best_dir)
            except Exception:
                pass

    def chapters_cached_status(self, book, book_id):
        """返回每章是否已完全缓存的 dict {chapter_idx: bool}。

        用于「继续上次下载」：只选中未缓存的章节，避免全书重新下载的观感。
        """
        if not self._tts_cache_dir or not book or not book.chapters:
            return {}
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        bid = str(book_id or "book")
        d = os.path.join(self._tts_cache_dir, self._ensure_cache_key(bid, book),
                         _sanitize_name(voice), rate_dir_key(rate))
        result = {}
        for ci, ch in enumerate(book.chapters):
            try:
                clean_text, cmap = ch.tts_content()
                orig_len = len(ch.content)
                clean_off = 0
                total = 0
                cached = 0
                while clean_off < len(clean_text):
                    text, nxt, _ = SpeechController._next_chunk(clean_text, clean_off)
                    if not text or nxt <= clean_off:
                        break
                    orig_off = clean_to_orig(cmap, clean_off, orig_len)
                    total += 1
                    p = os.path.join(d, f"{int(ci):04d}_{int(orig_off):08d}.mp3")
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        cached += 1
                    clean_off = nxt
                result[ci] = (total > 0 and cached >= total)
            except Exception:
                result[ci] = False
        return result

    @staticmethod
    def list_edge_voices(lang="zh"):
        """按界面语言返回 Edge 语音备选：en → 英文音色（美音+英音），其余 → 中文音色。"""
        if lang == "en":
            return list(EDGE_VOICES_EN)
        return list(EDGE_VOICES)

    @staticmethod
    def list_voices():
        """在临时线程中枚举系统语音，避免与工作线程的 COM 对象冲突。

        COM/SAPI 枚举偶发失败（曾复现返回空导致"本地语音列表消失"），
        因此失败时自动重试 3 次，每次间隔 200ms，最大限度保证拿到系统语音。
        """
        for _attempt in range(3):
            out = []

            def work():
                try:
                    mod = _load_pyttsx3()
                    if mod is None:
                        return
                    e = mod.init()
                    try:
                        out.extend(v.id for v in e.getProperty("voices"))
                    finally:
                        try:
                            e.stop()
                        except Exception:
                            pass
                except Exception:
                    pass

            t = threading.Thread(target=work, daemon=True)
            t.start()
            t.join(timeout=10)
            if out:
                return out
            time.sleep(0.2)
        return out

    # ---------- 控制（GUI 线程调用） ----------
    def start(self, book, chapter_idx, char_offset, keep_prefetch=False):
        """开始 / 续读：拿一个新令牌，旧令牌的一切后续动作立即作废。

        keep_prefetch=True 时保留已合成好的预取队列（跳句时目标句常常就在队列里，
        拿到即可出声，不必重新合成）。
        """
        self._ensure_thread()
        token = self._clock.next()
        # 接管音频输出：先同步静音上一个流，再交给新令牌。旧会话此后无法再开播。
        self._bus.accept(token)
        with self._cv:
            self._book = book
            self._ci = int(chapter_idx)
            self._off = int(char_offset)
            self._gen = token
            self._state = "playing"
            self._cv.notify_all()

    def pause(self):
        with self._cv:
            if self._state == "playing":
                self._state = "paused"
                self._cv.notify_all()
        self._bus.pause()

    def resume(self):
        with self._cv:
            if self._state == "paused":
                self._state = "playing"
                self._cv.notify_all()
        self._bus.resume()

    def _silence_now(self):
        """同步静音：所有后端都在"调用它的这个线程"里立刻停下。

        - Edge / external / 系统语音：音频统一由 AudioBus 播放（pygame 进程级，跨线程安全），
          直接 stop + 释放媒体即可立刻消音；
        - SAPI 的 pyttsx3 引擎不能从这里调 stop()：实测会阻塞 UI 线程约 500ms，
          反而让按键手感更差。系统语音改成"先合成到文件、再交给 bus 播"，所以
          这里不需要碰引擎。
        """
        try:
            self._bus.stop_now()
        except Exception:
            pass

    def stop(self):
        """用户停止朗读：令牌推进 + 同步静音 + 关闭预取队列。"""
        token = self._clock.next()
        self._silence_now()
        with self._cv:
            self._book = None
            self._state = "idle"
            self._gen = token
            self._cv.notify_all()
        pf = self._edge_prefetch
        self._edge_prefetch = None
        if pf is not None:
            try:
                pf.close()
            except Exception:
                pass
        self._post({"type": "stopped"})

    def halt_for_jump(self):
        """快捷键跳句：按键瞬间同步静音，并把令牌推进一代。
        与 stop() 的区别：这里保留已经合成好的预取队列（跳句目标句往往就在里面），
        也不发 stopped 事件——跳句不是"用户停止朗读"。旧会话的令牌随之作废，
        它的合成结果、开播请求、事件在链路上全部被拒。
        """
        token = self._clock.next()
        self._silence_now()
        with self._cv:
            self._book = None
            self._state = "idle"
            self._gen = token
            self._cv.notify_all()
        # 跳句必须关掉旧的预取队列：它按旧位置顺序合成，目标句几乎永远不在队首，
        # 留着只会让每次点按都多留一个合成线程（线程与请求越积越多 → 界面卡顿、
        # 到最后停在原地不出声）。新位置由新会话自己重开预取。
        pf = self._edge_prefetch
        self._edge_prefetch = None
        if pf is not None:
            try:
                pf.close()
            except Exception:
                pass

    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    # ---------- 工作线程 ----------
    def _loop(self):
        try:
            self._ensure_engine()
        except Exception:
            pass
        if self._ready is not None:
            self._ready.set()
        try:
            last = None
            while True:
                with self._cv:
                    while self._book is None:
                        self._cv.wait()
                    book = self._book
                    ci = self._ci
                    off = self._off
                    gen = self._gen
                    self._state = "playing"
                    # 同一个会话刚刚已经跑完（合成/播放失败退出）时不要再原地重跑：
                    # 否则会死循环重试同一句，把 CPU 和合成请求都打满，界面表现为卡死。
                    if (gen, ci, off) == last:
                        self._cv.wait(0.15)
                        continue
                    last = (gen, ci, off)
                self._run_session(book, ci, off, gen)
        except Exception as e:  # pragma: no cover
            self._post({"type": "error", "message": f"朗读出错：{e}"})

    def _ensure_engine(self):
        # 仅 SAPI 后端需要 pyttsx3；Edge 后端按需初始化 pygame。
        # pyttsx3/SAPI 是 COM 组件：朗读工作线程必须先 CoInitialize 当前线程的 COM 公寓，
        # 否则 pyttsx3.init() 抛 CO_E_NOTINITIALIZED（被吞）→ _engine 为 None → 后续 .connect 崩溃。
        if self._backend != "sapi":
            return
        mod = _load_pyttsx3()
        if self._engine is None and mod is not None:
            try:
                self._coinit_com()
                self._engine = mod.init()
                # 注意：这里绝不能 startLoop()。系统语音已改为 save_to_file + runAndWait
                # 合成到文件（再由 AudioBus 播放），而 runAndWait 在已 startLoop 的引擎上
                # 会抛 "run loop already started"。
            except Exception as e:
                self._sapi_err = repr(e)
                self._engine = None

    @staticmethod
    def _coinit_com():
        """初始化当前线程的 COM 公寓（幂等），供 SAPI/pyttsx3 在朗读工作线程使用。"""
        for mod in ("comtypes", "pythoncom"):
            try:
                getattr(__import__(mod), "CoInitialize")()
            except Exception:
                pass

    def _sync_props(self):
        with self._cv:
            v = self._pending_voice
            r = self._rate
        if v and v != self._applied_voice:
            try:
                self._engine.setProperty("voice", v)
                self._applied_voice = v
            except Exception:
                pass
        if r and r != self._applied_rate:
            try:
                self._engine.setProperty("rate", r)
                self._applied_rate = r
            except Exception:
                pass

    def _should_stop(self, gen):
        with self._cv:
            return (self._state == "idle" or self._book is None
                    or self._clock.current != gen)

    # ---------- 朗读会话 ----------
    def _run_session(self, book, ci, off, gen):
        """一次朗读会话。只在当前令牌下执行；令牌一变，所有写操作立即失败。

        预取队列不在这里关闭、也不在会话之间"转让"——它只由 stop() 关闭，
        会话自己决定"是继续用还是丢掉重建"，因此不存在两个会话互相关队列的问题。
        """
        chapters = book.chapters
        self._edge_fail_posted = False
        try:
            while True:
                if self._should_stop(gen):
                    return
                with self._cv:
                    if self._state == "paused":
                        self._cv.wait()
                        continue
                self._sync_props()
                if ci >= len(chapters):
                    self._post({"type": "finished"}, gen)
                    with self._cv:
                        self._book = None
                        self._state = "idle"
                        self._cv.notify_all()
                    return
                orig_content = chapters[ci].content
                clean_text, cmap = chapters[ci].tts_content()
                # 传入/保存的进度是「原文偏移」，朗读在「纯净文本」上切句，二者互转
                clean_off = (
                    orig_to_clean(cmap, off) if off < len(orig_content) else len(clean_text)
                )
                if clean_off >= len(clean_text):
                    ci += 1
                    off = 0
                    self._post({"type": "chapter", "chapter_idx": ci}, gen)
                    continue
                text, next_clean, sent_start = self._next_chunk(clean_text, clean_off)
                if not text:
                    off = len(orig_content)
                    continue
                # 用当前句文本的实际起始位置（跳过句前空白），而不是 clean_off
                actual_clean_off = clean_off + sent_start
                orig_off = clean_to_orig(cmap, actual_clean_off, len(orig_content))
                # 多人有声：按句子原文偏移解析该句角色音色（仅 Edge 后端）
                if self._backend == "edge":
                    with self._cv:
                        self._cur_voice = self._resolve_cur_voice(ci, orig_off)
                self._post(
                    {
                        "type": "sentence_start",
                        "chapter_idx": ci,
                        "char_offset": orig_off,
                        "text": text,
                    },
                    gen,
                )
                if self._backend in ("edge", "external"):
                    ok = self._speak_edge(
                        text, gen, ci, orig_off, clean_text, next_clean, chapters,
                        actual_clean_off,
                    )
                else:
                    ok = self._speak_sapi(text, gen)
                if not ok:
                    return
                if self._should_stop(gen):
                    return
                with self._cv:
                    if self._state == "paused":
                        # 本句被打断（暂停），等恢复后重读本句，不推进进度
                        self._cv.wait()
                        continue
                next_orig = clean_to_orig(cmap, next_clean, len(orig_content))
                self._post(
                    {"type": "sentence_done", "chapter_idx": ci, "char_offset": next_orig},
                    gen,
                )
                # 句子之间停顿（默认 0.10s），增强朗读节奏
                with self._cv:
                    gap = self._sentence_gap
                if gap > 0:
                    time.sleep(gap)
                off = next_orig
        finally:
            # 预取队列不归会话所有：它由 stop() 关闭，跳句时保留给下一个会话复用。
            self._post({"type": "stopped"}, gen)

    @staticmethod
    def _next_chunk(content, offset):
        seg = content[offset:]
        frags = split_sentences(seg)
        if not frags:
            return "", len(content), 0
        text = frags[0]
        start = seg.find(text)
        if start < 0:
            return text, offset + len(text), 0
        return text, offset + start + len(text), start

    # ---------- SAPI 后端 ----------
    def _speak_sapi(self, text, gen, fallback=False):
        """系统语音朗读一句：先合成到临时 WAV，再交给 AudioBus 播放。

        不再用 pyttsx3 的实时 say()/iterate() 直接出声——那条路径既不能在按键瞬间
        停下（轮询要等 ~500ms，主线程直接 stop() 又会阻塞 UI 半秒），也无法和新链路的
        令牌/同步静音统一。合成到文件后播出，静音就走 AudioBus，按键立刻生效。
        fallback=True：Edge 联网失败后的回退——系统语音不可用时跳过本句继续，不终止整本。
        """
        if not text:
            return False
        self._ensure_engine()
        if self._engine is None:
            # 系统语音（SAPI）初始化失败：明确提示，避免对 None 调用 .connect 裸崩
            reason = getattr(self, "_sapi_err", "") or ""
            if not fallback:
                # 主用系统语音：无法朗读 → 终止会话并明确报错
                self._post(
                    {
                        "type": "error",
                        "message": "系统语音不可用（SAPI 初始化失败%s），请检查系统语音设置或改用在线语音"
                        % ("：" + reason if reason else ""),
                    }
                )
                return False
            # Edge 联网失败后的回退：系统语音也用不了 → 跳过本句继续朗读（仅提示一次防刷屏）
            if not self._edge_fail_posted:
                self._edge_fail_posted = True
                self._post(
                    {"type": "error", "message": "联网语音与系统语音均不可用，本句已跳过"}
                )
            return True
        if self._should_stop(gen):
            return False
        self._sync_props()
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            self._engine.save_to_file(text, tmp_path)
            self._engine.runAndWait()
            if self._should_stop(gen):
                return False
            try:
                if os.path.getsize(tmp_path) < 512:
                    return True  # 引擎没吐出音频：跳过本句继续读，不中断整本
            except OSError:
                return True
            return self._play_audio_file(tmp_path, gen)
        except Exception as e:
            self._post({"type": "error", "message": f"朗读出错：{e}"})
            return False
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ---------- Edge 后端 ----------
    def _edge_synthesize(self, text):
        with self._cv:
            voice = self._cur_voice or self._edge_voice
            rate = self._rate
            backend = self._backend
            ext_voice = self._external_voice
        if backend == "external":
            audio, _ext = synth_external(text, ext_voice, rate)
            return audio
        return synth_audio(text, voice, rate)

    def _take_prefetched(self, pf, text, ci, cl_off, gen, deadline):
        """从预取队列里取到当前句的音频，返回 (audio, need_rebuild)。

        正常续读时队首就是当前句，取到即用（零等待）；快捷键往后跳句时，队列里
        夹着中间几句，能直接丢弃就用，丢掉的目标句不在队列里则立刻重建——
        绝不为了一句去等生产线程把中间每句都合成一遍（那样跳 6 句要等 6 次合成）。
        位置比较必须带章节号：章节切换时 clean 偏移会归 0，只看偏移会误判为倒退。
        """
        while True:
            if self._should_stop(gen):
                return None, True
            if cl_off is not None and (pf.consumed_ci, pf.consumed_off) > (ci, cl_off):
                return None, True          # 队列已越过目标句（目标在前方）→ 重建
            at_head = cl_off is None or (pf.consumed_ci, pf.consumed_off) == (ci, cl_off)
            item = pf.get(timeout=0.05 if at_head else 0)
            if item is None:
                if pf.stopped():
                    return None, True
                if not at_head:
                    return None, True      # 队列里没有目标句 → 重建比干等更快
                if time.time() > deadline:
                    return None, True      # 队首迟迟不产出（网络慢）→ 自己合成
                continue
            ptext, paudio, pstart, _pn, pci = item[0], item[1], item[2], item[3], item[4]
            if ptext == text and pci == ci:
                return paudio, False
            if cl_off is not None and (pci, pstart) > (ci, cl_off):
                return None, True          # 已越过目标句（文本切分不一致）→ 重建

    def _speak_edge(self, text, gen, ci, off, content, next_off, chapters, cl_off=None):
        """Edge 语音：整本缓存命中直接播放；否则复用预取/现场合成；失败回退系统语音。
        多人有声模式逐句独立音色，禁用批量预取（音色随句变化，预取会串音色）。"""
        cached = self._cached_audio(ci, off) if self._backend in ("edge", "external") else None
        if cached:
            return self._speak_edge_play(cached, gen)
        multi_mode = self._multi_roles is not None
        audio = None
        if multi_mode:
            # 多人有声：逐句直接合成（按 _cur_voice 独立音色）
            try:
                audio = self._edge_synthesize(text)
            except Exception:
                audio = None
        else:
            pf = self._edge_prefetch
            if pf is not None and pf.stopped():
                self._edge_prefetch = None
                pf = None
            if pf is not None:
                audio, rebuild = self._take_prefetched(
                    pf, text, ci, cl_off, gen, time.time() + 8
                )
                if rebuild:
                    # 队列用不上（目标在前方 / 越过目标）：丢掉重建。
                    # 但只在本会话仍持有令牌时关闭——已被跳句顶替的旧会话不能动
                    # 队列，留给新位置的新会话自己判断。
                    if self._clock.current == gen:
                        try:
                            pf.close()
                        except Exception:
                            pass
                        if self._edge_prefetch is pf:
                            self._edge_prefetch = None
            if audio is None:
                # 现场合成当前句；同时立刻启动后续预取（并行，不再串行等合成完再预取）
                # 先关掉可能还活着的旧队列：同一时刻只允许一个预取线程在跑，
                # 否则点按跳句时每个会话都会新增一个合成线程，越积越多。
                old = self._edge_prefetch
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
                self._edge_prefetch = _EdgePrefetch(
                    self._edge_synthesize, chapters, ci, content, next_off,
                    _EdgePrefetch.MAX_AHEAD,
                )
                try:
                    audio = self._edge_synthesize(text)
                except Exception:
                    audio = None
        if audio:
            return self._speak_edge_play(audio, gen)
        # 联网失败：回退系统语音（仅提示一次）
        if not self._edge_fail_posted:
            self._edge_fail_posted = True
            self._post(
                {"type": "error", "message": "联网语音生成失败，正在尝试系统语音"}
            )
        pf = self._edge_prefetch
        if pf is not None and self._clock.current == gen:
            try:
                pf.close()
            except Exception:
                pass
            self._edge_prefetch = None
        return self._speak_sapi(text, gen, fallback=True)

    def _speak_edge_play(self, audio, gen):
        """把一句音频字节写成临时文件，交给唯一的播放函数。"""
        if not audio:
            return False
        if self._should_stop(gen):
            return False
        audio = _trim_edge_silence(audio)  # 裁句首尾静音（停顿=0 时句间接近无缝）
        tmp_path = None
        try:
            suffix = ".wav" if audio[:4] == b"RIFF" else ".mp3"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp_path = tmp.name
            tmp.write(audio)
            tmp.flush()
            tmp.close()
            return self._play_audio_file(tmp_path, gen)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _play_audio_file(self, path, gen):
        """唯一播放函数（Edge / GPT-SoVITS / 系统语音共用）。

        令牌过期（用户又按了一次键 / 停止朗读）时连媒体都不打开，绝不会有旧句在新
        位置开读前闪一下声音；播放期间的暂停/停止也由令牌与状态统一裁决。
        """
        if not path or self._should_stop(gen):
            return False
        try:
            if not self._bus.play(path, gen, self._volume):
                return False
            while self._bus.playing():
                if self._should_stop(gen):
                    self._bus.stop_now()
                    break
                with self._cv:
                    if self._state == "paused":
                        self._bus.pause()
                        self._cv.wait()
                        if self._should_stop(gen):
                            self._bus.stop_now()
                            break
                        self._bus.resume()
                time.sleep(0.02)
            self._bus.stop_now()
            return True
        except Exception as e:
            self._post({"type": "error", "message": f"播放出错：{e}"})
            return False
        finally:
            self._bus.stop_now()


def _trim_edge_silence(audio, keep_head_ms=40, keep_tail_ms=90, threshold=0.02):
    """裁剪 edge_tts 音频首尾静音（2026-10-19 新增）。

    实测 edge_tts 每句音频自带 ~550ms 句尾静音 + ~190ms 句头静音（合成器固有），
    即使用户把句间停顿设为 0，句间感知停顿仍约 0.7s。本函数在播放前把首尾静音
    裁掉（保留少量自然尾音），实现"停顿=0"时接近无缝朗读。
    - 输入 mp3/wav bytes → pygame 解码 → 静音检测 → 重写为 wav bytes；
    - 失败 / 无 pygame / 裁剪后过短时原样返回，不影响播放；
    - 每次 init 后保持 mixer 状态，兼容 macOS/Linux pygame 播放器懒加载。
    """
    if not audio or audio[:4] == b"RIFF":  # wav 无需处理（external 引擎输出）
        return audio
    try:
        import array
        import wave
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=24000, channels=2, size=-16)
        freq, _size, channels = pygame.mixer.get_init()[:3]
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        try:
            tmp.write(audio)
            tmp.flush()
            tmp.close()
            snd = pygame.mixer.Sound(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
        raw = snd.get_raw()
        if not raw or len(raw) < 64:
            return audio
        samples = array.array("h", raw)
        n = len(samples)
        peak = max(abs(x) for x in samples) or 1
        th = max(peak * threshold, 1)
        # 尾部：从后往前找最后一个超过阈值的样本
        i = n - 1
        while i >= 0 and abs(samples[i]) < th:
            i -= 1
        tail_keep = int(keep_tail_ms / 1000.0 * freq)
        tail_end = min(n - 1, i + tail_keep)
        # 头部：从前往后找第一个超过阈值的样本
        j = 0
        while j < n and abs(samples[j]) < th:
            j += 1
        head_keep = int(keep_head_ms / 1000.0 * freq)
        head_start = max(0, j - head_keep)
        if tail_end - head_start < int(0.05 * freq):  # 裁剪后过短，放弃
            return audio
        trimmed = samples[head_start:tail_end + 1]
        import io as _io
        buf = _io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(freq)
            w.writeframes(trimmed.tobytes())
        return buf.getvalue()
    except Exception:
        return audio


# ---------- Windows MCI 播放器（winmm.dll，替代 pygame，零依赖） ----------
_MCI_ALIAS = "dd_tts_player"


def _mci_send(cmd):
    """发送 MCI 命令，返回 (错误码, 返回文本)。"""
    buf = ctypes.create_unicode_buffer(512)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 512, 0)
    return err, buf.value


def _mci_open(path):
    _mci_close()
    mtype = "waveaudio" if path.lower().endswith(".wav") else "mpegvideo"
    cmd = f'open "{path}" type {mtype} alias {_MCI_ALIAS}'
    err, _ = _mci_send(cmd)
    if err != 0:
        raise RuntimeError(f"MCI open 失败 code={err}")


def _mci_set_volume(volume_0_100):
    """设置 MCI 音量（0-100 -> 0-1000）。"""
    v = max(0, min(1000, int(volume_0_100 * 10)))
    _mci_send(f"setaudio {_MCI_ALIAS} volume to {v}")


def _set_process_volume(volume_0_100):
    """通过 Windows Core Audio 设置当前进程音量（0-100）。优先使用 pycaw。"""
    ok, AudioUtilities, ISimpleAudioVolume = _load_pycaw()
    if not ok:
        return False
    try:
        volume = max(0.0, min(1.0, volume_0_100 / 100.0))
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        sessions = AudioUtilities.GetAllSessions()
        for s in sessions:
            if s.ProcessId == pid:
                vol = s._ctl.QueryInterface(ISimpleAudioVolume)
                vol.SetMasterVolume(volume, None)
                return True
        return False
    except Exception:
        return False


def _mci_play():
    err, _ = _mci_send(f"play {_MCI_ALIAS}")
    if err != 0:
        raise RuntimeError(f"MCI play 失败 code={err}")
def _mci_pause():
    _mci_send(f"pause {_MCI_ALIAS}")


def _mci_resume():
    err, _ = _mci_send(f"play {_MCI_ALIAS}")
    if err != 0:
        raise RuntimeError(f"MCI resume 失败 code={err}")


def _mci_stop():
    _mci_send(f"stop {_MCI_ALIAS}")


def _mci_close():
    _mci_send(f"close {_MCI_ALIAS}")


def _mci_playing():
    """查询是否仍在播放/暂停中。自然播放结束返回 False。"""
    _, mode = _mci_send(f"status {_MCI_ALIAS} mode")
    mode = mode.strip().lower()
    return mode in ("playing", "paused")


# ---------- 跨平台播放器分发（统一 pygame.mixer.music） ----------
# 2026-11-16：Windows 原先走 MCI，但 MCI 的设备别名绑定在**调用线程**上——
# 主线程（UI）发 "stop/close" 会直接返回 263 无效设备，停止命令实际不生效，
# 这正是"按 ←/→ 语音不停"的根因之一。pygame.mixer.music 是进程级状态，
# 任何线程都能立刻 stop/pause，因此三个平台统一用它作为唯一播放通道。
_IS_WINDOWS = sys.platform.startswith("win")


def _player_open(path):
    _pp_open(path)


def _player_set_volume(v):
    _pp_set_volume(v)
    if _IS_WINDOWS:
        _set_process_volume(v)  # 进程音量（pycaw 可用时）：让整体音量跟随滑杆


def _player_play():
    _pp_play()


def _player_pause():
    _pp_pause()


def _player_resume():
    _pp_resume()


def _player_stop():
    _pp_stop()


def _player_close():
    _pp_close()


def _player_playing():
    return _pp_playing()


# ---------- pygame 播放器（macOS / Linux，懒加载，Windows 打包时排除 pygame 不受影响） ----------
_pp_paused = False


def _pp_open(path):
    global _pp_paused
    import pygame
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    _pp_paused = False
    pygame.mixer.music.load(path)


def _pp_set_volume(v):
    import pygame
    try:
        pygame.mixer.music.set_volume(max(0.0, min(1.0, v / 100.0)))
    except Exception:
        pass


def _pp_play():
    global _pp_paused
    import pygame
    _pp_paused = False
    pygame.mixer.music.play()


def _pp_pause():
    global _pp_paused
    import pygame
    if pygame.mixer.music.get_busy():
        pygame.mixer.music.pause()
        _pp_paused = True


def _pp_resume():
    global _pp_paused
    import pygame
    pygame.mixer.music.unpause()
    _pp_paused = False


def _pp_stop():
    global _pp_paused
    import pygame
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass
    _pp_paused = False


def _pp_close():
    global _pp_paused
    import pygame
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
    except Exception:
        pass
    _pp_paused = False


def _pp_playing():
    import pygame
    return bool(pygame.mixer.music.get_busy()) or _pp_paused
