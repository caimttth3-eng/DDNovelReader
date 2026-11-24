# -*- coding: utf-8 -*-
"""生成 novelreader/textconv_data.py（正文简繁 1:1 转换表）。

数据源：opencc 字典目录（默认取 .venv 内的 opencc-python-reimplemented）。
只保留「单个字 → 单个字」与「等长词组 → 等长词组」的映射：
转换前后字符数完全不变，才能保证阅读进度、朗读高亮、书签、插图锚点不漂移。

用法（项目根）：
    .venv\\Scripts\\python.exe tools\\gen_textconv_data.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "novelreader", "textconv_data.py")


def _dict_dir():
    cand = os.path.join(ROOT, ".venv", "Lib", "site-packages", "opencc", "dictionary")
    if os.path.isdir(cand):
        return cand
    try:
        import opencc
        d = os.path.join(os.path.dirname(os.path.abspath(opencc.__file__)), "dictionary")
        if os.path.isdir(d):
            return d
    except Exception:
        pass
    raise SystemExit("找不到 opencc 字典目录")


def load(name, ddir):
    out = {}
    path = os.path.join(ddir, name)
    for line in io.open(path, "r", encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        k, v = parts[0], parts[1]
        out[k] = v.split(" ")[0]  # 多候选取第一个（词组表负责歧义消解）
    return out


def build(chars, phrases):
    """丢掉「按字表逐字替换已能得到同样结果」的词组，只保留真正有歧义的。"""
    keep = {}
    for k, v in phrases.items():
        if len(k) != len(v) or len(k) < 2:
            continue
        if "".join(chars.get(c, c) for c in k) == v:
            continue
        keep[k] = v
    return keep


def dump(name, mapping):
    lines = [name + " = {"]
    for k in sorted(mapping):
        lines.append("    %r: %r," % (k, mapping[k]))
    lines.append("}")
    return "\n".join(lines)


def main():
    ddir = _dict_dir()
    sc = load("STCharacters.txt", ddir)
    tc = load("TSCharacters.txt", ddir)
    sp = load("STPhrases.txt", ddir)
    tp = load("TSPhrases.txt", ddir)
    # 字表只保留 1:1
    sc = {k: v for k, v in sc.items() if len(k) == 1 and len(v) == 1}
    tc = {k: v for k, v in tc.items() if len(k) == 1 and len(v) == 1}
    sp = build(sc, sp)
    tp = build(tc, tp)

    body = [
        "# -*- coding: utf-8 -*-",
        '"""正文简繁 1:1 转换表（自动生成，勿手改）。',
        "",
        "生成脚本：tools/gen_textconv_data.py（数据源 = opencc 字典 ST/TSCharacters、ST/TSPhrases）。",
        "只收录等长映射：转换前后字符数不变，阅读进度/朗读高亮/书签/插图锚点才不会错位。",
        '"""',
        "",
        "# 单字表：%d 条" % len(sc),
        dump("S2T_CHARS", sc),
        "",
        "# 词组表（仅保留按字表替换得不到的）：%d 条" % len(sp),
        dump("S2T_PHRASES", sp),
        "",
        "# 单字表：%d 条" % len(tc),
        dump("T2S_CHARS", tc),
        "",
        "# 词组表（仅保留按字表替换得不到的）：%d 条" % len(tp),
        dump("T2S_PHRASES", tp),
        "",
    ]
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(body))
    print("written %s: chars %d/%d phrases %d/%d" % (OUT, len(sc), len(tc), len(sp), len(tp)))


if __name__ == "__main__":
    sys.exit(main())
