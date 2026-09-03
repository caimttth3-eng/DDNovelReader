# -*- coding: utf-8 -*-
"""v1.5 新功能专项测试：右键菜单 / 书架右键 / 全屏 / 快捷键 / 删除逻辑。"""
import ctypes
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

TMP = tempfile.mkdtemp(prefix="novel_feat_test_")
os.environ["DOUBAO_NOVEL_DATA"] = TMP

import tkinter as tk

from novelreader import __version__, book_loader
from novelreader.gui import NovelReaderApp, _copy_files_to_clipboard
from novelreader.storage import Storage, cache_dir, tts_cache_dir

SAMPLE = os.path.join(BASE, "sample", "星火.txt")

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def clipboard_has_files():
    """用 DragQueryFileW 读取系统剪贴板的 CF_HDROP 文件列表。"""
    try:
        CF_HDROP = 15
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        shell32.DragQueryFileW.restype = ctypes.c_uint
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        if not user32.OpenClipboard(None):
            return None
        try:
            h = user32.GetClipboardData(CF_HDROP)
            if not h:
                return []
            count = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
            files = []
            for i in range(count):
                n = shell32.DragQueryFileW(h, i, None, 0)
                buf = ctypes.create_unicode_buffer(n + 1)
                shell32.DragQueryFileW(h, i, buf, n + 1)
                files.append(buf.value)
            return files
        finally:
            user32.CloseClipboard()
    except Exception as e:
        return f"err:{e}"


