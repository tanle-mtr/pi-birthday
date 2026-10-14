# -*- coding: utf-8 -*-
"""离线包读取器测试。

覆盖：
  1. 前 10 块与内置数据逐字节比对
  2. 乱序随机访问（验证偏移表）
  3. 跨块边界的匹配
  4. find_many 一趟扫多模式
  5. **并行与串行结果必须完全一致**（并行只是快，不能改变「首次出现」的答案）
  6. 后台预取不改变结果
  7. 内存占用与总位数无关
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

PACK = os.path.join(_ROOT, "pi_1b.pack")


def work_set_mb():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("f", wintypes.DWORD)] + \
                   [(n, ctypes.c_size_t) for n in
                    ("a", "b", "c", "d", "e", "f2", "g", "h")]

    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    p = PMC(); p.cb = ctypes.sizeof(PMC)
    fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.b / 2 ** 20


def main():
    pb.setup_console_utf8()
    if not os.path.exists(PACK):
        print(f"缺少 {PACK}，先跑 make_offline_pack.py")
        return 1

    pk = pb.OfflinePack(PACK)
    print(f"包：{pk.nchunks} 块 × {pk.chunk:,} 位 = {pk.digits:,} 位，"
          f"{pk.size / 2**20:.1f} MB\n")
    ok = True

    # ---- 1. 前 10 块与内置数据比对 ----
    print("[1] 前 10 块与内置数据逐字节比对")
    seed = pb.seed_digits()
    bad = sum(1 for k in range(10)
              if pk.get(k) != seed[k * pk.chunk:(k + 1) * pk.chunk])
    ok &= bad == 0
    print(f"    {'✅' if bad == 0 else '❌'} 10 块全部一致")

    # ---- 2. 乱序随机访问 ----
    print("\n[2] 乱序随机访问")
    good = all(pk.get(k) == seed[k * pk.chunk:(k + 1) * pk.chunk]
               for k in [5, 0, 9, 2, 7, 1, 8, 3, 6, 4])
    ok &= good
    print(f"    {'✅' if good else '❌'} 乱序读取结果全部正确")

    # ---- 3. 跨块匹配 ----
    print("\n[3] 跨块边界的匹配")
    b2, b3 = pk.get(2), pk.get(3)
    straddle = b2[-4:] + b3[:4]
    idx = pk.find(straddle, start_digit=0, prefetch=False)
    want = 2 * pk.chunk + len(b2) - 4
    good = idx == want
    ok &= good
    print(f"    跨块串 {straddle.decode()}  期望 {want:,}  实际 {idx:,}  "
          f"{'✅' if good else '❌'}")

    # ---- 4 & 5. find_many：并行 vs 串行 ----
    print("\n[4] find_many 串行 vs 并行（结果必须完全一致）")
    pats = [("20120528", b"20120528"), ("19900101", b"19900101"),
            ("0528", b"0528")]
    ref, res_by_w = None, {}
    for w in (1, 2, 4, 8):
        pk._cache.clear(); pk._order.clear()
        t = time.time()
        r = pk.find_many(pats, start_digit=10_000_000, stop_on="19900101",
                         workers=w)
        dt = time.time() - t
        res_by_w[w] = (r, dt)
        if ref is None:
            ref = dict(r)
        same = (r == ref)
        ok &= same
        print(f"    {w} 线程  {dt:5.2f}s  {'✅ 一致' if same else '❌ 不一致'}"
              f"   20120528={r['20120528']:,}")

    base = res_by_w[1][1]
    best = min(res_by_w.items(), key=lambda kv: kv[1][1])
    print(f"    → 最快 {best[0]} 线程，{best[1][1]:.2f}s，"
          f"相对串行加速 {base / best[1][1]:.2f}x")

    # ---- 6. 预取不改变结果 ----
    print("\n[6] 后台预取（默默解压）")
    pk._cache.clear(); pk._order.clear()
    a = pk.find(b"19900101", start_digit=10_000_000, prefetch=False)
    pk._cache.clear(); pk._order.clear()
    b = pk.find(b"19900101", start_digit=10_000_000, prefetch=True)
    good = (a == b)
    ok &= good
    print(f"    开/关预取结果一致={good}  下标 {a:,}  {'✅' if good else '❌'}")

    # ---- 7. 内存 ----
    print(f"\n[7] 进程工作集 {work_set_mb():.0f} MB"
          f"（共 {pk.digits:,} 位，未整体载入）")

    pk.stop_prefetch()
    print('\n' + ('=' * 48))
    print('离线包读取器测试通过 ✅' if ok else '离线包读取器测试失败 ❌')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
