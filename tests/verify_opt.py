# -*- coding: utf-8 -*-
"""验证 binary splitting 优化的正确性与速度。

必须带 main 守卫：多进程 spawn 会让子进程重新导入本模块，
没有守卫就会递归创建进程池、把机器拖垮。
"""
import multiprocessing as mp
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb


def main():
    pb.setup_console_utf8()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
    seed = pb.seed_digits()
    print(f"位数 {n:,}   内置基准 {len(seed):,} 位")
    print()
    print(f"{'进程':>5}{'耗时s':>10}{'与内置逐字节一致':>18}")
    print("-" * 36)
    for w in (1, 2, 4):
        t = time.time()
        d = pb.compute_pi_gmpy2(n, workers=w)
        dt = time.time() - t
        same = (d == seed[:n])
        print(f"{w:>5}{dt:>10.2f}{'✅' if same else '❌':>16}")
        if not same:
            for i, (a, b) in enumerate(zip(d, seed)):
                if a != b:
                    print(f"      首个差异在第 {i} 位: {chr(a)} vs {chr(b)}")
                    break
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
