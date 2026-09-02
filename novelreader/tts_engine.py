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

import asyncio
import queue
import re
import tempfile
import threading
import time

try:
    import pyttsx3
except Exception:  # pragma: no cover
    pyttsx3 = None

try:
    import edge_tts
except Exception:  # pragma: no cover
    edge_tts = None

import ctypes
try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    _HAS_PYCAW = True
except Exception:
    _HAS_PYCAW = False
from ctypes import wintypes

try:
    import pyttsx3
except Exception:  # pragma: no cover
    pyttsx3 = None
from .textproc import clean_to_orig, orig_to_clean

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
    if edge_tts is None:
        raise RuntimeError("edge-tts 未安装")
    rate_adj = int((max(80, min(400, int(rate))) - 200) / 2)
    buf = bytearray()

    async def _gen():
        c = edge_tts.Communicate(text, voice, rate=f"{rate_adj:+d}%")
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_gen())
    finally:
        loop.close()
    return bytes(buf)


class _EdgePrefetch:
    """后台批量预取后续句子的音频，缓存最多 MAX_AHEAD 句，句间零卡顿。

    生产线程从 start_off 起按句子顺序合成，放入有界队列（上限 MAX_AHEAD）。
    队列满则生产者阻塞，天然把预取量限制在 MAX_AHEAD 内，避免过量占用网络与内存。
    """

    MAX_AHEAD = 30

    def __init__(self, synth_fn, content, start_off, limit=MAX_AHEAD):
        self._synth = synth_fn
        self._content = content
        self._limit = limit
        self._queue = queue.Queue(maxsize=limit)
        self._lock = threading.Lock()
        self._next_off = start_off
        self._stopped = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            while not self._stopped.is_set():
                with self._lock:
                    off = self._next_off
                text, nxt = SpeechController._next_chunk(self._content, off)
                if not text or nxt <= off:
                    break
                with self._lock:
                    self._next_off = nxt
                try:
                    audio = self._synth(text)
                except Exception:
                    audio = None
                if audio is None:
                    continue
                item = (text, audio)
                while not self._stopped.is_set():
                    try:
                        self._queue.put(item, timeout=0.5)
                        break
                    except queue.Full:
                        continue
        except Exception:
            pass

    def get(self, timeout=None):
        """取一句已预取的音频，返回 (text, audio)；超时返回 None。"""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self._stopped.set()


