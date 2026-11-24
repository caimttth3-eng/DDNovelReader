# -*- coding: utf-8 -*-
"""正文简繁转换测试：严格 1:1 保长、词表覆盖、往返、性能。

长度不变是硬约束——阅读进度/朗读高亮/书签/插图锚点全按原文字符偏移算。
"""
import glob
import os
import random
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from novelreader import textconv
from novelreader import textconv_data

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def test_tables():
    for table in (textconv_data.S2T_CHARS, textconv_data.T2S_CHARS,
                  textconv_data.S2T_PHRASES, textconv_data.T2S_PHRASES):
        bad = [(k, v) for k, v in table.items() if len(k) != len(v)]
        check("表内全部等长映射（%d 条）" % len(table), not bad, str(bad[:3]))
    check("字表非空", len(textconv_data.S2T_CHARS) > 3000 and len(textconv_data.T2S_CHARS) > 3000)
    check("词组表非空", len(textconv_data.S2T_PHRASES) > 5000)


def test_cases():
    s = "他发现头发很乱，互联网软件鼠标。"
    a = textconv.convert(s, "s2t")
    check("简→繁 词级消歧（头发→頭髮/发现→發現）", "頭髮" in a and "發現" in a, a)
    check("简→繁 长度不变", len(a) == len(s))
    b = textconv.convert(a, "t2s")
    check("繁→简 长度不变", len(b) == len(a))
    check("繁→简 回到简体", b == s, b)
    # 一字多义：乾净 → 乾淨 → 干净
    c = textconv.convert("乾净", "t2s")
    check("繁→简 异体字（乾净→干净）", c == "干净", c)
    check("none 模式原样返回", textconv.convert(s, "none") == s)
    check("空串安全", textconv.convert("", "s2t") == "")
    check("未知模式按 none 处理", textconv.convert(s, "xx") == s)


def _load_samples():
    out = []
    for pat in ("sample/*.txt", "sample/*.md"):
        for p in glob.glob(os.path.join(BASE, pat)):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    out.append((p, f.read()))
            except Exception:
                pass
    # 兜底：仓库里任何一大段中文
    if not out:
        out.append(("<内置>", ("这是一段用于测试的中文文本。" * 200)))
    return out


def test_samples():
    for path, text in _load_samples():
        a = textconv.convert(text, "s2t")
        b = textconv.convert(a, "t2s")
        check("样本长度不变 %s（%d 字）" % (os.path.basename(path), len(text)),
              len(a) == len(text) and len(b) == len(a))
        # s2t 幂等：繁体再转一次不应再变
        check("s2t 幂等 %s" % os.path.basename(path), textconv.convert(a, "s2t") == a)


def test_random():
    pool = "的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美总从无情己面最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间斯知世什两次使身者被高已亲其进此话常与活正感头发乾净互联网软件鼠标"
    rnd = random.Random(20261124)
    for _ in range(40):
        s = "".join(rnd.choice(pool) for _ in range(rnd.randint(0, 300)))
        a = textconv.convert(s, "s2t")
        b = textconv.convert(a, "t2s")
        if not (len(a) == len(s) and len(b) == len(a)):
            check("随机串长度不变", False, s[:40])
            return
    check("随机串长度不变（40 组）", True)


def test_speed():
    text = "这是一段用于测速的中文正文，包含标点、数字 123 与英文 ABC。" * 400  # ~1.2 万字
    t0 = time.perf_counter()
    a = textconv.convert(text, "s2t")
    dt = (time.perf_counter() - t0) * 1000
    check("1.2 万字转换 < 120ms（实测 %.1fms）" % dt, dt < 120)
    check("测速样本长度不变", len(a) == len(text))


def main():
    print("[textconv] 表结构")
    test_tables()
    print("[textconv] 典型用例")
    test_cases()
    print("[textconv] 样本")
    test_samples()
    print("[textconv] 随机")
    test_random()
    print("[textconv] 性能")
    test_speed()
    print("\n%s（失败 %d 项）" % ("ALL PASS" if not FAIL else "FAILED", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
