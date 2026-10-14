# -*- coding: utf-8 -*-
"""自检：π 数字正确性 + 串行/并行搜索结果一致性 + 边界用例"""
import os, sys, ssl, time, urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import pi_birthday as pb


def main():
    pb.setup_console_utf8()
    ok = True

    # ---------- 1. π 数字正确性：与 angio.net 的 100 万位逐字节比对 ----------
    print("[1] 校验 π 数字正确性（100 万位，对照 angio.net）")
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request("https://www.angio.net/pi/digits/pi1000000.txt",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
        raw = r.read()
    ref_all = pb.clean_digits(raw)
    ref_dec = ref_all[1:1_000_001]          # 去掉整数位的 3
    print(f"    参考数据：{len(ref_all):,} 位（含整数位），小数 {len(ref_dec):,} 位")

    t = time.time()
    mine = pb.compute_pi_gmpy2(1_000_000, workers=4, log=lambda m: print("      " + m))
    dt = time.time() - t
    print(f"    本地计算：{dt:.2f}s，{len(mine):,} 位")
    if mine == ref_dec:
        print(f"    ✅ 100 万位完全一致（逐字节 MATCH）  速度：{1_000_000/dt/1000:.0f} 千位/秒")
    else:
        ok = False
        for i, (a, b) in enumerate(zip(mine, ref_dec)):
            if a != b:
                print(f"    ❌ 第 {i} 位不一致：本地={chr(a)} 参考={chr(b)}")
                break

    # ---------- 2. 并行 vs 串行 搜索一致性 ----------
    print("\n[2] 校验并行搜索 == 串行搜索")
    pats = pb.build_patterns("19900101")
    seq = pb.search_sequential(pats, mine)
    par = pb.search_parallel(pats, mine, workers=4, log=lambda m: print("      " + m))
    same = seq == par
    ok &= same
    print(f"    串行结果：{seq}")
    print(f"    并行结果：{par}")
    print(f"    {'✅ 完全一致' if same else '❌ 不一致'}")

    # ---------- 3. 边界用例 ----------
    print("\n[3] 边界用例")
    fake = b"1234567890" * 3          # 30 位
    cases = [
        ("开头匹配", b"1234567890" * 3, "12345678"),
        ("跨块边界", fake, "90123456"),
        ("结尾匹配", fake, "567890"),
        ("不存在", fake, "0000000"),
    ]
    for label, data, pat in cases:
        s = pb.search_sequential([("p", pat)], data)[pat]
        p = pb.search_parallel([("p", pat)], data, workers=2)[pat]
        good = (s == p) and (s == data.find(pat.encode()))
        ok &= good
        print(f"    {'✅' if good else '❌'} {label:<8} find={data.find(pat.encode())} "
              f"seq={s} par={p}")

    # ---------- 4. 生日模式构造 ----------
    print("\n[4] 生日模式构造")
    for b in ("19900101", "20001231", "1985-06-07"):
        bd = pb.parse_birthday(b)
        print(f"    {b:<12} -> {bd}  {[(l, p) for l, p in pb.build_patterns(bd)]}")

    # ---------- 5. 渲染层：结果文本必须反映真实命中位置 ----------
    # （这一节专门捕获「字典键用标签、取值用模式」这类展示 bug）
    print("\n[5] 渲染层校验：结果文本必须包含真实位置")
    pats = pb.build_patterns("19900101")
    res = pb.search_sequential(pats, mine)
    for label, pat in pats:
        idx = res[pat]
        expect = f"第 {idx + 1:,} 位" if idx >= 0 else "未出现"
        print(f"    {label:<18} {pat:<10} 期望文本: {expect}")
    hit_pat, hit_idx = "0101", res["0101"]
    if hit_idx >= 0 and f"第 {hit_idx + 1:,} 位" not in pb.format_position(hit_idx):
        ok = False
        print("    ❌ format_position 渲染错误")
    else:
        print(f"    ✅ 命中项渲染正确（{hit_pat} -> 第 {hit_idx + 1:,} 位）")

    # 端到端：run_search 的输出文本必须出现该位置
    text, _ = pb.run_search("1990-01-01", 1_000_000, "compute")
    needle = f"第 {hit_idx + 1:,} 位"
    if needle in text:
        print(f"    ✅ 端到端文本包含 '{needle}'")
    else:
        ok = False
        print(f"    ❌ 端到端文本缺少 '{needle}'")
        print("    ---- 实际输出 ----")
        for ln in text.splitlines():
            print("      " + ln)

    # ---------- 6. 纯离线模式：必须绝不联网 ----------
    # 用一个「网络炸弹」把 urllib 打瘫；离线路径若还能跑通，
    # 就证明它确实没有任何网络访问。
    print("\n[6] 纯离线模式校验（网络炸弹）")
    calls = {"n": 0}

    real_urlopen = urllib.request.urlopen

    def bomb(*a, **k):
        calls["n"] += 1
        raise AssertionError("纯离线模式下竟然发起了网络请求！")

    urllib.request.urlopen = bomb
    try:
        pb.set_offline(True)

        # 6a. 内置数据：请求「网络下载」也必须走离线路径
        pb.SEED_FILES_ORIG = list(pb.SEED_FILES)
        pb._seed_cache.clear()
        seed_name, seed = pb.load_seed()
        if seed is None:
            ok = False
            print("    ❌ 未找到内置 π 数据（pi_seed_10m.z）")
        else:
            print(f"    内置数据：{seed_name}（{len(seed):,} 位）")
            n = 5_000_000                       # 未缓存过，且被内置数据覆盖
            p = pb.cache_path(n)
            if os.path.exists(p):
                os.remove(p)
            t = time.time()
            d = pb.acquire_digits(n, method="download", log=lambda m: print("      " + m))
            dt = time.time() - t
            good = (len(d) == n) and d[:16] == pb.PI_PREFIX_DEC[:16]
            ok &= good
            print(f"    {'✅' if good else '❌'} 离线 + 请求下载模式 → 秒出 {len(d):,} 位，"
                  f"耗时 {dt:.3f}s，未联网")

        # 6b. 计算路径：屏蔽内置数据后，离线必须靠本地计算
        pb.SEED_FILES.clear()
        pb._seed_cache.clear()
        n2 = 150_000                            # 未缓存过
        p2 = pb.cache_path(n2)
        if os.path.exists(p2):
            os.remove(p2)
        t = time.time()
        d2 = pb.acquire_digits(n2, method="auto", log=lambda m: print("      " + m))
        dt2 = time.time() - t
        good2 = (len(d2) == n2) and d2[:16] == pb.PI_PREFIX_DEC[:16]
        ok &= good2
        print(f"    {'✅' if good2 else '❌'} 无内置数据 + 离线 → 本地计算 {len(d2):,} 位，"
              f"耗时 {dt2:.2f}s，未联网")

        # 6c. 全程零网络调用
        good3 = calls["n"] == 0
        ok &= good3
        print(f"    {'✅' if good3 else '❌'} 全程网络调用次数 = {calls['n']}（期望 0）")

        pb.SEED_FILES.extend(pb.SEED_FILES_ORIG)
        pb._seed_cache.clear()
    finally:
        urllib.request.urlopen = real_urlopen
        pb.set_offline(False)

    print("\n" + ("=" * 50))
    print("全部自检通过 ✅" if ok else "存在失败项 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
