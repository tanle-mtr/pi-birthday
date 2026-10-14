# -*- coding: utf-8 -*-
"""尾部优化的严格交错 A/B：旧写法 vs 新写法（CPU 时间，多轮取最小值）。

被验证的三项改动：
  1. isqrt(10005·10^(2n))      替代 isqrt(10005·one·one)
  2. ((Q*426880)*sqrt_c)//T    替代 (Q*426880*sqrt_c)//T
  3. format(value,'d')         替代 str(value)
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
N = int(os.environ.get("PI_N", "10000000"))


def bs_simple(terms):
    def rec(lo, hi):
        if hi - lo == 1:
            if lo == 0:
                P = Q = mpz(1)
            else:
                kk = mpz(lo)
                P = (6 * kk - 5) * (2 * kk - 1) * (6 * kk - 1)
                Q = kk * kk * kk * mpz(C3)
            T = P * (13591409 + 545140134 * lo)
            if lo & 1:
                T = -T
        else:
            m = (lo + hi) // 2
            P1, Q1, T1 = rec(lo, m)
            P2, Q2, T2 = rec(m, hi)
            return P1 * P2, Q1 * Q2, Q2 * T1 + P1 * T2
        return P, Q, T
    return rec(0, terms)


def tail_old(P, Q, T, total, n):
    one = mpz(10) ** total
    sqrt_c = gmpy2.isqrt(10005 * one * one)
    value = (Q * 426880 * sqrt_c) // T
    return str(value)[1:1 + n].encode("ascii")


def tail_new(P, Q, T, total, n):
    sqrt_c = gmpy2.isqrt(mpz(10005) * mpz(10) ** (2 * total))
    value = ((Q * 426880) * sqrt_c) // T
    return format(value, "d")[1:1 + n].encode("ascii")


def main():
    reps = int(os.environ.get("PI_REPS", "6"))
    total = N + pb.GUARD_DIGITS
    terms = int(total / pb.DIGITS_PER_TERM) + 2
    pb.setup_console_utf8()
    seed = pb.seed_digits()[:N]
    print(f"位数 {N:,}   交错 {reps} 轮   CPU 时间\n")

    print("  先算一次 P/Q/T 复用（尾部本身不受影响）…")
    c0 = time.process_time()
    P, Q, T = bs_simple(terms)
    print(f"  binary splitting {time.process_time() - c0:.2f}s CPU\n")

    old, new = [], []
    for i in range(reps):
        c0 = time.process_time(); d1 = tail_old(P, Q, T, total, N)
        c1 = time.process_time() - c0
        c0 = time.process_time(); d2 = tail_new(P, Q, T, total, N)
        c2 = time.process_time() - c0
        same = (d1 == d2 == seed)
        old.append(c1); new.append(c2)
        print(f"  第 {i+1} 轮  旧 {c1:6.3f}s   新 {c2:6.3f}s   "
              f"{c1/c2:5.2f}x   一致 {'✅' if same else '❌'}")

    print()
    print(f"  旧：min {min(old):.3f}s  中位 {statistics.median(old):.3f}s")
    print(f"  新：min {min(new):.3f}s  中位 {statistics.median(new):.3f}s")
    r_min = min(old) / min(new)
    r_med = statistics.median(old) / statistics.median(new)
    print()
    print(f"  按最小值 {r_min:.3f}x    按中位数 {r_med:.3f}x")
    print(f"  节省：{min(old) - min(new):.3f}s CPU（{N:,} 位）")
    if r_med > 1.03:
        print("  → 结论：**有实质收益**")
    else:
        print("  → 结论：收益在噪声范围内")
    return 0


if __name__ == "__main__":
    sys.exit(main())