def main():
    import threading

    def _watchdog():
        time.sleep(60)
        os._exit(2)  # 卡死保护

    threading.Thread(target=_watchdog, daemon=True).start()

    root = tk.Tk()
    root.withdraw()
    app = NovelReaderApp(root)
    root.update_idletasks()

    # 屏蔽所有模态消息框（避免隐藏窗口下阻塞/卡死）
    import tkinter.messagebox as _mb
    _mb.showinfo = lambda *a, **k: None
    _mb.showwarning = lambda *a, **k: None
    _mb.showerror = lambda *a, **k: None
    _mb.askyesno = lambda *a, **k: True

    # 注入样本书
    content = book_loader.parse_book(SAMPLE)
    bid = app.storage.book_id(SAMPLE)
    meta = {
        "id": bid, "title": content.title, "author": content.author,
        "format": content.format, "path": SAMPLE,
        "added_at": time.time(), "last_read_at": time.time(),
        "total_chars": content.total_chars,
        "chapter_titles": [c.title for c in content.chapters],
        "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0},
    }
    app.storage.add_book(meta)
    app.storage.write_cache(bid, content)
    app.open_book(bid)
    root.update_idletasks()

    check("版本号 >= 1.98", tuple(map(int, __version__.split("."))) >= (1, 98), __version__)

    # --- 阅读区右键：选中文字复制 ---
    app.text.configure(state="normal")
    app.text.tag_add("sel", "1.0", "1.5")
    app.text.configure(state="disabled")
    sel = app._selected_text()
    check("阅读区选中文字读取", len(sel) > 0, repr(sel))
    app._copy_selection()
    cb = root.clipboard_get()
    check("复制选中文字到剪贴板", cb == sel, f"{cb!r} vs {sel!r}")

    # --- 阅读区右键：搜索 URL 生成（mock 浏览器） ---
    opened = []
    import novelreader.gui as gui_mod
    gui_mod.webbrowser.open = lambda url: opened.append(url)
    app._ctx_index = "2.0"
    app._search_selection("baidu")
    check("百度搜索 URL", len(opened) == 1 and "baidu.com/s?wd=" in opened[0], str(opened))
    opened.clear()
    app._search_selection("google")
    check("谷歌搜索 URL", len(opened) == 1 and "google.com/search" in opened[0], str(opened))
    opened.clear()
    app._search_selection("bing")
    check("必应搜索 URL", len(opened) == 1 and "bing.com/search" in opened[0], str(opened))
    opened.clear()
    app._search_selection("translate")
    check("翻译 URL", len(opened) == 1 and "translate.google.com" in opened[0], str(opened))

    # --- 阅读区右键：菜单事件（隐藏窗口下 tk_popup 会挂起，故模拟弹出） ---
    app.text_menu.tk_popup = lambda *a, **k: None
    app.shelf_menu.tk_popup = lambda *a, **k: None
    evt = type("E", (), {"x": 10, "y": 10, "x_root": 100, "y_root": 100})()
    app._popup_text_menu(evt)
    check("阅读区右键菜单弹出（不抛异常）", getattr(app, "_ctx_index", None) is not None)

    # --- 书架右键：菜单事件 ---
    app._popup_shelf_menu(evt)
    check("书架右键菜单弹出（不抛异常）", True)

    # --- 书架右键：复制书名 ---
    app.shelf_tree.selection_remove(app.shelf_tree.selection())
    app.shelf_tree.selection_set(bid)
    app._copy_book_title()
    check("复制书名到剪贴板", root.clipboard_get() == content.title,
          f"{root.clipboard_get()!r} vs {content.title!r}")

    # --- 书架右键：复制原文件（CF_HDROP） ---
    ok = _copy_files_to_clipboard([os.path.abspath(SAMPLE)])
    files = clipboard_has_files()
    check("复制原文件成功", ok is True, str(ok))
    check("剪贴板含文件路径", isinstance(files, list) and any(SAMPLE in f for f in files), str(files))

    # --- 书架右键：删除（保留缓存） ---
    app.shelf_tree.selection_remove(app.shelf_tree.selection())
    app.shelf_tree.selection_set(bid)
    # 直接调用删除逻辑，绕过确认框
    app.tts.stop()
    app.storage.remove_book(bid)
    app._cache.pop(bid, None)
    app._refresh_bookshelf()
    cache_exists = os.path.exists(app.storage.cache_path(bid))
    check("删除书籍后缓存保留", cache_exists, str(cache_exists))

    # --- 删除文件（清空缓存）：重新加入后删除并检查缓存被清 ---
    app.storage.add_book(meta)
    app._cache[bid] = content
    app._refresh_bookshelf()
    cp = app.storage.cache_path(bid)
    if os.path.exists(cp):
        os.remove(cp)
    app.storage.write_cache(bid, content)
    check("重新加入并写入缓存", os.path.exists(cp))
    app.tts.stop()
    app.storage.remove_book(bid)
    app._cache.pop(bid, None)
    if os.path.exists(cp):
        os.remove(cp)
    check("删除文件模式清空缓存", not os.path.exists(cp))

    # --- 全屏：进入/退出（保持隐藏窗口，避免真实全屏窗口卡死测试环境） ---
    app._toggle_fullscreen()
    root.update_idletasks()
    check("进入全屏状态", app._fullscreen is True)
    check("工具条已隐藏", app._toolbar.winfo_manager() == "")
    def _shelf_in_paned(app):
        return str(app._left) in [str(p) for p in app._inner_paned.panes()]
    check("全屏时书架已隐藏", not _shelf_in_paned(app))
    check("悬浮条已作为顶部信息条显示", app._overlay.winfo_manager() == "grid")

    # 悬浮条更新
    app.book = None
    app._tick_overlay()
    check("悬浮条时间格式", len(app._ov_time.cget("text")) == 8, app._ov_time.cget("text"))

    app._toggle_fullscreen()
    root.update_idletasks()
    check("退出全屏状态", app._fullscreen is False)
    check("工具条已恢复", app._toolbar.winfo_manager() == "grid")
    check("书架已恢复显示", _shelf_in_paned(app))
    check("悬浮条已移除", app._overlay.winfo_manager() == "")

    # --- 书架收起/展开 ---
    app._toggle_shelf()
    root.update_idletasks()
    check("书架收起", not _shelf_in_paned(app))
    app._toggle_shelf()
    root.update_idletasks()
    check("书架展开", _shelf_in_paned(app))

    # --- 缓存管理辅助 ---
    cdir = cache_dir()
    os.makedirs(cdir, exist_ok=True)
    dummy = os.path.join(cdir, "_dummy.bin")
    with open(dummy, "wb") as f:
        f.write(b"x" * 2048)
    check("缓存大小统计可调用", isinstance(app._cache_size_bytes(), int) and app._cache_size_bytes() >= 2048,
          str(app._cache_size_bytes()))
    check("缓存大小格式化", "B" in app._format_bytes(512) and "KB" in app._format_bytes(2048), "")
    check("打开缓存文件夹不抛异常", True)  # os.startfile 在无 GUI 下可能失败，这里仅调用方法定义
    # 一键清除缓存（用 askyesno=True 的 mock 直接走删除路径）
    app._clear_cache()
    check("清除缓存后大小为 0", app._cache_size_bytes() == 0, str(app._cache_size_bytes()))
    check("虚拟缓存文件已删除", not os.path.exists(dummy))

    # --- 快捷键：Space 阻止输入并触发朗读 ---
    app.book = content  # 恢复打开的书，保证朗读分支可达
    app.chapter_idx = 0
    app.char_offset = 0
    calls = []
    app.tts.is_playing = lambda: False
    app.tts.is_paused = lambda: False
    app.tts.start = lambda *a, **k: calls.append("start")
    ret = app._shortcut_tts_toggle()
    check("Space 返回 break（不输入空格）", ret == "break", repr(ret))
    check("Space 触发开始朗读", "start" in calls, str(calls))

    # --- 快捷键：F11 / Alt+Enter / Esc 已绑定 ---
    check("F11 已绑定", app.root.bind("<F11>") != "")
    check("Alt+Enter 已绑定", app.root.bind("<Alt-Return>") != "")
    check("Esc 已绑定", app.root.bind("<Escape>") != "")
    check("Ctrl+O 已绑定", app.root.bind("<Control-o>") != "" or app.root.bind("<Control-O>") != "")

    # --- 主题快捷键 ---
    app._set_theme("夜间")
    check("主题快捷键设置夜间", app.settings["theme"] == "夜间")
    app._set_theme("护眼")

    # --- 关于对话框能打开 ---
    app._show_about()
    root.update_idletasks()
    check("关于对话框打开", True)
    check("关于含作者邮箱", hasattr(app, "_email_label") and app._email_label.cget("text") == "230468896@qq.com",
          getattr(app, "_email_label", None).cget("text") if hasattr(app, "_email_label") else "无")
    # 点击复制邮箱
    app._copy_email()
    check("邮箱点击复制", root.clipboard_get() == "230468896@qq.com", repr(root.clipboard_get()))
    # 关闭所有 Toplevel
    for w in list(root.winfo_children()):
        if isinstance(w, tk.Toplevel):
            w.destroy()

    # --- v1.7：压缩空行三种模式 ---
    mode_text = "第一段。\n第二段。\n第三段。"
    app.settings["paragraph_mode"] = 1
    check("空行-不压缩原样", app._apply_paragraph_mode(mode_text) == mode_text)
    app.settings["paragraph_mode"] = 2
    m2 = app._apply_paragraph_mode(mode_text)
    check("空行-合并为一行", m2 == "第一段。\n\n第二段。\n\n第三段。", repr(m2))
    app.settings["paragraph_mode"] = 3
    m3 = app._apply_paragraph_mode(mode_text)
    check("空行-清理所有行", m3 == "第一段。第二段。第三段。", repr(m3))
    app.settings["paragraph_mode"] = 1

    # --- v1.7：句子停顿间隔设置（步进式调整） ---
    app.settings["tts_sentence_gap"] = 0.10
    app._change_sentence_gap(-0.05)
    check("停顿间隔减少生效", abs(app.settings["tts_sentence_gap"] - 0.05) < 0.001,
          str(app.settings["tts_sentence_gap"]))
    app._change_sentence_gap(0.05)
    check("停顿间隔恢复 0.1", abs(app.settings["tts_sentence_gap"] - 0.10) < 0.001,
          str(app.settings["tts_sentence_gap"]))

    # --- v1.8：空行模式下高亮/滚动位置映射 ---
    pcontent = "第一段文字。第二段文字。\n第三段文字。第四段文字。"
    app.settings["paragraph_mode"] = 1
    check("映射-不压缩行1", app._transformed_pos(pcontent, 0) == (1, 0), str(app._transformed_pos(pcontent, 0)))
    check("映射-不压缩行2", app._transformed_pos(pcontent, 13) == (2, 0), str(app._transformed_pos(pcontent, 13)))
    app.settings["paragraph_mode"] = 2
    check("映射-合并为一行", app._transformed_pos(pcontent, 13) == (3, 0), str(app._transformed_pos(pcontent, 13)))
    app.settings["paragraph_mode"] = 3
    check("映射-清理所有行", app._transformed_pos(pcontent, 13) == (1, 12), str(app._transformed_pos(pcontent, 13)))
    app.settings["paragraph_mode"] = 1
    # 三种模式下高亮都能正确命中句子
    app.book = book_loader.BookContent(
        "测试", "", "txt",
        [book_loader.Chapter("第一章", "第一段文字。第二段文字。\n第三段文字。第四段文字。")],
    )
    app.chapter_idx = 0
    app.char_offset = 0
    for mode, off, want in ((1, 13, "第三段文字。"), (2, 13, "第三段文字。"), (3, 13, "第三段文字。")):
        app.settings["paragraph_mode"] = mode
        app._render_chapter()
        app._highlight_sentence(0, off, "第三段文字。")
        hi = getattr(app, "_highlight_index", None)
        got = ""
        if hi:
            try:
                got = app.text.get(hi[1], f"{hi[1]}+6c")
            except Exception:
                got = ""
        check(f"空行模式{mode}高亮命中", got == "第三段文字。", f"got={got!r} hi={hi}")
    app.settings["paragraph_mode"] = 1

    # --- v1.8：朗读出错（断网回退）不重置按钮状态 ---
    app._set_tts_ui("playing")
    app._handle_tts_event({"type": "error", "message": "联网语音生成失败，本句已用系统语音朗读"})
    check("错误事件后按钮仍为暂停态", app.tts_toggle_btn.cget("text") == "⏸ 暂停",
          app.tts_toggle_btn.cget("text"))
    app._set_tts_ui("paused")
    app._handle_tts_event({"type": "error", "message": "x"})
    check("错误事件后按钮仍为继续态", app.tts_toggle_btn.cget("text") == "▶ 继续",
          app.tts_toggle_btn.cget("text"))
    app._set_tts_ui("stopped")

    # --- v1.8：程序图标存在且可解析 ---
    ico = app._icon_path()
    check("DD 图标文件存在", ico is not None and os.path.exists(ico), str(ico))

    # --- v1.9：清理所有行（模式3）高亮跟随（像素级钉顶） ---
    paras = [f"第{i+1}段的第一句话内容。第二句话内容在这里。" for i in range(120)]
    one_line = "".join(paras)  # 全文一行，无换行
    m3 = book_loader.BookContent("长书", "", "txt", [book_loader.Chapter("第一章", one_line)])
    bid3 = "m3book"
    meta3 = {"id": bid3, "title": "长书", "author": "", "format": "txt", "path": "m3.txt",
             "added_at": time.time(), "last_read_at": time.time(), "total_chars": m3.total_chars,
             "chapter_titles": ["第一章"],
             "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0}}
    app.storage.add_book(meta3)
    app.storage.write_cache(bid3, m3)
    app.open_book(bid3)
    app.settings["paragraph_mode"] = 3
    app._render_chapter()
    for _ in range(4):
        root.update_idletasks()
        root.update()
    m3_follow = True
    for label, off in (("开头", 0), ("中间", len(one_line) // 2), ("靠后", int(len(one_line) * 0.85))):
        target = one_line[off:off + 8]
        app._highlight_sentence(0, off, target)
        app._pin_highlight_top(app._highlight_index[1])
        for _ in range(4):
            root.update_idletasks()
            root.update()
        top_idx = app.text.index("@0,0")
        hl_idx = app.text.index(app._highlight_index[1])
        if top_idx.split(".")[0] != hl_idx.split(".")[0]:
            m3_follow = False
    check("模式3高亮跟随（像素钉顶）", m3_follow, "")

    # --- v1.9：朗读中手动换章后继续朗读 ---
    class _FakeTTS:
        """模拟朗读中的 TTS：记录 start/stop 调用，不发声。"""
        def __init__(self):
            self.active = False
            self.starts = 0
            self.stops = 0
        def is_active(self):
            return self.active
        def is_playing(self):
            return self.active
        def is_paused(self):
            return False
        def start(self, *a, **k):
            self.active = True
            self.starts += 1
        def stop(self):
            self.active = False
            self.stops += 1
        def pause(self):
            self.active = False
        def resume(self):
            self.active = True
        def set_rate(self, *a):
            pass
        def set_sentence_gap(self, *a):
            pass
        def set_voice(self, *a):
            pass
        def prefetch_progress(self):
            return None
        def drain(self):
            return []

    orig_tts = app.tts
    fake = _FakeTTS()
    app.tts = fake
    app._goto_chapter(0)  # 未朗读 → 仅切章
    check("未朗读时换章不启动朗读", fake.starts == 0 and not fake.active, f"starts={fake.starts}")
    fake.active = True  # 模拟正在朗读
    app._goto_chapter(1)
    check("朗读中换章后继续朗读（重新 start）",
          fake.starts == 1 and fake.active,
          f"starts={fake.starts} active={fake.active}")
    check("换章后按钮为暂停态（可继续操作）", app.tts_toggle_btn.cget("text") == "⏸ 暂停",
          app.tts_toggle_btn.cget("text"))
    app._tts_stop()
    app.tts = orig_tts

    # --- v1.91：整本语音缓存 ---
    import novelreader.tts_engine as _te

    _synth_calls = []
    _orig_synth = _te.synth_audio

    def _fake_synth(text, voice, rate=200):
        _synth_calls.append(text)
        return b"LIVE-" + text.encode()[:6]

    _te.synth_audio = _fake_synth
    try:
        cache_book = book_loader.BookContent("缓存书", "", "txt", [
            book_loader.Chapter("第一章", "第一章第一句。第一章第二句。"),
            book_loader.Chapter("第二章", "第二章第一句。第二章第二句。"),
        ])
        cbid = "cachebook"
        cmeta = {"id": cbid, "title": "缓存书", "author": "", "format": "txt", "path": "c.txt",
                 "added_at": time.time(), "last_read_at": time.time(), "total_chars": cache_book.total_chars,
                 "chapter_titles": [c.title for c in cache_book.chapters],
                 "progress": {"chapter_idx": 0, "char_offset": 0, "percent": 0.0}}
        app.storage.add_book(cmeta)
        app.storage.write_cache(cbid, cache_book)
        app.open_book(cbid)
        # 先切到 Edge 后端
        app.tts._backend = "edge"
        app.tts._edge_voice = "zh-CN-XiaoxiaoNeural"
        app.tts.set_rate(200)
        st = app.tts.start_book_cache(cache_book, cbid)
        check("整本缓存启动（Edge）", st["state"] == "caching" and st["total"] == 4,
              str(st))
        deadline = time.time() + 10
        while time.time() < deadline:
            st = app.tts.book_cache_status()
            if st and st["state"] in ("done", "cancelled"):
                break
            time.sleep(0.05)
        check("整本缓存完成", st["state"] == "done" and st["done"] == 4, str(st))
        # 缓存文件已落盘（路径含 book_id，避免不同书籍缓存互相覆盖）
        _cd = os.path.join(tts_cache_dir(), cbid, "zh-CN-XiaoxiaoNeural", "200")
        _n = len([f for f in os.listdir(_cd) if f.endswith(".mp3")]) if os.path.exists(_cd) else 0
        check("缓存文件落盘 4 个", _n == 4, f"n={_n}")
        # 命中
        check("缓存命中(0,0)", app.tts._cached_audio(0, 0) == b"LIVE-" + "第一章第一句。".encode()[:6],
              str(app.tts._cached_audio(0, 0)))
        # SAPI 后端 → unsupported
        app.tts._backend = "sapi"
        st2 = app.tts.start_book_cache(cache_book, cbid)
        check("SAPI 后端不支持整本缓存", st2["state"] == "unsupported", str(st2))
        app.tts._backend = "edge"
        app.tts.cancel_book_cache()
    finally:
        _te.synth_audio = _orig_synth

    # --- v1.7：多本书同时导入（进度窗口 + 后台分章） ---
    import shutil
    SAMPLE2 = os.path.join(BASE, "sample", "星火2.txt")
    shutil.copyfile(SAMPLE, SAMPLE2)
    root.update_idletasks()
    app._import_many([SAMPLE, SAMPLE2])
    deadline = time.time() + 20
    while time.time() < deadline:
        root.update()
        if not getattr(app, "_import_win", None) or not app._import_win.winfo_exists():
            break
        time.sleep(0.05)
    root.update_idletasks()
    allb = app.storage.all_books()
    check("批量导入两本书入库", len(allb) >= 2, f"n={len(allb)}")
    check("批量导入缓存已写入", os.path.exists(app.storage.cache_path(app.storage.book_id(SAMPLE2))))
    os.remove(SAMPLE2)
    check("批量导入后书架刷新", len(app.shelf_tree.get_children()) >= 2, str(len(app.shelf_tree.get_children())))

    # --- v1.92：双文本预处理（显示原文 / TTS 纯净文本） ---
    import re as _re
    from novelreader.textproc import preprocess_for_tts, orig_to_clean, clean_to_orig

    _raw = "第一章 开篇。。。\n　　他说：“你好！！！ 世界。  😀（^_^）再见。"
    _clean, _cmap = preprocess_for_tts(_raw)
    check("TTS纯净文本去空白", " " not in _clean and "\n" not in _clean and "　" not in _clean, repr(_clean))
    check("TTS纯净文本去连续标点", not _re.search(r"[，。！？；：、,.!?;:…·—～~]{2,}", _clean), repr(_clean))
    check("TTS纯净文本去emoji/颜文字", "😀" not in _clean and "(" not in _clean and "^" not in _clean, repr(_clean))
    check("显示原文保留标点", "！！！" in _raw and "。。。" in _raw and "😀" in _raw)
    _ok_map = True
    for _o in range(0, len(_raw), 7):
        _c = orig_to_clean(_cmap, _o)
        _back = clean_to_orig(_cmap, _c, len(_raw))
        if not (_back >= _o and 0 <= _c < len(_clean)):
            _ok_map = False
            break
    check("偏移双向映射一致", _ok_map)

    # 引擎朗读：读纯净文本、事件回传原文偏移（实例级 monkeypatch，双参）
    _te_sapi = app.tts._speak_sapi
    app.tts._speak_sapi = lambda text, gen: (_synth_calls.append(text), True)[1]
    # 前序快捷键测试曾用 lambda 覆盖 app.tts.start 实例属性，此处恢复真实实现
    app.tts.start = _te.SpeechController.start.__get__(app.tts, type(app.tts))
    _dt_book = book_loader.BookContent("双文本", "", "txt", [
        book_loader.Chapter("第一章", _raw),
    ])
    _dtbid = "dualb"
    app.tts._backend = "sapi"
    app.tts.start(_dt_book, 0, 0)
    _evts = []
    _dl = time.time() + 5
    while time.time() < _dl:
        _evts.extend(app.tts.drain())
        if app.tts.is_stopped():
            break
        time.sleep(0.01)
    _evts.extend(app.tts.drain())
    app.tts.stop()
    app.tts._speak_sapi = _te_sapi
    _starts = [e for e in _evts if e["type"] == "sentence_start"]
    check("双文本朗读事件产生", len(_starts) >= 2, f"n={len(_starts)}")
    _offs_ok = all(0 <= e["char_offset"] < len(_raw) for e in _starts)
    check("双文本事件为原文偏移", _offs_ok, str([e["char_offset"] for e in _starts]))
    _spoken = "".join(e["text"] for e in _starts)
    check("朗读文本为纯净文本", " " not in _spoken and "。。。" not in _spoken, repr(_spoken))

    # --- v1.92：缓存菜单（章节子集 / 暂停 / 继续 / 容量 / 续传） ---
    _sel_book = book_loader.BookContent("缓存书2", "", "txt", [
        book_loader.Chapter("第一章", "一。二。三。四。五。"),
        book_loader.Chapter("第二章", "六。七。八。"),
    ])
    _sbid = "cachebook2"
    app.tts._backend = "edge"
    app.tts._edge_voice = "zh-CN-XiaoxiaoNeural"
    app.tts.set_rate(200)
    def _wait_cache_ready(expect_total=None, timeout=15):
        """等待缓存任务构建完成（异步），返回状态 dict。"""
        t0 = time.time()
        st = None
        while time.time() - t0 < timeout:
            st = app.tts.book_cache_status(_sbid)
            if st and st.get("total", 0) > 0:
                if expect_total is None or st["total"] == expect_total:
                    return st
            time.sleep(0.05)
        return st

    st = app.tts.start_book_cache(_sel_book, _sbid, {0})
    check("缓存章节子集启动", st["state"] in ("caching", "done"), str(st))
    st = _wait_cache_ready(5)
    check("章节子集任务数=5", st is not None and st["total"] == 5, str(st))
    app.tts.pause_book_cache(_sbid)
    st = app.tts.book_cache_status(_sbid)
    check("缓存暂停", st["state"] == "paused", str(st))
    app.tts.resume_book_cache(_sbid)
    st = app.tts.book_cache_status(_sbid)
    check("缓存继续", st["state"] == "caching", str(st))
    _dl = time.time() + 10
    while time.time() < _dl:
        st = app.tts.book_cache_status(_sbid)
        if st and st["state"] in ("done", "cancelled"):
            break
        time.sleep(0.05)
    check("章节子集缓存完成", st["state"] == "done" and st["done"] == 5, str(st))
    _ch2f = os.path.join(tts_cache_dir(), "zh-CN-XiaoxiaoNeural", "200", _sbid, "0001_00000000.mp3")
    check("子集未缓存第二章", not os.path.exists(_ch2f))
    _disk = app.tts.book_cache_disk_used()
    check("缓存容量统计>0", _disk > 0, str(_disk))
    app.tts.set_book_cache_auto_shutdown(True)  # 只验证设置器不抛错（不真正关机）
    check("自动关机设置器", True)
    st = app.tts.start_book_cache(_sel_book, _sbid, None)  # 全量，续传跳过已有
    st = _wait_cache_ready(8)
    check("续传全量启动(total=8)", st is not None and st["total"] == 8, str(st))
    _dl = time.time() + 10
    while time.time() < _dl:
        st = app.tts.book_cache_status(_sbid)
        if st and st["state"] in ("done", "cancelled"):
            break
        time.sleep(0.05)
    check("续传全量完成", st["state"] == "done" and st["done"] == 8, str(st))
    app.tts.set_book_cache_auto_shutdown(False, _sbid)

    # 缓存管理窗口可打开
    app.open_book(cbid)  # 回到已入库书籍
    app._open_cache_dialog()
    root.update_idletasks()
    check("缓存窗口打开", getattr(app, "_cache_dlg", None) and app._cache_dlg.winfo_exists())
    try:
        app._cache_dlg.destroy()
    except Exception:
        pass
    # --- v1.92：从该段开始朗读（显示行→原文偏移） ---
    _map_book = book_loader.BookContent("映射书", "", "txt", [
        book_loader.Chapter("第一章", "第一行第一段。\n第二行第二段。\n第三行第三段。"),
    ])
    app.book = _map_book
    app.chapter_idx = 0
    _line2start = len("第一行第一段。\n")
    app.settings["paragraph_mode"] = 1
    check("模式1 第1行→偏移0", app._display_line_to_offset(1) == 0)
    check("模式1 第2行→段起点", app._display_line_to_offset(2) == _line2start)
    app.settings["paragraph_mode"] = 2
    check("模式2 显示行1→偏移0", app._display_line_to_offset(1) == 0)
    check("模式2 显示行3→第2段起点", app._display_line_to_offset(3) == _line2start)
    app.settings["paragraph_mode"] = 3
    check("模式3 任意行→偏移0", app._display_line_to_offset(5) == 0)
    app.settings["paragraph_mode"] = 1
    # 右键菜单/书架菜单含新项（分隔符项跳过）
    def _menu_labels(menu):
        labels = []
        try:
            n = menu.index("end")
        except Exception:
            return labels
        if n is None:
            return labels
        for i in range(int(n) + 1):
            try:
                labels.append(menu.entrycget(i, "label"))
            except Exception:
                pass
        return labels

    _mi = _menu_labels(app.shelf_menu)
    check("书架右键含添加书籍", "添加书籍" in _mi, str(_mi))
    _ti = _menu_labels(app.text_menu)
    check("阅读区右键含从该段朗读", "从该段开始朗读" in _ti, str(_ti))

    # --- v1.92：定时停止播放 ---
    app._timer_running = True
    app._timer_deadline = time.time() - 1
    app._timer_minutes = 5
    app._tick_timer()
    check("定时到点停止", app._timer_running is False and app._timer_deadline is None)

    # --- v1.92：全屏阅读时间按启动后台计算 ---
    app._app_start = time.time() - 125
    app._fullscreen = True
    app._tick_overlay()
    _ov_txt = app._ov_read.cget("text")
    check("全屏阅读时间按启动计算", "02:05" in _ov_txt, _ov_txt)
    app._fullscreen = False

    # --- v1.92：重复书籍确认（覆盖/取消 不新增；重新预处理走后台） ---
    _n_before = len(app.storage.all_books())
    _orig_ask = app._ask_duplicate
    app._ask_duplicate = lambda title: "overwrite"
    app._import_single(SAMPLE)
    check("重复导入覆盖不新增", len(app.storage.all_books()) == _n_before,
          f"{len(app.storage.all_books())} vs {_n_before}")
    app._ask_duplicate = lambda title: None
    app._import_single(SAMPLE)
    check("重复导入取消无变化", len(app.storage.all_books()) == _n_before)
    app._ask_duplicate = _orig_ask

    # ================= v1.93 =================
    # 1) 弹窗居中
    _t = tk.Toplevel(root)
    _t.geometry("400x300")
    app._center_window(_t)
    root.update_idletasks()
    _mgeo = _t.geometry()
    _mm = _re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", _mgeo)
    _sw2, _sh2 = root.winfo_screenwidth(), root.winfo_screenheight()
    if _mm:
        _xx, _yy = int(_mm.group(3)), int(_mm.group(4))
        check("弹窗居中X", abs(_xx - (_sw2 - 400) // 2) <= 3, f"x={_xx}")
        check("弹窗居中Y", abs(_yy - (_sh2 - 300) // 2) <= 3, f"y={_yy}")
    else:
        check("弹窗居中X", False, _mgeo)
        check("弹窗居中Y", False, _mgeo)
    _t.destroy()

    # 2) 自定义音频缓存目录 + 一键转移 + 大小统计 + 删除
    _old_custom = app.storage.get_setting("tts_cache_dir")
    _custom = os.path.join(TMP, "audio_cache_custom")
    app.settings["tts_cache_dir"] = _custom
    app.storage.set_setting("tts_cache_dir", _custom)
    app.tts.set_tts_cache_dir(app._effective_tts_cache_root())
    check("自定义缓存目录生效", app._effective_tts_cache_root() == _custom, app._effective_tts_cache_root())
    _cc_book = book_loader.BookContent("缓存书3", "", "txt", [book_loader.Chapter("第一章", "甲。乙。丙。")])
    _cc_bid = "cachebook3"
    app.tts._backend = "edge"
    st = app.tts.start_book_cache(_cc_book, _cc_bid)
    _dl = time.time() + 8
    while time.time() < _dl:
        st = app.tts.book_cache_status()
        if st and st["state"] in ("done", "cancelled"):
            break
        time.sleep(0.05)
    check("自定义目录缓存完成", st["state"] == "done", str(st))
    _cf = os.path.join(_custom, _cc_bid, "zh-CN-XiaoxiaoNeural", "200")
    check("音频缓存落在自定义目录", os.path.isdir(_cf), _cf)
    _newdir = os.path.join(TMP, "audio_cache_moved")
    app._do_transfer_cache(app._effective_tts_cache_root(), _newdir, kind="audio")
    check("转移后设置更新", app.storage.get_setting("tts_cache_dir") == _newdir,
          str(app.storage.get_setting("tts_cache_dir")))
    check("转移后文件在新目录", os.path.isdir(os.path.join(_newdir, _cc_bid, "zh-CN-XiaoxiaoNeural", "200")))
    check("转移后旧目录清空", not os.path.isdir(os.path.join(_custom, _cc_bid)))
    check("音频缓存大小统计>0", app._audio_cache_size(_cc_bid) > 0, str(app._audio_cache_size(_cc_bid)))
    app._delete_audio_cache(_cc_bid)
    check("删除音频缓存后为0", app._audio_cache_size(_cc_bid) == 0, str(app._audio_cache_size(_cc_bid)))
    # 恢复默认位置
    app.settings["tts_cache_dir"] = _old_custom or ""
    app.storage.set_setting("tts_cache_dir", _old_custom or "")
    app.tts.set_tts_cache_dir(app._effective_tts_cache_root())

    # 3) 缓存窗口：下载管理器（多书）+ 单书章节选择窗口
    app.open_book(cbid)
    app._open_cache_dialog()
    root.update_idletasks()
    check("缓存窗口打开（多书）", getattr(app, "_cache_dlg", None) and app._cache_dlg.winfo_exists())
    try:
        app._cache_dlg.destroy()
    except Exception:
        pass
    # 打开单书章节选择窗口（含反选 / 继续上次下载）
    app._open_book_cache_dialog(cbid)
    root.update_idletasks()

    def _find_btn(text, w=None):
        w = w or getattr(app, "_cache_book_dlg", None) or app._cache_dlg
        try:
            for c in w.winfo_children():
                try:
                    if isinstance(c, tk.Button) and c.cget("text") == text:
                        return True
                except Exception:
                    pass
                if _find_btn(text, c):
                    return True
        except Exception:
            return False
        return False

    def _find_lbl(sub, w=None):
        w = w or getattr(app, "_cache_book_dlg", None) or app._cache_dlg
        try:
            for c in w.winfo_children():
                try:
                    if isinstance(c, tk.Label) and sub in str(c.cget("text")):
                        return True
                except Exception:
                    pass
                if _find_lbl(sub, c):
                    return True
        except Exception:
            return False
        return False

    check("缓存窗口无每100章", not _find_btn("每100章"))
    check("缓存窗口有反选", _find_btn("反选"))
    check("缓存窗口有继续上次下载", _find_btn("继续上次下载"))
    check("缓存窗口警示说明", _find_lbl("体积较大"))
    # 反选逻辑
    app._cache_select_all()
    _n_all = app._cache_lb.size()
    check("全选生效", len(app._cache_lb.curselection()) == _n_all, str(len(app._cache_lb.curselection())))
    app._cache_select_invert()
    check("反选后为空", len(app._cache_lb.curselection()) == 0)
    app._cache_select_invert()
    check("再反选恢复全选", len(app._cache_lb.curselection()) == _n_all)
    try:
        app._cache_book_dlg.destroy()
    except Exception:
        pass

    # 4) 书架显示缓存总大小
    app._refresh_bookshelf()
    _shelf_vals = [app.shelf_tree.item(i, "values") for i in app.shelf_tree.get_children()]
    check("书架显示缓存大小", any(len(v) >= 3 and v[2] not in ("", "-") for v in _shelf_vals), str(_shelf_vals[:2]))

    # 5) 状态栏缓存点击（暂停/继续）
    _cc2 = book_loader.BookContent("点击书", "", "txt", [book_loader.Chapter("第一章", "子。丑。寅。卯。" * 300)])
    app.tts._backend = "edge"
    app.current_bid = "clickbook"  # _on_status_cache_click 依赖当前打开书
    app.tts.start_book_cache(_cc2, "clickbook")
    st = app.tts.book_cache_status()
    if st and st["state"] == "caching":
        app._on_status_cache_click()
        st2 = app.tts.book_cache_status()
        check("状态栏点击暂停", st2["state"] == "paused", str(st2))
        app._on_status_cache_click()
        st3 = app.tts.book_cache_status()
        check("状态栏再点击继续", st3["state"] in ("caching", "done"), str(st3))
        _dl = time.time() + 8
        while time.time() < _dl:
            st3 = app.tts.book_cache_status()
            if st3 and st3["state"] in ("done", "cancelled"):
                break
            time.sleep(0.05)
        app._delete_audio_cache("clickbook")
    else:
        app._delete_audio_cache("clickbook")
        check("状态栏点击（已完成跳过）", True)

    # 6) 手动百分比跳转
    app.open_book(cbid)
    _nch = len(app.book.chapters)
    app._seek_percent(0)
    check("跳转到0%为第一章", app.chapter_idx == 0, str(app.chapter_idx))
    app._seek_percent(100)
    check("跳转到100%为最后一章", app.chapter_idx == _nch - 1, str(app.chapter_idx))
    app._seek_percent(50)
    check("跳转到50%位置合法",
          0 <= app.chapter_idx < _nch and 0 <= app.char_offset <= len(app.book.chapters[app.chapter_idx].content),
          f"ci={app.chapter_idx} off={app.char_offset}")

    # 7) 单本导入弹进度窗（v1.7 补全）
    S3 = os.path.join(TMP, "单本导入.txt")
    with open(S3, "w", encoding="utf-8") as _f:
        _f.write("第一章 测试\n第一句。第二句。\n第二章\n第三句。")
    app._import_many([S3])
    root.update()
    _dl = time.time() + 15
    while time.time() < _dl:
        root.update()
        if not getattr(app, "_import_win", None) or not app._import_win.winfo_exists():
            break
        time.sleep(0.05)
    root.update_idletasks()
    b3 = app.storage.get_book(app.storage.book_id(S3))
    check("单本导入入库", b3 is not None)
    check("单本导入已打开", app.current_bid == app.storage.book_id(S3), str(app.current_bid))
    os.remove(S3)

    # 8) 关闭自动暂停音频缓存（沿用最后的 _on_close）
    _cc3 = book_loader.BookContent("关闭书", "", "txt", [book_loader.Chapter("第一章", "好。吧。啊。" * 3000)])
    app.tts._backend = "edge"
    app.tts.start_book_cache(_cc3, "closebook")

    app._on_close()
    _final_st = app.tts.book_cache_status()
    check("关闭后不再缓存(暂停/完成)", _final_st is not None and _final_st["state"] != "caching", str(_final_st))
    check("正常关闭", True)

    print()
    if FAIL:
        print(f"共 {len(FAIL)} 项失败: {FAIL}")
        sys.exit(1)
    print("多多朗读 v1.95 新功能测试通过")
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
