# -*- coding: utf-8 -*-
"""算到 1200 万位，找一个「8 位完整生日位于 1000 万位之后」的样例，
用于测试「继续往后算」这条路径。"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

TARGET = 12_000_000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deep_sample.txt")


def main():
    pb.setup_console_utf8()
    print(f"计算到 {TARGET:,} 位…")
    d = pb.acquire_digits(TARGET, method="compute", log=lambda m: print("   " + m))
    print(f"实际取得 {len(d):,} 位")

    txt = d.decode()
    base = 10_000_000                     # 内置数据的边界
    win = txt[base:base + 500_000]        # 只在紧邻的一段里找，保证测试够快
    pat = re.compile(rb"(19[5-9]\d|20[0-2]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
    hits = []
    for m in pat.finditer(win.encode()):
        pos = base + m.start()
        s = m.group().decode()
        hits.append((pos, s, f"{s[:4]}-{s[4:6]}-{s[6:]}"))
        if len(hits) >= 5:
            break

    print(f"\n1000 万位之后的第一个 8 位生日：")
    for pos, s, pretty in hits:
        print(f"  {s}  ({pretty})  第 {pos + 1:,} 位   （相对边界 +{pos - base:,}）")
    if len(hits) < 2:
        print("⚠️ 样本不足，请扩大窗口")
        return 1

    with open(OUT, "w", encoding="utf-8") as f:
        for pos, s, pretty in hits:
            f.write(f"{pretty}\t{s}\t{pos}\n")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