class WholeBookCacher:
    """整本语音缓存：后台多线程把全书逐句合成并落盘。

    设计要点（保证「缓存期间不影响正常朗读」）：
    - 完全独立于朗读工作线程：不使用 pygame / pyttsx3，不占用朗读音频资源；
    - 并发限 3 线程，避免打满网络拖慢朗读自身的按需合成；
    - 文件先写临时名再 os.replace 原子发布，朗读线程永远读不到半截文件；
    - 朗读侧按 (章节, 句偏移, 语音, 语速, 书籍) 命中缓存则直接播放，整本缓存完成后朗读零网络延迟；
    - 支持暂停/继续、章节选择、续传（已缓存文件自动跳过）、容量统计、完成后自动关机。
    """

    WORKERS = 3
    SAVE_INTERVAL = 50  # 每完成 N 个任务保存一次进度

    def __init__(self, book, book_id, cache_root, voice, rate, chapter_indices=None):
        self._book = book
        self._book_id = str(book_id or "book")
        self._dir = os.path.join(
            cache_root, _sanitize_name(voice), str(int(rate)), self._book_id
        )
        self._voice = voice
        self._rate = int(rate)
        self._chapter_indices = chapter_indices  # set[int] 或 None=全部
        self._tasks = []  # [(ci, off, text)]
        self._build_tasks()
        self._total = len(self._tasks)
        self._done = 0
        self._next = 0
        self._completed = set()  # 已完成任务索引（持久化）
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._pause_evt = threading.Event()
        self._pause_evt.set()  # 默认运行
        self._state = "idle"  # idle / caching / paused / done / cancelled
        self._auto_shutdown = False
        self._shutdown_posted = False
        self._last_save = 0
        self._bytes_written = 0  # 本轮实际写入的音频字节（持久化大小用）

    def _progress_path(self):
        return os.path.join(self._dir, "progress.json")

    def _load_progress(self):
        """加载持久化进度，返回 (done_count, completed_set) 或 None。"""
        try:
            p = self._progress_path()
            if not os.path.exists(p):
                return None
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 只有任务总数匹配时才复用进度（章节选择变化时重置）
            if data.get("total") != self._total:
                return None
            if data.get("book_id") != self._book_id:
                return None
            if data.get("voice") != self._voice or data.get("rate") != self._rate:
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
                "book_id": self._book_id,
                "voice": self._voice,
                "rate": self._rate,
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

    def _build_tasks(self):
        for ci, ch in enumerate(self._book.chapters):
            if self._chapter_indices is not None and ci not in self._chapter_indices:
                continue
            # 与朗读侧一致：在「纯净文本」上切句合成（去连续标点/空白/颜文字），
            # 文件仍按「原文偏移」命名，保证朗读时缓存可命中。
            clean_text, cmap = ch.tts_content()
            orig_len = len(ch.content)
            clean_off = 0
            while clean_off < len(clean_text):
                text, nxt = SpeechController._next_chunk(clean_text, clean_off)
                if not text or nxt <= clean_off:
                    break
                orig_off = clean_to_orig(cmap, clean_off, orig_len)
                self._tasks.append((ci, orig_off, text))
                clean_off = nxt

    def start(self, resume=False):
        """开始缓存。resume=True 时从持久化进度继续（跳过已完成任务）。"""
        if self._state == "caching":
            return
        if not self._tasks:
            self._state = "done"
            return
        os.makedirs(self._dir, exist_ok=True)
        self._state = "caching"
        self._cancelled.clear()
        self._pause_evt.set()

        if resume:
            prog = self._load_progress()
            if prog is not None:
                done, completed = prog
                with self._lock:
                    self._done = done
                    self._completed = completed
                    # 找到第一个未完成的任务索引
                    self._next = 0
                    while self._next < self._total and self._next in self._completed:
                        self._next += 1
                # 如果全部完成，直接标记 done
                if self._next >= self._total:
                    with self._lock:
                        self._state = "done"
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
        for _ in range(min(self.WORKERS, max(1, self._total - self._done))):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()

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
        while True:
            if self._cancelled.is_set():
                return
            if not self._pause_evt.wait(timeout=0.2):
                return  # 暂停：退出，等待 resume 重新拉线程
            with self._lock:
                idx = self._next
                self._next += 1
            if idx >= len(self._tasks):
                break
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
                audio = synth_audio(text, self._voice, self._rate)
                if audio:
                    self._write(ci, off, audio)
            except Exception:
                pass
            with self._lock:
                self._completed.add(idx)
                self._done += 1
            self._maybe_save()
        # 检查是否全部完成
        with self._lock:
            all_done = self._done >= self._total
            if all_done and self._state == "caching" and not self._cancelled.is_set():
                self._state = "done"
        if all_done:
            self._save_progress("done")
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
        except Exception:
            pass

    def _path(self, ci, off):
        return os.path.join(self._dir, f"{int(ci):04d}_{int(off):08d}.mp3")

    def disk_used(self):
        """当前书籍缓存目录占用字节数（不含 progress.json）。"""
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
        self._gen = 0  # 会话代号：每次 start 自增
        self._queue = queue.Queue()
        self._pending_voice = None  # SAPI 语音
        self._applied_voice = None
        self._applied_rate = None
        self._rate = 200
        self._sentence_gap = 0.10
        self._backend = "sapi"
        self._edge_voice = "zh-CN-XiaoxiaoNeural"
        self._edge_prefetch = None
        self._edge_fail_posted = False
        self._tts_cache_dir = None
        self._book_cacher = None

    # ---------- 事件 ----------
    def _post(self, evt):
        self._queue.put(evt)

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
            if "HKEY" in voice_id or "TTS_MS" in voice_id or "SOFTWARE" in voice_id:
                # 系统 SAPI 语音
                self._backend = "sapi"
                self._pending_voice = voice_id
            else:
                # Edge 神经语音（zh-CN-XiaoxiaoNeural 等）
                self._backend = "edge"
                self._edge_voice = voice_id

    def set_rate(self, rate):
        with self._cv:
            self._rate = int(rate)

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
    def set_tts_cache_dir(self, path):
        self._tts_cache_dir = path

    def set_book_id(self, book_id):
        self._book_id = str(book_id or "book")

    def _cached_audio(self, ci, off):
        """按 (章节, 句偏移, 语音, 语速, 书籍) 查找整本缓存中的 MP3；未命中返回 None。"""
        if not self._tts_cache_dir:
            return None
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        bid = self._book_id or "book"
        d = os.path.join(self._tts_cache_dir, _sanitize_name(voice), str(rate), bid)
        p = os.path.join(d, f"{int(ci):04d}_{int(off):08d}.mp3")
        try:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                with open(p, "rb") as f:
                    return f.read()
        except Exception:
            pass
        return None

    def start_book_cache(self, book, book_id, chapter_indices=None, resume=False):
        """开启整本语音缓存（仅 Edge 后端），可按章节子集缓存。resume=True 从持久化进度继续。返回状态 dict。"""
        if self._backend != "edge":
            return {"state": "unsupported"}
        if not self._tts_cache_dir or not book or not book.chapters:
            return {"state": "unavailable"}
        if self._book_cacher is not None and self._book_cacher.status()["state"] == "caching":
            return self._book_cacher.status()  # 已在缓存中，幂等返回
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        self._book_cacher = WholeBookCacher(
            book, book_id, self._tts_cache_dir, voice, rate, chapter_indices
        )
        self._book_cacher.start(resume=resume)
        return self._book_cacher.status()

    def get_book_cache_progress(self, book, book_id):
        """返回持久化的缓存进度信息（用于显示续传位置），无进度返回 None。"""
        if not self._tts_cache_dir or not book:
            return None
        with self._cv:
            voice = self._edge_voice
            rate = int(self._rate)
        try:
            cacher = WholeBookCacher(book, book_id, self._tts_cache_dir, voice, rate, None)
            prog = cacher._load_progress()
            if prog is None:
                return None
            done, completed = prog
            return {"done": done, "total": cacher._total, "state": "paused"}
        except Exception:
            return None

    def pause_book_cache(self):
        if self._book_cacher is not None:
            return self._book_cacher.pause()
        return None

    def resume_book_cache(self):
        if self._book_cacher is not None:
            return self._book_cacher.resume()
        return None

    def cancel_book_cache(self):
        if self._book_cacher is not None:
            self._book_cacher.cancel()
            return self._book_cacher.status()
        return None

    def set_book_cache_auto_shutdown(self, on):
        if self._book_cacher is not None:
            self._book_cacher.set_auto_shutdown(bool(on))

    def book_cache_disk_used(self):
        if self._book_cacher is not None:
            return self._book_cacher.disk_used()
        return 0

    def book_cache_status(self):
        if self._book_cacher is None:
            return None
        return self._book_cacher.status()

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
        d = os.path.join(self._tts_cache_dir, _sanitize_name(voice), str(rate), bid)
        result = {}
        for ci, ch in enumerate(book.chapters):
            try:
                clean_text, cmap = ch.tts_content()
                orig_len = len(ch.content)
                clean_off = 0
                total = 0
                cached = 0
                while clean_off < len(clean_text):
                    text, nxt = SpeechController._next_chunk(clean_text, clean_off)
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
    def list_edge_voices():
        return list(EDGE_VOICES)

    @staticmethod
    def list_voices():
        """在临时线程中枚举系统语音，避免与工作线程的 COM 对象冲突。"""
        out = []

        def work():
            try:
                if pyttsx3 is None:
                    return
                e = pyttsx3.init()
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
        return out

    # ---------- 控制（GUI 线程调用） ----------
    def start(self, book, chapter_idx, char_offset):
        self._ensure_thread()
        with self._cv:
            self._book = book
            self._ci = int(chapter_idx)
            self._off = int(char_offset)
            self._gen += 1
            self._state = "playing"
            self._cv.notify_all()

    def pause(self):
        with self._cv:
            if self._state == "playing":
                self._state = "paused"
                self._cv.notify_all()

    def resume(self):
        with self._cv:
            if self._state == "paused":
                self._state = "playing"
                self._cv.notify_all()

    def stop(self):
        with self._cv:
            self._book = None
            self._state = "idle"
            self._cv.notify_all()

    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    # ---------- 工作线程 ----------
    def _loop(self):
        self._ensure_engine()
        if self._ready is not None:
            self._ready.set()
        try:
            while True:
                with self._cv:
                    while self._book is None:
                        self._cv.wait()
                    book = self._book
                    ci = self._ci
                    off = self._off
                    gen = self._gen
                    self._state = "playing"
                self._run_session(book, ci, off, gen)
        except Exception as e:  # pragma: no cover
            self._post({"type": "error", "message": f"朗读出错：{e}"})

    def _ensure_engine(self):
        # 仅 SAPI 后端需要 pyttsx3 引擎；Edge 后端按需初始化 pygame
        if self._engine is None and pyttsx3 is not None:
            self._engine = pyttsx3.init()
            self._engine.startLoop(useDriverLoop=False)

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
            return self._state == "idle" or self._book is None or self._gen != gen

    # ---------- 朗读会话 ----------
    def _run_session(self, book, ci, off, gen):
        chapters = book.chapters
        self._edge_prefetch = None
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
                    self._post({"type": "finished"})
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
                    self._post({"type": "chapter", "chapter_idx": ci})
                    continue
                text, next_clean, sent_start = self._next_chunk(clean_text, clean_off)
                if not text:
                    off = len(orig_content)
                    continue
                # 用当前句文本的实际起始位置（跳过句前空白），而不是 clean_off
                actual_clean_off = clean_off + sent_start
                orig_off = clean_to_orig(cmap, actual_clean_off, len(orig_content))
                self._post(
                    {
                        "type": "sentence_start",
                        "chapter_idx": ci,
                        "char_offset": orig_off,
                        "text": text,
                    }
                )
                if self._backend == "edge":
                    ok = self._speak_edge(text, gen, ci, orig_off, clean_text, next_clean)
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
                    {"type": "sentence_done", "chapter_idx": ci, "char_offset": next_orig}
                )
                # 句子之间停顿（默认 0.10s），增强朗读节奏
                with self._cv:
                    gap = self._sentence_gap
                if gap > 0:
                    time.sleep(gap)
                off = next_orig
        finally:
            if self._edge_prefetch is not None:
                try:
                    self._edge_prefetch.close()
                except Exception:
                    pass
                self._edge_prefetch = None
            self._post({"type": "stopped"})

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
    def _speak_sapi(self, text, gen):
        """系统语音朗读一句，阻塞到结束或被暂停/停止打断。"""
        if not text:
            return False
        self._ensure_engine()
        done = threading.Event()

        def _on_finished(name=None, completed=None, **kw):
            done.set()

        try:
            self._engine.connect("finished-utterance", _on_finished)
            self._engine.say(text)
            while not done.is_set():
                if self._should_stop(gen):
                    self._engine.stop()  # 停止/切书：同线程打断
                    break
                with self._cv:
                    if self._state == "paused":
                        self._engine.stop()  # 暂停：同线程打断
                        break
                try:
                    self._engine.iterate()
                except Exception:
                    break
                done.wait(0.02)
        except Exception as e:
            self._post({"type": "error", "message": f"朗读出错：{e}"})
            return False
        finally:
            try:
                self._engine.disconnect({"topic": "finished-utterance", "cb": _on_finished})
            except Exception:
                pass
        return True

    # ---------- Edge 后端 ----------
    def _edge_synthesize(self, text):
        with self._cv:
            voice = self._edge_voice
            rate = self._rate
        return synth_audio(text, voice, rate)

    def _speak_edge(self, text, gen, ci, off, content, next_off):
        """Edge 语音：整本缓存命中直接播放；否则批量预取/按需合成；失败回退系统语音。"""
        cached = self._cached_audio(ci, off)
        if cached:
            return self._speak_edge_play(cached, gen)
        if self._edge_prefetch is None:
            # 首句：直接生成，同时启动后续最多 30 句的批量预取
            try:
                audio = self._edge_synthesize(text)
            except Exception:
                audio = None
            self._edge_prefetch = _EdgePrefetch(
                self._edge_synthesize, content, next_off, _EdgePrefetch.MAX_AHEAD
            )
        else:
            audio = None
            item = None
            try:
                item = self._edge_prefetch.get(timeout=10)
            except Exception:
                item = None
            if item:
                ptext, paudio = item
                if ptext == text:
                    audio = paudio
            if audio is None:
                # 预取未就绪（网络慢）或句序失配（罕见竞态）：直接生成本句
                try:
                    audio = self._edge_synthesize(text)
                except Exception:
                    audio = None
                # 重建预取缓冲，保证后续句仍从正确位置预取
                try:
                    self._edge_prefetch.close()
                except Exception:
                    pass
                self._edge_prefetch = _EdgePrefetch(
                    self._edge_synthesize, content, next_off, _EdgePrefetch.MAX_AHEAD
                )
        if audio:
            return self._speak_edge_play(audio, gen)
        # 联网失败：回退系统语音（仅提示一次）
        if not self._edge_fail_posted:
            self._edge_fail_posted = True
            self._post(
                {"type": "error", "message": "联网语音生成失败，本句已用系统语音朗读"}
            )
        try:
            self._edge_prefetch.close()
        except Exception:
            pass
        self._edge_prefetch = None
        return self._speak_sapi(text, gen)

    def _speak_edge_play(self, audio, gen):
        """用 Windows MCI（winmm.dll）播放预生成的 MP3，支持暂停/停止（零第三方依赖）。"""
        if not audio:
            return False
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp_path = tmp.name
            tmp.write(audio)
            tmp.flush()
            tmp.close()
            _mci_open(tmp_path)
            _mci_set_volume(self._volume)  # MCI 层音量（回退）
            _set_process_volume(self._volume)  # Core Audio 进程音量（主要）
            _mci_play()
            while _mci_playing():
                with self._cv:
                    if self._state == "idle" or self._book is None or self._gen != gen:
                        _mci_stop()
                        break
                    if self._state == "paused":
                        _mci_pause()
                        self._cv.wait()
                        if self._state == "idle" or self._book is None or self._gen != gen:
                            _mci_stop()
                            break
                        _mci_resume()
                time.sleep(0.03)
            _mci_close()
            return True
        except Exception as e:
            self._post({"type": "error", "message": f"播放出错：{e}"})
            return False
        finally:
            try:
                _mci_stop()
                _mci_close()
            except Exception:
                pass
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


