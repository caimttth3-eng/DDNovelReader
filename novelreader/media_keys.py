# -*- coding: utf-8 -*-
"""蓝牙耳机 / 键盘多媒体键控制（WH_KEYBOARD_LL 低级键盘钩子，仅 Windows）。

方案说明：早期版本用 SetWindowLongPtr 替换主窗口 WndProc 捕获 WM_APPCOMMAND，
实测在 tkinter 下导致 64 位 LRESULT 截断、消息循环损坏（启动卡加载、播放闪退）。
改为低级键盘钩子捕获 VK_MEDIA_PLAY_PAUSE / VK_MEDIA_STOP：
- 不碰窗口过程，与 tkinter 完全解耦，稳定无侵入
- 仅当本程序窗口为前台时响应（与主流播放器一致），非前台放行系统继续处理
- 蓝牙耳机 / 键盘媒体键在 Windows 上会映射为这些媒体虚拟键
"""
import ctypes
import sys

try:
    import ctypes.wintypes as wt
    _WIN = sys.platform.startswith("win")
except Exception:  # pragma: no cover
    _WIN = False

_WH_KEYBOARD_LL = 13
_VK_MEDIA_STOP = 0xB2
_VK_MEDIA_PLAY_PAUSE = 0xB3
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MediaKeysMixin:
    """多媒体键 → 朗读控制（播放/暂停切换、停止）。"""

    def _install_media_keys(self):
        """安装低级键盘钩子（非 Windows 自动跳过）。"""
        if not _WIN:
            return
        try:
            user32 = ctypes.windll.user32
            LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM,
                ctypes.POINTER(_KBDLLHOOKSTRUCT))
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t

            self._mk_hook = None

            def _hook_proc(n_code, w_param, l_param):
                try:
                    if n_code >= 0 and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                        kbd = l_param.contents
                        if kbd.vkCode == _VK_MEDIA_PLAY_PAUSE:
                            if self._media_keys_foreground():
                                self.root.after(0, self._tts_toggle)
                                return 1  # 吞掉，避免系统再转发
                        elif kbd.vkCode == _VK_MEDIA_STOP:
                            if self._media_keys_foreground():
                                self.root.after(0, self._tts_stop)
                                return 1
                except Exception:
                    pass
                return user32.CallNextHookEx(
                    self._mk_hook, n_code, w_param,
                    ctypes.cast(l_param, ctypes.c_void_p).value)

            self._mk_proc = LowLevelKeyboardProc(_hook_proc)
            self._mk_hwnd = self.root.winfo_id()
            self._mk_hook = user32.SetWindowsHookExW(
                _WH_KEYBOARD_LL, self._mk_proc, None, 0)
            if not self._mk_hook:
                self._mk_proc = None
                return
            self.root.bind("<Destroy>", self._on_media_keys_destroy, add="+")
        except Exception:
            self._mk_hook = None
            self._mk_proc = None

    def _media_keys_foreground(self):
        """仅前台窗口响应媒体键（与主流播放器一致）。"""
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            return bool(fg) and fg == getattr(self, "_mk_hwnd", None)
        except Exception:
            return True

    def _on_media_keys_destroy(self, event):
        """窗口销毁时卸载钩子。"""
        try:
            if getattr(event, "widget", None) is not self.root:
                return
            hook = getattr(self, "_mk_hook", None)
            if hook:
                ctypes.windll.user32.UnhookWindowsHookEx(hook)
                self._mk_hook = None
        except Exception:
            pass
