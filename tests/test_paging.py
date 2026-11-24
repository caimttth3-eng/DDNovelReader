# -*- coding: utf-8 -*-
"""视口分页算法测试：页首对齐、页边界完整、字号/窗口变化重算、上下翻页。

要点：每页顶部必须是完整显示行（dlineinfo 的 y 等于 pady），
页与页首尾相接不重叠不跳行；换字号/换窗口高度后重算仍然对齐。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
VENV = os.path.join(BASE, ".venv")
if os.name == "nt" and not os.environ.get("TCL_LIBRARY"):
    os.environ["TCL_LIBRARY"] = os.path.join(VENV, "Lib", "tcl8.6")
    os.environ["TK_LIBRARY"] = os.path.join(VENV, "Lib", "tk8.6")

import tkinter as tk

from novelreader import paging

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


BODY = (
    "第一章 测试章节\n\n"
    + ("夜色沉下来的时候，村口那盏旧灯还亮着，风从稻田上吹过来，带着一点潮湿的味道。\n\n" * 400)
)


def make_text(root, size=13):
    t = tk.Text(root, width=20, height=10, wrap="word", pady=10, padx=12,
                relief="flat", borderwidth=0, highlightthickness=0)
    t.configure(font=("Microsoft YaHei", size))
    t.pack(fill="both", expand=True)
    t.insert("1.0", BODY)
    root.update()
    return t


def top_ok(text, idx, pady):
    d = text.dlineinfo(idx)
    if not d:
        return False, "dlineinfo=None"
    return abs(int(d[1]) - pady) <= 1, f"y={int(d[1])} pady={pady}"


def walk_pages(text, count=20):
    """从文首连续翻 count 页，返回每页 (start, peek, next) 与对齐结果。"""
    pady = paging._pady(text)
    text.see("1.0")
    text.yview_moveto(0.0)
    text.update()
    logs = []
    for _ in range(count):
        start = paging.page_top_index(text)
        ok, detail = top_ok(text, start, pady)
        peek, nxt = paging.page_bounds(text, start)
        logs.append((start, peek, nxt, ok, detail))
        if not nxt:
            break
        paging.snap_index_to_top(text, nxt)
    return logs


def main():
    root = tk.Tk()
    root.title("paging-test")
    root.geometry("420x520+2500+100")
    try:
        root.attributes("-alpha", 0.02)
    except Exception:
        pass
    text = make_text(root)
    root.update()

    print("[paging] 首屏对齐")
    text.yview_moveto(0.0)
    text.update()
    paging.snap_index_to_top(text, "1.0")
    start = paging.page_top_index(text)
    ok, detail = top_ok(text, start, paging._pady(text))
    check("文首页首对齐", start == "1.0" and ok, f"{start} {detail}")

    print("[paging] 连续翻 20 页")
    logs = walk_pages(text, 20)
    check("能连续翻满 20 页", len(logs) >= 20, str(len(logs)))
    mid = [l for l in logs if l[2] is not None]
    check("中间页页首都是完整显示行", all(l[3] for l in mid),
          str([l[4] for l in mid if not l[3]])[:200])
    check("页首严格递增（无重叠）",
          all(text.compare(logs[i + 1][0], ">", logs[i][0]) for i in range(len(logs) - 1)))
    check("下一页页首 == 上一页算出的 next",
          all(logs[i + 1][0] == logs[i][2] for i in range(len(logs) - 1) if logs[i][2]))
    # 上一页末行底边 <= 下一页页首顶边：靠 dlineinfo 检查页首前一显示行的位置
    gap_ok = True
    for i in range(len(logs) - 1):
        prev_last = text.index("%s -1 displaylines" % logs[i + 1][0])
        if text.compare(prev_last, "<", logs[i][0]):
            gap_ok = False
    check("页边界连续（页首前一显示行属于上一页）", gap_ok)

    print("[paging] 翻到文末")
    pages = len(logs)
    while True:
        peek, nxt = paging.page_bounds(text, paging.page_top_index(text))
        if not nxt:
            break
        paging.snap_index_to_top(text, nxt)
        pages += 1
        if pages > 200:
            break
    check("能翻到文末（next=None）", pages <= 200, str(pages))
    d = text.dlineinfo(text.index("end-1c"))
    check("文末页末行可见", d is not None, str(d))

    print("[paging] 换字号重算")
    text.yview_moveto(0.0)
    text.update()
    paging.snap_index_to_top(text, "1.0")
    old_first = paging.page_bounds(text, "1.0")
    text.configure(font=("Microsoft YaHei", 19))
    root.update()
    paging.snap_index_to_top(text, paging.page_top_index(text))
    pady = paging._pady(text)
    ok, detail = top_ok(text, paging.page_top_index(text), pady)
    check("字号变大后页首仍对齐", ok, detail)
    new_first = paging.page_bounds(text, paging.page_top_index(text))
    check("字号变大后单页内容减少",
          new_first[1] is not None and (old_first[1] is None or text.compare(new_first[1], "<", old_first[1])),
          f"{new_first[1]} vs {old_first[1]}")

    print("[paging] 窗口变矮重算")
    root.geometry("420x300+2500+100")
    root.update()
    paging.snap_index_to_top(text, paging.page_top_index(text))
    ok, detail = top_ok(text, paging.page_top_index(text), paging._pady(text))
    check("窗口变矮后页首仍对齐", ok, detail)
    num = 0
    text.yview_moveto(0.0)
    text.update()
    paging.snap_index_to_top(text, "1.0")
    while num < 100:
        peek, nxt = paging.page_bounds(text, paging.page_top_index(text))
        if not nxt:
            break
        paging.snap_index_to_top(text, nxt)
        num += 1
    check("变矮后页数更多", num > pages, f"{num} vs {pages}")

    print("[paging] 回翻（上一页页首）")
    starts = []
    text.yview_moveto(0.0)
    root.update()
    paging.snap_index_to_top(text, "1.0")
    for _ in range(4):
        starts.append(paging.page_top_index(text))
        peek, nxt = paging.page_bounds(text)
        if not nxt:
            break
        paging.snap_index_to_top(text, nxt)
    back = paging.snap_index_to_top(text, starts[-2])
    ok, detail = top_ok(text, back, paging._pady(text))
    check("回翻到上一页页首对齐", ok and text.compare(back, "==", starts[-2]), f"{back} {detail}")

    print("[paging] 遮罩行判定")
    text.yview_moveto(0.0)
    root.update()
    paging.snap_index_to_top(text, "1.0")
    peek, nxt = paging.page_bounds(text)
    if peek is not None:
        d = text.dlineinfo(peek)
        cut_ok = d is not None and int(d[1]) + int(d[3]) > text.winfo_height() - paging._pady(text) + 1
        check("遮罩行确实超出页底", cut_ok, str(d))
    else:
        check("遮罩行为 None（该页恰好整齐）", True)

    root.destroy()
    print("\n%s（失败 %d 项）" % ("ALL PASS" if not FAIL else "FAILED", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
