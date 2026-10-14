# -*- coding: utf-8 -*-
"""测量计算 π 的峰值内存与耗时。

用法： python measure_mem.py <位数> <进程数>
输出： 一行 JSON，含 peak_mb / seconds / avail_before_mb / avail_after_mb

用 GetProcessMemoryInfo 取本进程的真实峰值工作集（不是采样估计）。
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def peak_mb():
    """本进程的峰值工作集。

    必须设 argtypes/restype —— 不设的话 ctypes 会按默认的 int 传参，
    在 64 位下静默失败（返回 0），早期版本就踩过这个坑。
    """
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    pmc = PROCESS_MEMORY_COUNTERS()
    pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    ok = fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    if not ok:
        return -1.0
    return pmc.PeakWorkingSetSize / 1048576


def avail_mb():
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 1048576


def main():
    n = int(sys.argv[1])
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    import multiprocessing as mp
    mp.freeze_support()
    import pi_birthday as pb

    a0 = avail_mb()
    t0 = time.time()
    d = pb.compute_pi_gmpy2(n, workers=workers, log=None)
    dt = time.time() - t0
    a1 = avail_mb()

    ok = (len(d) == n) and d[:16] == pb.PI_PREFIX_DEC[:16]
    print(json.dumps({
        "n": n, "workers": workers, "peak_mb": round(peak_mb(), 1),
        "sec": round(dt, 2), "avail_before": round(a0), "avail_after": round(a1),
        "ok": ok,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
