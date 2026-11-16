# -*- coding: utf-8 -*-
"""朗读音频输出与朗读会话代际（2026-11-16 重写）。

这一层只解决三件事，且各自只有一个权威：

1. **谁在出声** —— `AudioBus` 是全进程唯一的音频输出。任何时刻至多一个流；
   `play()` 必须带会话令牌，令牌过期一律拒绝开播；`stop_now()` 在调用线程里
   立刻下发停止，不等轮询、不等旧会话退出。
2. **现在是第几次开读** —— `SessionClock` 单调自增的令牌。开读拿新令牌，掐断也
   拿新令牌；旧令牌的一切后续动作（合成返回、开播、事件）全部自动作废。
3. **事件属于谁** —— 引擎发出的每个事件都带令牌，UI 只接受当前令牌的事件。
   没有 pending 窗口、没有时间猜测、没有位置比对。

设计上不允许"旧会话自己判断该不该让位"这类分布式协商：令牌一变，旧会话的
所有写操作必然失败，不存在协商错漏。
"""

import threading


class SessionClock:
    """单调自增的会话令牌。开读 / 掐断都推进一代，旧代自动失效。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._token = 0

    @property
    def current(self):
        with self._lock:
            return self._token

    def next(self):
        with self._lock:
            self._token += 1
            return self._token


class AudioBus:
    """唯一音频输出通道：令牌准入 + 同步停止 + 幂等关闭。

    播放器后端（Windows=MCI / macOS·Linux=pygame）由 SpeechController 注入，
    本类不关心平台差异，只保证"同一时刻只有一个流、过期令牌永远开不了播"。
    """

    def __init__(self, open_fn, play_fn, pause_fn, resume_fn, stop_fn,
                 close_fn, playing_fn, volume_fn):
        self._lock = threading.RLock()
        self._open = open_fn
        self._play = play_fn
        self._pause = pause_fn
        self._resume = resume_fn
        self._stop = stop_fn
        self._close = close_fn
        self._playing = playing_fn
        self._volume = volume_fn
        self._token = 0          # 当前允许出声的令牌
        self._opened = False     # 是否已有打开的媒体
        self._path = None

    # ---------- 令牌 ----------
    def accept(self, token):
        """新会话接管输出：先同步静音上一个流，再把输出权交给新令牌。"""
        self.stop_now()
        with self._lock:
            self._token = int(token)

    def is_current(self, token):
        with self._lock:
            return self._token == int(token)

    # ---------- 播放 ----------
    def play(self, path, token, volume=None):
        """开播。令牌过期直接返回 False（连打开都不打开，杜绝旧句闪声）。"""
        with self._lock:
            if self._token != int(token):
                return False
            if volume is not None:
                try:
                    self._volume(volume)
                except Exception:
                    pass
            try:
                self._open(path)
                self._opened = True
                self._path = path
                self._play()
                return True
            except Exception:
                self._hard_close()
                raise

    def playing(self):
        with self._lock:
            if not self._opened:
                return False
            try:
                return bool(self._playing())
            except Exception:
                return False

    def pause(self):
        with self._lock:
            if not self._opened:
                return
            try:
                self._pause()
            except Exception:
                pass

    def resume(self):
        with self._lock:
            if not self._opened:
                return
            try:
                self._resume()
            except Exception:
                pass

    # ---------- 停止 ----------
    def stop_now(self):
        """同步停止并释放媒体（幂等）。调用线程立即生效，不需要等任何轮询。"""
        with self._lock:
            self._hard_close()

    def _hard_close(self):
        if self._opened:
            try:
                self._stop()
            except Exception:
                pass
            try:
                self._close()
            except Exception:
                pass
        self._opened = False
        self._path = None

    def set_volume(self, v):
        with self._lock:
            try:
                self._volume(v)
            except Exception:
                pass
