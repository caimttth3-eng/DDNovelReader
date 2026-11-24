# -*- coding: utf-8 -*-
"""视口分页（不重排正文）。

思路：正文与行映射完全不动，只移动视口。页边界由「显示行」决定：
从当前页首行往下走，第一行放不进视口（底部会切掉）就是下一页的首行。
这样每页顶部永远是完整显示行，页内不会出现上半行被切掉的情况。

所有测量都用 Tk 自己的 display linestart / dlineinfo / yview_scroll("pixels")，
不依赖字体度量估算，换字号/行距/窗口宽度后重算即可。

性能注意：`N displaylines` 偏移每次都要从段落开头重新排版，遇到整章只有
一个换行（几万字一大段）的 txt 会卡到秒级；因此页边界一律用
`@0,y display linestart` 按像素取样，只走可见的那几行。
"""


def _pady(text):
    try:
        return int(float(text.cget("pady")))
    except Exception:
        return 0


def viewport_height(text):
    return max(1, int(text.winfo_height()))


def _line_at_pixel(text, y):
    """视口内 y 像素处所在显示行的行首索引（y 越界时 Tk 会给最接近的字符）。"""
    try:
        return text.index("@0,%d display linestart" % max(0, int(y)))
    except Exception:
        return text.index("@0,0")


def _next_display_line(text, idx):
    try:
        nxt = text.index("%s + 1 displaylines" % idx)
    except Exception:
        return None
    return None if nxt == idx else nxt


def page_top_index(text):
    """当前页首：视口顶部内边距处那一行的行首（如该行上半截被裁则取下一行）。"""
    idx = _line_at_pixel(text, _pady(text))
    try:
        d = text.dlineinfo(idx)
    except Exception:
        d = None
    if d and int(d[1]) < 0:
        try:
            idx = text.index("%s + 1 displaylines" % idx)
        except Exception:
            pass
    return idx


def page_bounds(text, top=None):
    """返回 (peek_index, next_start)。

    peek_index：当前页底部被切掉的那一行（页内可见但其上半截属于下一行时；
                没有则 None）。
    next_start：下一页首行索引；已到文末则 None。
    """
    pady = _pady(text)
    bottom = viewport_height(text) - pady
    idx = text.index(top) if top else page_top_index(text)
    try:
        if float(text.yview()[1]) >= 0.999:
            return None, None  # 已滚到文末：没有下一页
    except Exception:
        pass
    cut = _line_at_pixel(text, bottom)
    if not text.compare(cut, ">", idx):
        return None, None  # 整页连一行都放不满（超高插图等）
    try:
        d = text.dlineinfo(cut)
    except Exception:
        d = None
    if d is None:
        # 底部取样到的行其实还在视口外：下一页从它开始
        return None, cut
    if int(d[1]) + int(d[3]) > bottom + 1:
        return cut, cut  # 这一行会被页底裁掉：下一页从它开始
    nxt = _next_display_line(text, cut)
    if not nxt:
        return None, None
    return None, nxt


def first_cut_line(text, top=None):
    """当前页底部被裁掉的显示行索引（用于画遮罩把半行藏起来），没有则 None。"""
    peek, _nxt = page_bounds(text, top)
    return peek


def snap_index_to_top(text, idx, max_tries=4):
    """把 idx 所在显示行精确对齐到视口顶部（含顶部内边距）。返回实际页首索引。

    用 yview(idx) 先把该行顶到视口上沿（Tk 内部按行吸附），再用像素滚动补齐
    pady 的差额。每次测量前必须让 Tk 完成重绘，否则 dlineinfo 拿到的是滚动前的
    旧坐标，连续两次就会越滚越远。
    """
    idx = text.index(idx)
    pady = _pady(text)
    text.see(idx)
    text.update_idletasks()
    limit = max(1, viewport_height(text) - 2 * pady)
    for _ in range(max_tries):
        d = text.dlineinfo(idx)
        if not d:
            text.see(idx)
            text.update_idletasks()
            d = text.dlineinfo(idx)
            if not d:
                break
        dy = int(d[1]) - pady
        if abs(dy) <= 1:
            break
        # 单次别滚过一整页：过冲会把页首推到上一行，来回震荡
        dy = max(-limit, min(limit, dy))
        text.yview_scroll(dy, "pixels")
        text.update_idletasks()
    return page_top_index(text)


def yview_fraction(text):
    try:
        return float(text.yview()[0])
    except Exception:
        return 0.0


def animate_fraction(root, text, from_f, to_f, frames=6, interval=16, done=None):
    """按 yview 分数平滑滚动（分页滑动动画）。"""
    state = {"i": 0}

    def step():
        state["i"] += 1
        k = state["i"] / float(frames)
        f = from_f + (to_f - from_f) * k
        try:
            text.yview_moveto(max(0.0, min(1.0, f)))
        except Exception:
            pass
        if state["i"] < frames:
            try:
                root.after(interval, step)
            except Exception:
                pass
        elif done is not None:
            try:
                done()
            except Exception:
                pass

    step()
