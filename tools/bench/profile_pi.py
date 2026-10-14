# -*- coding: utf-8 -*-
"""计算 π 的逐阶段性能剖析 —— 用来定位「优化哪里才真的有收益」。

输出每个阶段的墙钟耗时与占比，作为后续优化的基线。
用法： py -3.14 profile_pi.py [位数] [进程数]
"""
import gc
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import pi_birthday as pb


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    import gmpy2
    from gmpy2 import mpz

    pb.setup_console_utf8()
    print(f"位数 {n:,}   进程数 {workers}   Python {sys.version.split()[0]}   "
          f"gmpy2 {gmpy2.version()} (GMP {gmpy2.mp_version()})")
    print()

    stages = []

    def stage(name, fn):
        gc.collect()
        t = time.time()
        r = fn()
        dt = time.time() - t
        stages.append((name, dt))
        print(f"  {name:<26}{dt:>9.3f}s")
        return r

    total_digits = n + pb.GUARD_DIGITS
    terms = int(total_digits / pb.DIGITS_PER_TERM) + 2
    print(f"  Chudnovsky 项数：{terms:,}")
    print()

    t0 = time.time()

    if workers <= 1:
        P, Q, T = stage("binary splitting(单进程)",
                        lambda: pb._bs_worker((0, terms)))
    else:
        ranges = pb._split_ranges(terms, workers)

        def par():
            import multiprocessing as mp
            with mp.Pool(len(ranges)) as pool:
                parts = pool.map(pb._bs_worker, ranges)
                while len(parts) > 1:
                    pairs = [(parts[i], parts[i + 1])
                             for i in range(0, len(parts) - 1, 2)]
                    merged = pool.map(pb._combine_worker, pairs)
                    if len(parts) % 2 == 1:
                        merged.append(parts[-1])
                    parts = merged
                return parts[0]
        P, Q, T = stage(f"binary splitting({workers}进程)", par)

    one = stage("10**n", lambda: mpz(10) ** total_digits)
    sqrt_c = stage("isqrt(10005*one^2)",
                   lambda: gmpy2.isqrt(10005 * one * one))
    del one
    gc.collect()

    a = stage("Q*426880", lambda: Q * 426880)
    num = stage("*(sqrt_c)", lambda: a * sqrt_c)
    del a, sqrt_c
    gc.collect()

    value = stage("// T  (最终除法)", lambda: num // T)
    del num, P, Q, T
    gc.collect()

    s = stage("str(value)", lambda: str(value))
    dec = stage("切片+encode", lambda: s[1:1 + n].encode("ascii"))
    del s, value
    gc.collect()

    total = time.time() - t0
    print()
    print(f"  {'合计':<26}{total:>9.3f}s")
    print()
    print("  占比：")
    for name, dt in sorted(stages, key=lambda x: -x[1]):
        bar = "█" * max(1, int(dt / total * 40))
        print(f"    {name:<26}{dt / total * 100:>5.1f}%  {bar}")

    ok = dec[:16] == pb.PI_PREFIX_DEC[:16] and len(dec) == n
    print(f"\n  正确性：{'✅' if ok else '❌'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
