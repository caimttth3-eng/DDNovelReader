import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from novelreader.gui import NovelReaderApp

root = tk.Tk()
app = NovelReaderApp(root)
root.update()
time.sleep(0.5)
root.update()

# 模拟右键事件
class FakeEvent:
    x = 100
    y = 100
    x_root = 500
    y_root = 500

try:
    app._popup_text_menu(FakeEvent())
    print("调用成功，菜单应该弹出来了")
    root.update()
    time.sleep(1)
except Exception as e:
    print(f"调用报错: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

root.destroy()
