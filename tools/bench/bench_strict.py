# -*- coding: utf-8 -*-
"""单进程交错的严格 A/B（唯一可比的口径）。

为什么要这样测：
  · process_time() 只统计当前进程 —— 多进程时子进程不计入，所以只能测单进程
  · 墙钟时间噪声极大（同一份代码能差 2 倍）
  · GMP 是内存带宽密集型，后台负载会污染 CPU 时间
所以：同一进程内交替跑 A/B，多轮取最小值。
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

import gmpy2
from gmpy2 import mpz

C3 = 640320 ** 3 // 24


# ---------------- 优化前的完整单进程计算 ----------------
def old_bs(terms):
    def rec(lo, hi):
        if hi - lo == 1:
            if lo == 0:
                P = Q = mpz(1)
            else:
                P = mpz((6 * lo - 5) * (2 * lo - 1) * (6 * lo - 1))
                Q = mpz(lo * lo * lo * C3)
            T = P * (13591409 + 545140134 * lo)
            if lo & 1:
                T = -T
        else:
            m = (lo + hi) // 2
            P1, Q1, T1 = rec(lo, m)
            P2, Q2, T2 = rec(m, hi)
            P = P1 * P2
            Q = Q1 * Q2
            T = Q2 * T1 + P1 * T2
        return P, Q, T
    return rec(0, terms)


def old_full(n):
    total = n + pb.GUARD_DIGITS
    terms = int(total / pb.DIGITS_PER_TERM) + 2
    P, Q, T = old_bs(terms)
    one = mpz(10) ** total
    sqrt_c = gmpy2.isqrt(10005 * one * one)
    value = (Q * 426880 * sqrt_c) // T
    s = str(value)
    return s[1:1 + n].encode("ascii")


# ---------------- 优化后（当前 pi_birthday 实现） ----------------
def new_full(n):
    return pb.compute_pi_gmpy2(n, workers=1)


def main():
    n = int(os.environ.get("PI_N", "5000000"))
    reps = int(os.environ.get("PI_REPS", "4"))
    pb.setup_console_utf8()
    seed = pb.seed_digits()[:n]
    print(f"位数 {n:,}   交错 {reps} 轮   单进程   指标 = CPU 时间\n")

    old, new = [], []
    for i in range(reps):
        c0 = time.process_time(); d1 = old_full(n); c1 = time.process_time() - c0
        c0 = time.process_time(); d2 = new_full(n); c2 = time.process_time() - c0
        ok1 = d1 == seed
        ok2 = d2 == seed
        old.append(c1); new.append(c2)
        print(f"  第 {i+1} 轮  旧 {c1:6.2f}s {'✅' if ok1 else '❌'}   "
              f"新 {c2:6.2f}s {'✅' if ok2 else '❌'}   {c1/c2:.2f}x")

    print()
    print(f"  旧：min {min(old):.2f}s  中位 {statistics.median(old):.2f}s")
    print(f"  新：min {min(new):.2f}s  中位 {statistics.median(new):.2f}s")
    print()
    print(f"  按最小值：提速 {min(old)/min(new):.2f}x  "
          f"（省 {min(old)-min(new):.2f}s CPU）")
    print(f"  按中位数：提速 {statistics.median(old)/statistics.median(new):.2f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