# ---------- Windows MCI 播放器（winmm.dll，替代 pygame，零依赖） ----------
_MCI_ALIAS = "dd_tts_player"


def _mci_send(cmd):
    """发送 MCI 命令，返回 (错误码, 返回文本)。"""
    buf = ctypes.create_unicode_buffer(512)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 512, 0)
    return err, buf.value


def _mci_open(path):
    _mci_close()
    cmd = f'open "{path}" type mpegvideo alias {_MCI_ALIAS}'
    err, _ = _mci_send(cmd)
    if err != 0:
        raise RuntimeError(f"MCI open 失败 code={err}")


def _mci_set_volume(volume_0_100):
    """设置 MCI 音量（0-100 -> 0-1000）。"""
    v = max(0, min(1000, int(volume_0_100 * 10)))
    _mci_send(f"setaudio {_MCI_ALIAS} volume to {v}")


def _set_process_volume(volume_0_100):
    """通过 Windows Core Audio 设置当前进程音量（0-100）。优先使用 pycaw。"""
    if not _HAS_PYCAW:
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
    _mci_send(f"play {_MCI_ALIAS}")


def _mci_pause():
    _mci_send(f"pause {_MCI_ALIAS}")


def _mci_resume():
    _mci_send(f"play {_MCI_ALIAS}")


def _mci_stop():
    _mci_send(f"stop {_MCI_ALIAS}")


def _mci_close():
    _mci_send(f"close {_MCI_ALIAS}")


def _mci_playing():
    """查询是否仍在播放/暂停中。自然播放结束返回 False。"""
    _, mode = _mci_send(f"status {_MCI_ALIAS} mode")
    mode = mode.strip().lower()
    return mode in ("playing", "paused")

