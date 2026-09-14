# -*- coding: utf-8 -*-
"""蓝牙耳机 / 键盘多媒体键控制（WM_APPCOMMAND 子类化捕获，仅 Windows）。

方案演进：
- v1: SetWindowLongPtr 替换 WndProc —— 与 Tk 窗口过程管理冲突，64 位 LRESULT 截断，闪退
- v2: WH_KEYBOARD_LL 低级键盘钩子 —— 真实媒体键事件触发进程级 fast fail（0xC000041D）
- v3: 纯轮询 GetAsyncKeyState —— 蓝牙 AVRCP 键不产生键状态位，功能失效
- v4: RegisterHotKey + WH_GETMESSAGE —— 媒体键热键被其他播放器(酷狗等)占用(1409)
- v5: SystemMediaTransportControls(SMTC) —— 需媒体会话服务，沙箱环境不可验证，vtable 签名不稳定，放弃
- v6（本版）: SetWindowSubclass 官方链式子类化捕获 WM_APPCOMMAND：
  * 纯系统 API（comctl32/user32 自带），零第三方依赖，非 Windows 完全不加载
  * 键盘媒体键 / 蓝牙 AVRCP 媒体键 → 系统产生 WM_APPCOMMAND(0x0319) 发给前台窗口
  * 子类回调只解析命令置标志位，绝不触碰 tkinter；主线程轮询消费 → 无崩溃路径
  * DefSubclassProc 正确转发其余消息，不破坏 Tk 消息循环（与 v1 替换式子类化本质不同）
  * 需要软件在前台（媒体键发给前台窗口）；酷狗等播放器运行并占用媒体键时归对方，不抢占
  * GetAsyncKeyState 物理多媒体键轮询兜底
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
# 媒体键去抖：系统对同一媒体键可能重复派发（按下/释放、AVRCP 双发），
# 350ms 内忽略同命令的第二次触发，避免一次按键被 toggle 两次
_DEBOUNCE = 0.35

_WM_APPCOMMAND = 0x0319
_APPCOMMAND_MEDIA_PLAY_PAUSE = 14
_APPCOMMAND_MEDIA_STOP = 13
_SUBCLASS_ID = 0xDDD0


class MediaKeysMixin:
    """多媒体键 → 朗读控制（播放/暂停切换、停止）。"""

    def _install_media_keys(self):
        self._mk_pending = {"pp": False, "stop": False}
        self._mk_last_pp = 0.0
        self._mk_last_stop = 0.0
        self._mk_polling = True
        self._mk_subclass = None
        if _WIN:
            try:
                self._mk_subclass_init()
            except Exception:
                self._mk_subclass = None
        self.root.after(_POLL_MS, self._poll_media_keys)

    # ---------- WM_APPCOMMAND 子类化 ----------
    def _mk_subclass_init(self):
        comctl32 = ctypes.WinDLL("comctl32.dll", use_last_error=False)

        # LRESULT CALLBACK SubclassProc(HWND, UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR)
        SUBCLASSPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
            ctypes.c_size_t, ctypes.c_size_t)
        DEFSUBCLASSPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        def_addr = ctypes.cast(comctl32.DefSubclassProc, ctypes.c_void_p).value

        def _def(hwnd, msg, wp, lp, _addr=def_addr):
            return DEFSUBCLASSPROC(_addr)(hwnd, msg, wp, lp)

        def _subclass_proc(hwnd, msg, wp, lp, uid, refdata):
            try:
                if msg == _WM_APPCOMMAND:
                    cmd = (lp >> 16) & 0x0FFF
                    if cmd == _APPCOMMAND_MEDIA_PLAY_PAUSE:
                        self._mk_pending["pp"] = True
                        return 0
                    if cmd == _APPCOMMAND_MEDIA_STOP:
                        self._mk_pending["stop"] = True
                        return 0
            except Exception:
                pass
            try:
                return _def(hwnd, msg, wp, lp)
            except Exception:
                return 0

        self._mk_subclass_proc = SUBCLASSPROC(_subclass_proc)

        hwnd = self.root.winfo_id()
        comctl32.SetWindowSubclass.argtypes = [wt.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
        comctl32.SetWindowSubclass.restype = wt.BOOL
        ok = comctl32.SetWindowSubclass(hwnd, self._mk_subclass_proc, _SUBCLASS_ID, 0)
        if not ok:
            raise OSError("SetWindowSubclass failed")
        self._mk_subclass_hwnd = hwnd
        self._mk_subclass = True

    # ---------- 轮询消费 ----------
    def _poll_media_keys(self):
        if not getattr(self, "_mk_polling", False):
            return
        try:
            import time as _t
            pp = self._mk_pending.get("pp", False)
            st = self._mk_pending.get("stop", False)
            if pp or st:
                self._mk_pending["pp"] = False
                self._mk_pending["stop"] = False
            now = _t.time()
            if pp:
                if now - self._mk_last_pp < _DEBOUNCE:
                    pp = False
                else:
                    self._mk_last_pp = now
            if st:
                if now - self._mk_last_stop < _DEBOUNCE:
                    st = False
                else:
                    self._mk_last_stop = now
            if pp:
                try:
                    self._tts_toggle()
                except Exception:
                    pass
            if st:
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

    def _on_media_keys_destroy(self, event=None):
        """窗口销毁：卸载子类化。"""
        self._mk_polling = False
        if not _WIN or not getattr(self, "_mk_subclass", None):
            return
        try:
            comctl32 = ctypes.WinDLL("comctl32.dll", use_last_error=False)
            comctl32.RemoveWindowSubclass.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_size_t]
            comctl32.RemoveWindowSubclass.restype = wt.BOOL
            proc_addr = ctypes.cast(self._mk_subclass_proc, ctypes.c_void_p).value
            comctl32.RemoveWindowSubclass(self._mk_subclass_hwnd, proc_addr, _SUBCLASS_ID)
        except Exception:
            pass
        self._mk_subclass = None
