# -*- coding: utf-8 -*-
"""只对比 binary splitting 本身：旧实现 vs 新实现。

之前的孤立测试有两个偏差，这里修正：
  1. bench_leaf.py 用「单个叶子装 20000 项」测 int vs mpz ——
     那不是 K=32 的场景（叶子内数字规模完全不同），结论不适用
  2. bench_bs.py 用单次墙钟时间，噪声可达 2 倍

这里：交错多轮 + CPU 时间 + 取最小值 + 两者都校验。
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


def bs_old(terms):
    """旧：叶子=1 项，且用 Python int 先算再转 mpz。"""
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
            return P1 * P2, Q1 * Q2, Q2 * T1 + P1 * T2
        return P, Q, T
    return rec(0, terms)


def bs_new(terms):
    """新：叶子成块 BS_LEAF_CHUNK 项 + 全程 mpz（当前 pi_birthday 的实现）。"""
    return pb._bs_worker((0, terms))


def check(P, Q, T, n=80):
    one = mpz(10) ** n
    sc = gmpy2.isqrt(10005 * one * one)
    return str((Q * 426880 * sc) // T).startswith(pb.PI_PREFIX_WITH_INT[:n].decode())


def main():
    n = int(os.environ.get("PI_N", "5000000"))
    reps = int(os.environ.get("PI_REPS", "6"))
    terms = int((n + pb.GUARD_DIGITS) / pb.DIGITS_PER_TERM) + 2
    pb.setup_console_utf8()
    print(f"位数 {n:,}  项数 {terms:,}  交错 {reps} 轮  CPU 时间")
    print(f"新实现叶子块大小 K = {pb.BS_LEAF_CHUNK}\n")

    old, new = [], []
    for i in range(reps):
        c0 = time.process_time(); (P1, Q1, T1) = bs_old(terms)
        c1 = time.process_time() - c0
        c0 = time.process_time(); (P2, Q2, T2) = bs_new(terms)
        c2 = time.process_time() - c0
        same = (Q1 == Q2 and T1 == T2)
        old.append(c1); new.append(c2)
        print(f"  第 {i+1} 轮  旧 {c1:6.2f}s   新 {c2:6.2f}s   "
              f"{c1/c2:5.2f}x   结果一致 {'✅' if same else '❌'}")
        del P1, Q1, T1, P2, Q2, T2

    print()
    print(f"  旧：min {min(old):.2f}s  中位 {statistics.median(old):.2f}s")
    print(f"  新：min {min(new):.2f}s  中位 {statistics.median(new):.2f}s")
    r_min = min(old) / min(new)
    r_med = statistics.median(old) / statistics.median(new)
    print()
    print(f"  按最小值 {r_min:.3f}x    按中位数 {r_med:.3f}x")
    if r_min < 1.03 and r_med < 1.03:
        print("  → 结论：**没有实质收益**，在噪声范围内。")
    else:
        print(f"  → 结论：有实质收益。")
    _, _, (P, Q, T) = time.process_time(), None, bs_new(terms)
    print(f"  正确性：{'✅' if check(P, Q, T) else '❌'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
