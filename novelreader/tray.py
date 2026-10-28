# -*- coding: utf-8 -*-
"""系统托盘：最小化缩入托盘（三平台，pystray）。

- 最小化（iconic）→ 隐藏主窗口并在系统托盘显示图标
- 托盘菜单：显示主窗口 / 退出（文案走 _T() 五语言）
- pystray 缺失或平台后端不可用时自动降级：最小化按普通行为，不影响主程序
"""
import os
import sys
import threading

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - 依赖缺失降级
    pystray = None
    Image = None

from novelreader.i18n import T as _T


def _resource_base():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TrayIcon:
    """绑定到主窗口的托盘图标。绑定失败 / 运行时异常一律不影响主程序。"""

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self._icon = None
        self._thread = None
        self._hidden = False
        self._quitting = False
        self._available = pystray is not None and Image is not None

    def bind(self):
        """绑定最小化事件与销毁清理。返回是否绑定成功。"""
        if not self._available:
            return False
        try:
            self.root.bind("<Unmap>", self._on_unmap)
            self.root.bind("<Destroy>", self._on_destroy)
            # 轮询 state 兜底：Windows 上外部最小化（最小化按钮/ShowWindow）不一定触发
            # Tk <Unmap>，只有轮询 state()=="iconic" 才能覆盖所有最小化路径
            self.root.after(300, self._poll_state)
            return True
        except Exception:
            return False

    def _poll_state(self):
        try:
            if self._quitting:
                return
            st = self.root.state()
            if st == "iconic" and not self._hidden:
                self._hidden = True
                self.root.withdraw()
                self._start_tray()
        except Exception:
            pass
        try:
            self.root.after(300, self._poll_state)
        except Exception:
            pass

    # ---------- 图标资源 ----------
    def _tray_image(self):
        # 优先复用软件主图标 assets/app.ico（与窗口图标一致），缩放 32px
        try:
            p = os.path.join(_resource_base(), "assets", "app.ico")
            if os.path.exists(p):
                img = Image.open(p)
                img = img.resize((32, 32), Image.LANCZOS)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                return img
        except Exception:
            pass
        # 兜底：蓝色圆角方块 + “读”
        try:
            img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([2, 2, 30, 30], radius=7, fill="#4C6FFF")
            try:
                font = ImageFont.truetype("msyh.ttc", 20)
                d.text((16, 16), "读", fill="white", anchor="mm", font=font)
            except Exception:
                d.text((12, 8), "读", fill="white")
            return img
        except Exception:
            return None

    # ---------- 托盘生命周期 ----------
    def _start_tray(self):
        if self._icon is not None or self._quitting:
            return
        try:
            img = self._tray_image()
            menu = pystray.Menu(
                pystray.MenuItem(_T("显示主窗口"), self._show, default=True),
                pystray.MenuItem(_T("退出"), self._quit),
            )
            self._icon = pystray.Icon("DDNovelReader", img, "多多朗读", menu)
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
        except Exception:
            self._icon = None
            self._thread = None

    def _stop_tray(self):
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        self._icon = None
        self._thread = None

    # ---------- 事件 ----------
    def _on_unmap(self, event):
        # withdraw() 自身会再触发一次 <Unmap>：此时 state 已是 withdrawn，不会重复进入
        try:
            if self._quitting:
                return
            if event.widget is not self.root:
                return
            if self.root.state() != "iconic":
                return
            self._hidden = True
            self.root.withdraw()
            self._start_tray()
        except Exception:
            pass

    def _on_destroy(self, event):
        # root.destroy() 时子控件也会触发 <Destroy>，只处理根窗口自身
        if event.widget is not self.root:
            return
        self._quitting = True
        self._stop_tray()

    # ---------- 菜单回调（pystray 线程 → 切回 Tk 主线程） ----------
    def _show(self, icon=None, item=None):
        try:
            self.root.after(0, self._restore)
        except Exception:
            pass

    def _restore(self):
        try:
            self._hidden = False
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            try:
                self.root.attributes("-topmost", True)
                self.root.update_idletasks()
                self.root.attributes("-topmost", False)
            except Exception:
                pass
            self.root.focus_force()
        except Exception:
            pass

    def _quit(self, icon=None, item=None):
        self._quitting = True
        try:
            self.root.after(0, self.app._on_close)
        except Exception:
            try:
                self.root.destroy()
            except Exception:
                pass

    def stop(self):
        """主程序退出时由外部调用。"""
        self._quitting = True
        self._stop_tray()
