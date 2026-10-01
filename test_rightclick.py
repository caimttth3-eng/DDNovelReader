import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from novelreader.gui import NovelReaderApp

root = tk.Tk()
app = NovelReaderApp(root)

root.update()
time.sleep(1)
root.update()

print("text exists:", hasattr(app, "text"))
print("text_menu exists:", hasattr(app, "text_menu"))
print("text state:", app.text["state"])
print("Button-3 binding:", app.text.bind("<Button-3>"))
print("text width:", app.text.winfo_width())
print("text height:", app.text.winfo_height())

# 检查 text_menu 的菜单项
print("text_menu entries:", app.text_menu.index("end"))

root.destroy()
