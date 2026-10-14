# -*- coding: utf-8 -*-
"""在不同可用内存下，程序最多能「一直算到」多少位。

用来回答「到底能算多远」这个问题（模型：20MB + 13 字节/位，取可用内存的 70%）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

print(f"{'可用内存':>10}{'可算上限':>12}{'预计峰值':>10}{'8位生日命中率':>16}")
print("-" * 50)
for gb in (0.8, 1, 2, 3, 4, 6, 7):
    avail = int(gb * 2 ** 30)
    cap = pb.max_digits_for_memory(avail, 0.70, 1) or 0
    peak = pb.estimate_peak_memory(cap, 1) / 2 ** 30
    # 命中率 = 1 - e^(-N/1e8)
    import math
    p = (1 - math.exp(-cap / 1e8)) * 100
    print(f"{gb:>8.1f}GB{cap / 1e6:>10.0f}M{peak:>9.1f}GB{p:>15.1f}%")

print()
print("说明：8 位生日的出现位置服从均匀分布，在前 N 位的命中率 = 1 - e^(-N/1e8)。")
print("      要 99.9% 把握需要约 6.9 亿位，本机（总内存 7.6GB）离线算不到。")
