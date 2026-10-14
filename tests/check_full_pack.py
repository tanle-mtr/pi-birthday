# -*- coding: utf-8 -*-
"""用完整的 10 亿位离线包做端到端查询。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

PACK = os.path.join(_ROOT, "pi_1b.pack")


def main():
    pb.setup_console_utf8()
    t0 = time.time()
    pk = pb.OfflinePack(PACK)
    print(f"离线包：{pk.nchunks} 块 × {pk.chunk:,} 位 = {pk.digits:,} 位，"
          f"{pk.size / 2**20:.1f} MB（打开耗时 {time.time() - t0:.2f}s）\n")

    for birthday in ("20120528", "19900101", "20190914"):
        b = birthday
        pats = [(b, b.encode())]
        t = time.time()
        res = pk.find_many(pats, start_digit=0, stop_on=b)
        dt = time.time() - t
        i = res[b]
        if i >= 0:
            print(f"  {b} -> 第 {i + 1:,} 位   （扫了 {pk.digits:,} 位，耗时 {dt:.2f}s）")
        else:
            print(f"  {b} -> 10 亿位内未出现   （耗时 {dt:.2f}s）")

    # 内存占用
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("f", wintypes.DWORD)] + \
                   [(n, ctypes.c_size_t) for n in ("a", "b", "c", "d", "e", "f2", "g", "h")]

    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    p = PMC(); p.cb = ctypes.sizeof(PMC)
    fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    print(f"\n进程工作集 {p.b / 2**20:.0f} MB（共 {pk.digits:,} 位，未整体载入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
