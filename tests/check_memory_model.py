# -*- coding: utf-8 -*-
"""内存模型自检：打印各规模的预计峰值内存，以及本机可算上限。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

avail = pb.available_memory_bytes()
print(f"可用内存: {avail / 2 ** 20:.0f} MB" if avail else "可用内存: 未知")
print()
print(f"{'位数':<14}{'预计峰值MB':<12}说明")
print("-" * 62)
for n in (10_000_000, 20_000_000, 40_000_000, 80_000_000,
          100_000_000, 200_000_000, 500_000_000, 1_000_000_000):
    print(f"{n:>12,}  {pb.estimate_peak_memory(n, 1) / 2 ** 20:>9.0f}   {pb.memory_report(n, 1)}")

print()
cap = pb.max_digits_for_memory(avail, 0.55, 1)
print(f"本机可用内存下，单进程最多可算: {cap:,} 位")

print()
print("多进程对照（省内存策略下不用，仅作对比）:")
for w in (1, 2, 4):
    need = pb.estimate_peak_memory(100_000_000, w)
    print(f"  1 亿位 × {w} 进程 → 预计峰值 {need / 2 ** 20:.0f} MB")
