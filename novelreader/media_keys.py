# -*- coding: utf-8 -*-
"""蓝牙耳机 / 键盘多媒体键控制（WM_APPCOMMAND 捕获，仅 Windows）。

Windows 在按下多媒体键（播放/暂停等）时，会向「前台焦点窗口」发送
WM_APPCOMMAND 消息；这里子类化主窗口过程拦截该消息，映射到朗读的
开始/暂停/继续与停止。macOS / Linux 无此机制，自动跳过。

注意：仅当本程序为前台窗口时才会收到多媒体键消息（与主流播放器一致）。
"""
import ctypes
import sys

try:
    import ctypes.wintypes as wt
    _WIN = sys.platform.startswith("win")
except Exception:  # pragma: no cover
    _WIN = False

_WM_APPCOMMAND = 0x0319
_APPCOMMAND_MEDIA_PLAY_PAUSE = 14
_APPCOMMAND_MEDIA_STOP = 13
_FAPPCOMMAND_MASK = 0xF000
_GWLP_WNDPROC = -4


class MediaKeysMixin:
    """多媒体键 → 朗读控制（播放/暂停切换、停止）。"""

    def _install_media_keys(self):
        """为 Windows 主窗口挂 WM_APPCOMMAND 钩子（非 Windows 自动跳过）。"""
        if not _WIN:
            return
        try:
            user32 = ctypes.windll.user32
            # 64 位下 LONG_PTR 必须显式声明，否则指针被截断导致钩子失效
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
                user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            else:
                user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_int]
                user32.SetWindowLongW.restype = ctypes.c_int
            user32.CallWindowProcW.argtypes = [
                ctypes.c_ssize_t, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
            user32.CallWindowProcW.restype = ctypes.c_long

            hwnd = self.root.winfo_id()
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)

            def _wnd_proc(hwnd_, msg, wparam, lparam):
                if msg == _WM_APPCOMMAND:
                    # 命令码在 lParam 高 16 位（去掉 4 位标志位）
                    cmd = (lparam >> 16) & ~_FAPPCOMMAND_MASK
                    if cmd == _APPCOMMAND_MEDIA_PLAY_PAUSE:
                        self.root.after(0, self._tts_toggle)
                        return 0
                    if cmd == _APPCOMMAND_MEDIA_STOP:
                        self.root.after(0, self._tts_stop)
                        return 0
                if getattr(self, "_mk_old_proc", None) is not None:
                    return user32.CallWindowProcW(
                        self._mk_old_proc, hwnd_, msg, wparam, lparam)
                return user32.DefWindowProcW(hwnd_, msg, wparam, lparam)

            self._mk_proc = WNDPROC(_wnd_proc)
            proc_ptr = ctypes.cast(self._mk_proc, ctypes.c_void_p).value
            self._mk_hwnd = hwnd
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                self._mk_old_proc = user32.SetWindowLongPtrW(
                    hwnd, _GWLP_WNDPROC, proc_ptr)
            else:
                self._mk_old_proc = user32.SetWindowLongW(
                    hwnd, _GWLP_WNDPROC, proc_ptr)
            # 窗口销毁时恢复原窗口过程，避免指针失效
            self.root.bind("<Destroy>", self._on_media_keys_destroy, add="+")
        except Exception:
            self._mk_proc = None
            self._mk_old_proc = None

    def _on_media_keys_destroy(self, event):
        """根窗口销毁时恢复原 WndProc。"""
        if getattr(self, "_mk_old_proc", None) is None:
            return
        try:
            if getattr(event, "widget", None) is not self.root:
                return
            user32 = ctypes.windll.user32
            hwnd = getattr(self, "_mk_hwnd", None)
            if hwnd:
                if ctypes.sizeof(ctypes.c_void_p) == 8:
                    user32.SetWindowLongPtrW(hwnd, _GWLP_WNDPROC, self._mk_old_proc)
                else:
                    user32.SetWindowLongW(hwnd, _GWLP_WNDPROC, self._mk_old_proc)
        except Exception:
            pass
