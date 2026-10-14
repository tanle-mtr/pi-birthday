# -*- coding: utf-8 -*-
"""尾部（isqrt / 除法 / 进制转换）的微优化实验。

尾部占单进程总耗时约 30%，且全是单次大数运算 —— 无法并行，只能换算法。
用 CPU 时间测量，避免墙钟噪声。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

import gmpy2
from gmpy2 import mpz

N = int(os.environ.get("PI_N", "10000000"))


def cpu(fn):
    c0 = time.process_time()
    r = fn()
    return time.process_time() - c0, r


def main():
    pb.setup_console_utf8()
    total = N + pb.GUARD_DIGITS
    terms = int(total / pb.DIGITS_PER_TERM) + 2
    print(f"位数 {N:,}   项数 {terms:,}（用 CPU 时间测量）\n")

    print("  先做一次 binary splitting 拿到 P/Q/T …")
    c, (P, Q, T) = cpu(lambda: pb._bs_worker((0, terms)))
    print(f"  binary splitting: {c:.2f}s CPU\n")

    print("=" * 66)
    print("A. 求 sqrt(10005) 的定标值：isqrt(10005·10^2n) vs 其他写法")
    print("=" * 66)

    def way_isqrt():
        one = mpz(10) ** total
        return gmpy2.isqrt(10005 * one * one)

    def way_pow2n():
        return gmpy2.isqrt(mpz(10005) * mpz(10) ** (2 * total))

    def way_mpfr():
        # 用 MPFR 直接算浮点平方根，避开 2n 位整数
        bits = int(total * 3.3219280948873626) + 64
        with gmpy2.context() as ctx:
            ctx.precision = bits
            return gmpy2.sqrt(gmpy2.mpfr(10005))

    ref = None
    for name, fn in (("isqrt(10005*one*one) [当前]", way_isqrt),
                     ("isqrt(10005*10^(2n))", way_pow2n),
                     ("mpfr sqrt(10005)", way_mpfr)):
        c, r = cpu(fn)
        print(f"  {name:<30}{c:>8.3f}s CPU   类型={type(r).__name__}")
        if ref is None:
            ref = r

    print()
    print("=" * 66)
    print("B. 最终求值：(Q*426880*sqrt_c)//T 的写法")
    print("=" * 66)

    sqrt_c = way_isqrt()

    def eval_current():
        return (Q * 426880 * sqrt_c) // T

    def eval_mul_first():
        # 先把常数并进 Q，再做一次大乘法
        return ((Q * 426880) * sqrt_c) // T

    def eval_div_last():
        # 先算 Q/T 的高精度浮点，再乘
        bits = int(total * 3.3219280948873626) + 64
        with gmpy2.context() as ctx:
            ctx.precision = bits
            return gmpy2.mpfr(Q * 426880) / gmpy2.mpfr(T) * gmpy2.mpfr(sqrt_c)

    v_int = None
    for name, fn in (("(Q*426880*sqrt_c)//T [当前]", eval_current),
                     ("((Q*426880)*sqrt_c)//T", eval_mul_first),
                     ("mpfr 浮点乘除", eval_div_last)):
        c, r = cpu(fn)
        print(f"  {name:<30}{c:>8.3f}s CPU   类型={type(r).__name__}")
        if v_int is None:
            v_int = r

    print()
    print("=" * 66)
    print("C. 进制转换")
    print("=" * 66)
    for name, fn in (("str(value)", lambda: str(v_int)),
                     ("format(value,'d')", lambda: format(v_int, "d")),
                     ("value.digits(10)", lambda: v_int.digits(10))):
        c, r = cpu(fn)
        ok = len(r) >= N
        print(f"  {name:<30}{c:>8.3f}s CPU   长度={len(r):,} {'OK' if ok else 'BAD'}")

    print()
    print("=" * 66)
    print("D. 结论参考：各阶段 CPU 占比")
    print("=" * 66)
    c_split, _ = cpu(lambda: pb._bs_worker((0, terms)))
    c_sqrt, _ = cpu(way_isqrt)
    c_eval, _ = cpu(eval_current)
    c_str, _ = cpu(lambda: format(v_int, "d"))
    tot = c_split + c_sqrt + c_eval + c_str
    for name, c in (("binary splitting", c_split), ("isqrt", c_sqrt),
                    ("最终乘除", c_eval), ("进制转换", c_str)):
        print(f"  {name:<22}{c:>8.2f}s  {c / tot * 100:>5.1f}%")
    print(f"  {'合计':<22}{tot:>8.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
