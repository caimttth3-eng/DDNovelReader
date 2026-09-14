# -*- coding: utf-8 -*-
"""蓝牙耳机 / 键盘多媒体键控制（轮询方案，仅 Windows）。

方案演进：
- v1: SetWindowLongPtr 替换 WndProc 捕获 WM_APPCOMMAND —— 64 位 LRESULT 截断损坏
  tkinter 消息循环（启动卡加载、播放闪退，0xC0000409）
- v2: WH_KEYBOARD_LL 低级键盘钩子 —— 真实媒体键事件在 ctypes 回调层触发系统级
  崩溃（0xC000041D STATUS_FATAL_USER_CALLBACK_EXCEPTION，Python 无法捕获）
- v3（本版）: 纯主线程轮询 GetAsyncKeyState 的「按下位」(bit0)：
  * 零系统回调、零钩子、零窗口过程替换 → 不可能 fast fail / 闪退
  * bit0 = 自上次查询以来被按下过，消息被消费也不会丢失，不漏检
  * 仅前台窗口时响应，与主流播放器一致
"""
import ctypes
import sys

try:
    import ctypes.wintypes as wt  # noqa: F401
    _WIN = sys.platform.startswith("win")
except Exception:  # pragma: no cover
    _WIN = False

_VK_MEDIA_STOP = 0xB2
_VK_MEDIA_PLAY_PAUSE = 0xB3
_POLL_MS = 150


class MediaKeysMixin:
    """多媒体键 → 朗读控制（播放/暂停切换、停止）。"""

    def _install_media_keys(self):
        """启动轮询（非 Windows 自动跳过）。"""
        if not _WIN:
            return
        try:
            self._mk_polling = True
            self._mk_hwnd = self.root.winfo_id()
            self.root.after(_POLL_MS, self._poll_media_keys)
        except Exception:
            self._mk_polling = False

    def _media_keys_foreground(self):
        """仅前台窗口响应媒体键（与主流播放器一致）。"""
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            return bool(fg) and fg == getattr(self, "_mk_hwnd", None)
        except Exception:
            return True

    def _poll_media_keys(self):
        """定时轮询媒体键按下位（bit0），检测到即触发朗读控制。"""
        if not getattr(self, "_mk_polling", False):
            return
        try:
            if self._media_keys_foreground():
                user32 = ctypes.windll.user32
                if user32.GetAsyncKeyState(_VK_MEDIA_PLAY_PAUSE) & 0x0001:
                    try:
                        self._tts_toggle()
                    except Exception:
                        pass
                if user32.GetAsyncKeyState(_VK_MEDIA_STOP) & 0x0001:
                    try:
                        self._tts_stop()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self.root.after(_POLL_MS, self._poll_media_keys)
        except Exception:
            self._mk_polling = False

    def _on_media_keys_destroy(self, event):
        """窗口销毁时停止轮询（兼容旧绑定，当前无需额外清理）。"""
        if getattr(event, "widget", None) is self.root:
            self._mk_polling = False
