# -*- coding: utf-8 -*-
"""逐阶段测量计算 π 的内存占用，定位「字节/位」花在哪里。

用法： python measure_phases.py <位数>
"""
import ctypes
import gc
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def mem():
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    p = PMC(); p.cb = ctypes.sizeof(PMC)
    fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.WorkingSetSize / 1048576, p.PeakWorkingSetSize / 1048576


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000_000
    import gmpy2
    from gmpy2 import mpz
    import pi_birthday as pb

    cur0, peak0 = mem()
    print(f"位数 {n:,}    起始 当前={cur0:.0f}MB 峰值={peak0:.0f}MB")
    print(f"{'阶段':<22}{'当前MB':>9}{'峰值MB':>9}{'增量MB':>9}")
    print("-" * 52)

    marks = [("起始", cur0, peak0)]

    def mark(name):
        gc.collect()
        c, p = mem()
        d = c - marks[-1][1]
        marks.append((name, c, p))
        print(f"{name:<22}{c:>9.0f}{p:>9.0f}{d:>+9.0f}")
        return c, p

    total_digits = n + pb.GUARD_DIGITS
    terms = int(total_digits / pb.DIGITS_PER_TERM) + 2
    mark("准备")

    t0 = time.time()
    P, Q, T = pb._bs_worker((0, terms))          # 单进程，与省内存策略一致
    mark("binary splitting")
    t1 = time.time()

    one = mpz(10) ** total_digits
    mark("10**n")

    sqrt_c = gmpy2.isqrt(10005 * one * one)
    mark("isqrt(10005*one^2)")
    t2 = time.time()

    del one
    gc.collect()
    mark("释放 one")

    value = (Q * 426880 * sqrt_c) // T
    mark("乘除得 pi*10^n")
    t3 = time.time()

    del P, Q, T, sqrt_c
    gc.collect()
    mark("释放中间量")

    s = str(value)
    mark("str()")
    t4 = time.time()

    b = s[1:1 + n].encode("ascii")
    mark("encode()")
    t5 = time.time()

    print("-" * 52)
    _, peak = mem()
    print(f"峰值 {peak:.0f} MB = {peak * 1048576 / n:.2f} 字节/位")
    print(f"耗时: 分裂 {t1-t0:.1f}s | isqrt {t2-t1:.1f}s | 乘除 {t3-t2:.1f}s | "
          f"str {t4-t3:.1f}s | encode {t5-t4:.1f}s")

    # 各阶段的理论下限（二进制位数 ≈ n*0.415 字节）
    theo = n * 0.4152 / 1048576
    print(f"\n单个 n 位大整数的理论大小 ≈ {theo:.0f} MB")
    print(f"峰值 / 单个数 = {peak / theo:.1f}x")
    ok = b[:16] == pb.PI_PREFIX_DEC[:16] and len(b) == n
    print("正确性:", ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
