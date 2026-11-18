# -*- coding: utf-8 -*-
"""语速倍数标注自测（v2.3.0）。

标定依据（2026-11-18 实测，Edge zh-CN-XiaoxiaoNeural，同一句话去静音后时长）：
    rate 100(-50%) → 0.50×    rate 200(+0%) → 1.00×    rate 250(+25%) → 1.25×
    rate 300(+50%) → 1.50×    rate 350(+75%) → 1.74×    rate 400(+100%) → 1.99×
结论：真实倍数 ≈ rate / 200；Edge 上限就是 +100%（2.00×），再往上被服务端忽略；
      低于 -50% 也不再更慢（rate 50 与 100 时长相同）。

跑法： .venv\\Scripts\\python.exe tests\\test_rate_multiplier.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from novelreader.content_key import (                                    # noqa: E402
    RATE_MAX, RATE_MIN, RATE_STEP, format_multiplier, multiplier_of_rate,
    rate_dir_key, rate_of_multiplier, rate_pct_of_speech_rate,
)

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("[ OK ] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s %s" % (name, extra))


def main():
    # 1) 倍数显示：与实测一致
    cases = [(100, "0.50×"), (200, "1.00×"), (250, "1.25×"),
             (300, "1.50×"), (310, "1.55×"), (350, "1.75×"), (400, "2.00×")]
    for rate, text in cases:
        check("rate %d 显示 %s" % (rate, text), format_multiplier(rate) == text,
              format_multiplier(rate))

    # 2) 倍数 → rate（步进 0.05）与回环
    check("1.55× → rate 310", rate_of_multiplier(1.55) == 310, rate_of_multiplier(1.55))
    check("1.00× → rate 200", rate_of_multiplier(1.0) == 200)
    check("0.50× → rate 100", rate_of_multiplier(0.5) == 100)
    check("0.75× → rate 150", rate_of_multiplier(0.75) == 150)
    for rate in (100, 110, 200, 280, 310, 400):
        check("rate %d 倍数回环" % rate,
              rate_of_multiplier(multiplier_of_rate(rate)) == rate,
              rate_of_multiplier(multiplier_of_rate(rate)))
    # 旧版本可能存过不在 0.05 档上的值（如 275 = 1.375×）：显示按真实值两位小数，
    # 不四舍五入到档位（避免"显示 1.40× 实际 1.375×"），只有输入/滑块才落档。
    check("非档位值 275 显示真实倍数 1.38×", format_multiplier(275) == "1.38×",
          format_multiplier(275))

    # 3) 边界：超出范围夹到 0.50×~2.00×（3.00× 做不到，被夹到 2.00×）
    check("下限 0.25× 被夹到 0.50×", rate_of_multiplier(0.25) == RATE_MIN, rate_of_multiplier(0.25))
    check("上限 3.00× 被夹到 2.00×", rate_of_multiplier(3.00) == RATE_MAX, rate_of_multiplier(3.00))
    check("rate 50（旧最小值）显示 0.50×（Edge 更慢也无效）", format_multiplier(50) == "0.50×")
    check("rate 600（旧超限）显示 2.00×", format_multiplier(600) == "2.00×")
    check("非法输入回落到 1.00×", format_multiplier(None) == "1.00×", format_multiplier(None))
    check("步进常量 = 0.05×", RATE_STEP == 10, RATE_STEP)

    # 4) 与跨端缓存口径一致：PC rate → pct 必须等于安卓 speechRate → pct
    for rate in (100, 150, 200, 250, 300, 350, 400):
        mult = multiplier_of_rate(rate)
        check("rate %d 的 pct 与安卓 %.2f 一致" % (rate, mult),
              int(rate_dir_key(rate)) == rate_pct_of_speech_rate(mult),
              (rate_dir_key(rate), rate_pct_of_speech_rate(mult)))

    # 5) 语速标签不会再显示 50~400 这种数字
    check("显示串一定带 × 号",
          all(format_multiplier(r).endswith("×") for r in (100, 200, 310, 400)))

    print("\n==== %d FAIL ====" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
