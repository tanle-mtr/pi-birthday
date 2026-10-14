# -*- coding: utf-8 -*-
"""用 CPU 时间（process_time）代替墙钟时间做 A/B。

墙钟时间在这台机器上噪声极大：同一份代码不同轮次能差 2 倍
（7.03s ~ 13.31s），因为后台有其它进程抢 CPU。
process_time() 只统计本进程实际占用 CPU 的时长，不受调度等待影响，
是单线程基准测试更可靠的指标。
"""
import multiprocessing as mp
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


def bs_new(terms):
    return pb._bs_worker((0, terms))


def measure(fn, terms):
    """返回 (CPU时间, 墙钟时间)。"""
    c0, w0 = time.process_time(), time.perf_counter()
    r = fn(terms)
    return time.process_time() - c0, time.perf_counter() - w0, r


def check(P, Q, T, n=80):
    one = mpz(10) ** n
    sc = gmpy2.isqrt(10005 * one * one)
    return str((Q * 426880 * sc) // T).startswith(pb.PI_PREFIX_WITH_INT[:n].decode())


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000_000
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    terms = int((n + pb.GUARD_DIGITS) / pb.DIGITS_PER_TERM) + 2
    pb.setup_console_utf8()
    print(f"位数 {n:,}  项数 {terms:,}  交错 {reps} 轮")
    print("（同时记录 CPU 时间与墙钟时间，对比两者的噪声）\n")

    oc, ow, nc, nw = [], [], [], []
    for i in range(reps):
        c1, w1, r1 = measure(bs_old, terms)
        c2, w2, r2 = measure(bs_new, terms)
        oc.append(c1); ow.append(w1); nc.append(c2); nw.append(w2)
        print(f"  第 {i+1} 轮  CPU: 旧 {c1:6.2f} 新 {c2:6.2f} ({c1/c2:.2f}x) | "
              f"墙钟: 旧 {w1:6.2f} 新 {w2:6.2f} ({w1/w2:.2f}x)")
        del r1, r2

    def stats(xs):
        return min(xs), statistics.median(xs)

    ocm, ocmd = stats(oc); ncm, ncmd = stats(nc)
    owm, owmd = stats(ow); nwm, nwmd = stats(nw)

    print()
    print("  CPU 时间（更可靠）：")
    print(f"    旧  min {ocm:.2f}s  中位 {ocmd:.2f}s")
    print(f"    新  min {ncm:.2f}s  中位 {ncmd:.2f}s")
    print(f"    → min 提速 {ocm/ncm:.2f}x   中位提速 {ocmd/ncmd:.2f}x")
    print()
    print("  墙钟时间（噪声大）：")
    print(f"    旧  min {owm:.2f}s  中位 {owmd:.2f}s")
    print(f"    新  min {nwm:.2f}s  中位 {nwmd:.2f}s")
    print(f"    → min 提速 {owm/nwm:.2f}x   中位提速 {owmd/nwmd:.2f}x")

    _, _, (P, Q, T) = measure(bs_new, terms)
    print(f"\n  正确性：{'✅' if check(P, Q, T) else '❌'}")
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
