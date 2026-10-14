# -*- coding: utf-8 -*-
"""生成内置 π 数据种子文件（zlib 压缩），供 exe 离线使用。

为什么要压缩：π 小数位的熵约 3.32 bit/位，zlib 能压到 ~42%。
PyInstaller 的 onefile 每次启动都会把内置文件解压到临时目录，
存压缩版能把「每次启动的解压量」从 10MB 降到 ~4MB。
"""
import os, sys, time, zlib, hashlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb

OUT = r"E:\编程作品\作品\DeepSeek\圆周率生日\pi_seed_10m.z"
N = 10_000_000


def main():
    pb.setup_console_utf8()
    print(f"生成 {N:,} 位 π 种子数据…")

    t = time.time()
    digits = pb.compute_pi_gmpy2(N, workers=4, log=lambda m: print("   " + m))
    dt = time.time() - t
    print(f"计算完成：{len(digits):,} 位，耗时 {dt:.1f}s")

    # 校验：必须与已缓存/已知值一致
    assert digits[:32] == pb.PI_PREFIX_DEC[:32], "前缀不对！"
    ref = pb.load_cache(1_000_000)
    if ref is not None:
        assert digits[:1_000_000] == ref, "与已验证的 100 万位缓存不一致！"
        print("✅ 前 100 万位与已验证缓存逐字节一致")
    else:
        print("⚠️ 无 100 万位缓存可对照，仅做了前缀校验")

    raw = digits
    comp = zlib.compress(raw, 9)
    with open(OUT, "wb") as f:
        f.write(comp)

    print(f"\n原始大小 : {len(raw) / 1024 / 1024:.2f} MB")
    print(f"压缩后   : {len(comp) / 1024 / 1024:.2f} MB "
          f"（{len(comp) / len(raw) * 100:.1f}%，理论下限约 41.5%）")
    print(f"输出     : {OUT}")
    print(f"sha256   : {hashlib.sha256(comp).hexdigest()[:32]}…")

    # 回读验证
    with open(OUT, "rb") as f:
        back = zlib.decompress(f.read())
    assert back == raw, "回读不一致！"
    print("✅ 回读解压一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
