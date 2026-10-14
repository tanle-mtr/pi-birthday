# -*- coding: utf-8 -*-
"""校验每个下载源：取回的数字必须与本地 gmpy2 计算结果逐字节一致。

背景：angio.net 的 pi1000000.txt 内含换行，字节偏移 ≠ 数字偏移。
旧实现按字节偏移 Range 切片，导致数据整体错位、搜索结果错误。
本脚本专门守住这条线。
"""
import os, sys, time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb


def main():
    pb.setup_console_utf8()
    N = 200_000

    ref = pb.load_cache(300_000)
    if ref is None:
        print("基准：本地计算 300000 位…")
        ref = pb.compute_pi_gmpy2(300_000, workers=4)
    else:
        print(f"基准：使用已缓存的本地计算结果（前 {len(ref):,} 位）")
    ref = ref[:N]

    ok = True
    for url, limit, contiguous in pb.PI_SOURCES:
        print(f"\n{'=' * 64}")
        print(f"数据源: {url}")
        print(f"  上限 {limit:,} 位 | 字节流连续: {contiguous}")
        print("=" * 64)
        t = time.time()
        try:
            got = pb._download_one(url, N, contiguous,
                                   log=lambda m: print("   " + m))
        except Exception as e:
            print(f"  ⚠️ 该源当前不可用（跳过）：{e}")
            continue
        dt = time.time() - t
        print(f"  → 取得 {len(got):,} 位，耗时 {dt:.1f}s")

        if got == ref:
            print(f"  ✅ 与本地计算逐字节一致")
        else:
            ok = False
            print(f"  ❌ 不一致！")
            for i, (a, b) in enumerate(zip(got, ref)):
                if a != b:
                    print(f"     首个差异在第 {i} 位：下载={chr(a)} 本地={chr(b)}")
                    print(f"     下载: {got[max(0, i - 15):i + 15].decode('ascii', 'ignore')}")
                    print(f"     本地: {ref[max(0, i - 15):i + 15].decode('ascii', 'ignore')}")
                    break

    print(f"\n{'=' * 64}")
    print("全部数据源校验通过 ✅" if ok else "存在错位/错误数据源 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
