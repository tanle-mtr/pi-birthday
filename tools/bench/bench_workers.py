# -*- coding: utf-8 -*-
"""并行度扫描：测量不同进程数下计算 π 的墙钟时间，找出真实最优点。

目的：验证「进程数越多越快」是否成立，并观察 spawn 开销与内存压力。
"""
import os, sys, time, ctypes

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import pi_birthday as pb


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def avail_mb():
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 1024 / 1024


def main():
    pb.setup_console_utf8()
    N = 1_000_000
    cores = os.cpu_count()
    print(f"位数={N:,}  逻辑核心={cores}  可用内存={avail_mb():.0f} MB")
    print(f"{'进程数':>6} {'耗时(s)':>9} {'加速比':>8} {'vs 1进程':>9} {'起始可用MB':>11} {'结束可用MB':>11}")
    print("-" * 66)

    base = None
    results = []
    for w in (1, 2, 4, 8, 16, 32):
        a0 = avail_mb()
        t = time.time()
        try:
            d = pb.compute_pi_gmpy2(N, workers=w)
        except Exception as e:
            print(f"{w:>6}  失败: {e}")
            continue
        dt = time.time() - t
        a1 = avail_mb()
        if base is None:
            base = dt
        results.append((w, dt))
        # 正确性必须始终一致
        head = d[:16].decode()
        assert head == "1415926535897932", f"位数{w}算错了: {head}"
        print(f"{w:>6} {dt:>9.2f} {base/dt:>7.2f}x {dt/base:>8.2f}x "
              f"{a0:>11.0f} {a1:>11.0f}")

    print("-" * 66)
    best_w, best_t = min(results, key=lambda r: r[1])
    print(f"最快：{best_w} 进程，{best_t:.2f}s")
    print(f"1 进程：{base:.2f}s")

    # 外推：按 spawn 开销线性估算 512 进程
    print("\n【外推】若开 512 进程：")
    print(f"  spawn 开销约 512 × {pb.SPAWN_COST_SEC}s = {512 * pb.SPAWN_COST_SEC:.0f}s（仅启动）")
    print(f"  进程基线内存约 512 × 20MB = {512 * 20 / 1024:.1f} GB"
          f"（本机总内存 7.6 GB，可用 {avail_mb():.0f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
