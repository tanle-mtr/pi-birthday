#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生日在圆周率第几位？ —— 命令行入口。

三级数据来源，逐级往下推进，找到完整生日即停：

    ① 内置 1000 万位（随包分发，秒出）
    ② 离线数据包 pi_1b.pack（10 亿位，多线程按块搜索，几秒）
    ③ 本地 gmpy2 计算（内存优先，逐级翻倍）

用法示例：

    pi-birthday 1990-01-01                  # 自动逐级查
    pi-birthday 1990-01-01 --json           # 机器可读输出
    pi-birthday 1990-01-01 --no-compute     # 只查内置 + 离线包（不本地计算）
    pi-birthday 1990-01-01 --pack pi_1b.pack
    pi-birthday --info                      # 查看数据源与并行度上限
    pi-birthday 1990-01-01 --quiet          # 只输出结果
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pi_birthday as pb

__version__ = "1.0.0"

# 退出码：便于脚本判断
EXIT_FOUND = 0        # 找到了完整生日
EXIT_NOT_FOUND = 1    # 数据源都查完仍未找到
EXIT_ERROR = 2        # 参数或运行错误


# ============================================================================
# 输出
# ============================================================================
class Printer:
    """统一管理「安静模式 / 详细模式 / JSON 模式」的输出。"""

    def __init__(self, quiet=False, verbose=False, as_json=False, stream=None):
        self.quiet = quiet
        self.verbose = verbose
        self.as_json = as_json
        self.out = stream or sys.stdout
        self.logs = []

    def log(self, msg):
        """过程信息（进度、耗时）—— 安静模式和 JSON 模式下不打印。"""
        self.logs.append(msg)
        if self.quiet or self.as_json:
            return
        if self.verbose or True:      # 默认就显示关键过程
            print(f"  · {msg}", file=self.out, flush=True)

    def result(self, text):
        if not self.quiet and not self.as_json:
            print(text, file=self.out)

    def raw(self, text=""):
        if not self.as_json:
            print(text, file=self.out)


def human(n):
    return f"{n:,}"


def fmt_pos(idx):
    return f"第 {idx + 1:,} 位" if idx is not None and idx >= 0 else "未出现"


# ============================================================================
# 三级搜索
# ============================================================================
def search_builtin(birthday, pats, printer):
    """① 内置数据（秒出）。返回 {pat: idx}。"""
    name, seed = pb.load_seed(log=printer.log if printer.verbose else None)
    if seed is None:
        return None, None
    res = {pat: seed.find(pat.encode("ascii")) for _label, pat in pats}
    return res, len(seed)


def search_pack(pack, pats, printer, workers=0):
    """② 离线数据包（多线程按块并行，结果按块序取，保证是首次出现）。"""
    def on_step(scanned, total, nfound):
        pct = scanned / total * 100
        printer.log(f"离线包搜索中… {pct:5.1f}%  "
                    f"已到第 {human(scanned)} 位（命中 {nfound} 项）")

    pairs = [(pat, pat.encode("ascii")) for _label, pat in pats]
    stop_on = pats[0][1] if pats else None
    hits = pack.find_many(pairs, start_digit=0, on_step=on_step,
                          stop_on=stop_on, workers=workers)
    return hits, pack.digits


def search_compute(birthday, pats, printer, count, workers=0):
    """③ 本地计算（内存优先，单/多进程由 pi_birthday 决定）。"""
    digits = pb.acquire_digits(count, method="compute", workers=workers,
                               progress=None, log=printer.log)
    res = {}
    for _label, pat in pats:
        i = digits.find(pat.encode("ascii"))
        res[pat] = i
    return res, len(digits)


# ============================================================================
# 主流程
# ============================================================================
def run(args):
    printer = Printer(quiet=args.quiet, verbose=args.verbose, as_json=args.json)

    pb.setup_console_utf8()
    if args.offline:
        pb.set_offline(True)

    # ---- 解析生日 ----
    try:
        birthday = pb.parse_birthday(args.birthday)
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        return EXIT_ERROR

    pats = pb.build_patterns(birthday)
    yyyy, mm, dd = birthday[:4], birthday[4:6], birthday[6:]
    t_start = time.time()

    sources = []          # 记录每个数据源的搜索情况
    final = {}            # pat -> (idx, source, total_digits)
    ctxs = {}             # pat -> 上下文片段

    # ---- ① 内置数据 ----
    res, total = search_builtin(birthday, pats, printer)
    if res is not None:
        for pat, idx in res.items():
            if idx >= 0:
                final[pat] = (idx, "内置数据", total)
        sources.append({"source": "内置数据", "digits": total,
                        "found": sum(1 for v in res.values() if v >= 0)})
        printer.log(f"内置数据 {human(total)} 位：命中 "
                    f"{sum(1 for v in res.values() if v >= 0)} 项")
    else:
        printer.log("未找到内置数据（pi_seed_10m.z）")

    full_pat = pats[0][1]
    found_full = full_pat in final

    # ---- ② 离线数据包 ----
    pack = None
    if not found_full and not args.no_pack:
        pack_path = args.pack
        if pack_path:
            if not os.path.exists(pack_path):
                printer.log(f"⚠️ 指定的数据包不存在：{pack_path}")
            else:
                try:
                    pack = pb.OfflinePack(pack_path)
                except Exception as e:
                    printer.log(f"⚠️ 数据包打不开：{e}")
        else:
            pack = pb.open_pack(log=printer.log)

    if pack is not None and not found_full:
        t0 = time.time()
        hits, total = search_pack(pack, pats, printer, workers=args.pack_workers)
        dt = time.time() - t0
        for pat, idx in hits.items():
            if idx >= 0:
                prev = final.get(pat)
                # 取更早的那次出现（理论上一致，但防御性处理）
                if prev is None or idx < prev[0]:
                    final[pat] = (idx, "离线数据包", total)
                # 取上下文
                a = max(0, idx - 30)
                ctxs[pat] = (pack.slice(a, idx + len(pat) + 30).decode("ascii"),
                             idx - a)
        sources.append({"source": "离线数据包", "digits": total,
                        "found": sum(1 for v in hits.values() if v >= 0),
                        "seconds": round(dt, 2)})
        printer.log(f"离线数据包 {human(total)} 位：命中 "
                    f"{sum(1 for v in hits.values() if v >= 0)} 项，耗时 {dt:.2f}s")
        found_full = full_pat in final

    # ---- ③ 本地计算 ----
    if not found_full and not args.no_compute and args.compute_digits > 0:
        if not pb.HAS_GMPY2:
            printer.log("⚠️ 未安装 gmpy2，跳过本地计算（纯 Python 回退慢约 1000 倍）")
        else:
            t0 = time.time()
            res, total = search_compute(birthday, pats, printer,
                                        args.compute_digits, workers=args.workers)
            dt = time.time() - t0
            for pat, idx in res.items():
                if idx >= 0:
                    prev = final.get(pat)
                    if prev is None or idx < prev[0]:
                        final[pat] = (idx, "本地计算", total)
            sources.append({"source": "本地计算", "digits": total,
                            "found": sum(1 for v in res.values() if v >= 0),
                            "seconds": round(dt, 2)})
            printer.log(f"本地计算 {human(total)} 位：命中 "
                        f"{sum(1 for v in res.values() if v >= 0)} 项，耗时 {dt:.2f}s")
            found_full = full_pat in final

    elapsed = time.time() - t_start

    # ---- 输出 ----
    if args.json:
        payload = {
            "birthday": f"{yyyy}-{mm}-{dd}",
            "normalized": birthday,
            "found_full": found_full,
            "elapsed_seconds": round(elapsed, 3),
            "sources": sources,
            "results": [
                {
                    "label": label,
                    "format": _fmt_name(pat, birthday),
                    "pattern": pat,
                    "found": pat in final,
                    "position": (final[pat][0] + 1) if pat in final else None,
                    "position_0based": final[pat][0] if pat in final else None,
                    "source": final[pat][1] if pat in final else None,
                    "searched_digits": final[pat][2] if pat in final else None,
                }
                for label, pat in pats
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_FOUND if found_full else EXIT_NOT_FOUND

    # 人类可读
    printer.raw()
    printer.raw("=" * 64)
    printer.raw(f"🎂 生日 {yyyy}-{mm}-{dd}   （规范化：{birthday}）")
    printer.raw("=" * 64)
    printer.raw()

    width = max(len(l) for l, _ in pats)
    for label, pat in pats:
        if pat in final:
            idx, src, _tot = final[pat]
            printer.raw(f"  {label:<{width}}  {pat:<10}  {fmt_pos(idx):>18}   [{src}]")
            if pat in ctxs:
                ctx, pre = ctxs[pat]
                left, mid, right = ctx[:pre], ctx[pre:pre + len(pat)], ctx[pre + len(pat):]
                printer.raw(f"  {'':<{width}}  {left}【{mid}】{right}")
        else:
            printer.raw(f"  {label:<{width}}  {pat:<10}  {'未出现':>18}")

    printer.raw()
    printer.raw("-" * 64)
    for s in sources:
        extra = f"，{s['seconds']}s" if "seconds" in s else ""
        printer.raw(f"  {s['source']}：{human(s['digits'])} 位，命中 {s['found']} 项{extra}")
    printer.raw(f"  总耗时：{elapsed:.2f} 秒")

    printer.raw()
    if found_full:
        idx = final[full_pat][0]
        printer.raw(f"✅ 完整生日 {birthday} 出现在圆周率第 {human(idx + 1)} 位"
                    f"（小数位，从 1 起算）")
    else:
        printer.raw(f"❌ 已搜索的数据里没有完整生日 {birthday}。")
        printer.raw("   8 位数字在前 N 位的命中率 = 1 - e^(-N/1e8)：")
        printer.raw("     1000 万位 → 约 10%    1 亿位 → 约 63%    10 亿位 → 约 99.99%")
        if pack is None:
            printer.raw("   提示：下载 10 亿位离线包可把命中率提到 99.99%：")
            printer.raw("     python tools/make_offline_pack.py")
    printer.raw()
    return EXIT_FOUND if found_full else EXIT_NOT_FOUND


def _fmt_name(pat, birthday):
    yyyy, mm, dd = birthday[:4], birthday[4:6], birthday[6:]
    yy = yyyy[2:]
    return {
        birthday: "YYYYMMDD", yy + mm + dd: "YYMMDD", mm + dd + yy: "MMDDYY",
        dd + mm + yy: "DDMMYY", mm + dd: "MMDD",
    }.get(pat, "?")


# ============================================================================
# --info
# ============================================================================
def show_info(args):
    pb.setup_console_utf8()
    printer = Printer(quiet=False, verbose=True, as_json=args.json)

    seed_name, seed = pb.load_seed()
    pack = pb.open_pack()

    if args.json:
        print(json.dumps({
            "version": __version__,
            "python": sys.version.split()[0],
            "gmpy2": pb.HAS_GMPY2,
            "gmpy2_version": _gmpy2_version(),
            "cores": os.cpu_count(),
            "available_memory_mb": (round(pb.available_memory_bytes() / 2 ** 20)
                                    if pb.available_memory_bytes() else None),
            "builtin": {"file": seed_name, "digits": len(seed)} if seed else None,
            "pack": ({"path": pack.path, "digits": pack.digits,
                      "chunk": pack.chunk, "size_mb": round(pack.size / 2 ** 20)}
                     if pack else None),
            "max_compute_digits": pb.max_digits_for_memory(
                pb.available_memory_bytes(), 0.70, 1),
            "limits": {"max_workers": pb.MAX_WORKERS, "max_conns": pb.MAX_CONNS,
                       "pack_search_workers": pb.PACK_SEARCH_WORKERS},
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"生日在圆周率第几位？ v{__version__}")
    print("=" * 64)
    print(f"  Python        : {sys.version.split()[0]}")
    print(f"  gmpy2         : {'✅ ' + (_gmpy2_version() or '') if pb.HAS_GMPY2 else '❌ 未安装（本地计算会慢约 1000 倍）'}")
    print(f"  CPU 核心      : {os.cpu_count()}")
    avail = pb.available_memory_bytes()
    print(f"  可用内存      : {avail / 2 ** 20:.0f} MB" if avail else "  可用内存      : 未知")
    print()
    print("  数据源：")
    if seed:
        print(f"    ① 内置数据   : {seed_name}（{len(seed):,} 位）")
    else:
        print(f"    ① 内置数据   : ❌ 缺失（运行 python tools/make_seed.py 生成）")
    if pack:
        print(f"    ② 离线数据包 : {pack.path}")
        print(f"                   {pack.digits:,} 位，{pack.size / 2 ** 20:.0f} MB，"
              f"{pack.nchunks} 块 × {pack.chunk:,} 位")
    else:
        print(f"    ② 离线数据包 : ❌ 未找到（运行 python tools/make_offline_pack.py 生成）")
        print(f"                   有了它可把搜索范围从 1000 万位提到 10 亿位")
    cap = pb.max_digits_for_memory(avail, 0.70, 1)
    print(f"    ③ 本地计算   : 受内存限制，最多约 {cap:,} 位" if cap
          else "    ③ 本地计算   : 内存不足")
    print()
    print(f"  并行度上限    : 计算进程 {pb.MAX_WORKERS}，下载连接 {pb.MAX_CONNS}，"
          f"离线包搜索线程 {pb.PACK_SEARCH_WORKERS}")
    print()
    return 0


def _gmpy2_version():
    try:
        import gmpy2
        return gmpy2.version()
    except Exception:
        return None


# ============================================================================
# argparse
# ============================================================================
def build_parser():
    ap = argparse.ArgumentParser(
        prog="pi-birthday",
        description="查出生日在圆周率小数中首次出现的位置。"
                    "自动按「内置数据 → 离线数据包 → 本地计算」逐级搜索。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  pi-birthday 1990-01-01                自动逐级查（推荐）
  pi-birthday 1990-01-01 --json         机器可读输出（便于脚本处理）
  pi-birthday 1990-01-01 --no-compute   只查内置 + 离线包，不做本地计算
  pi-birthday 1990-01-01 -d 50000000    本地计算到 5000 万位
  pi-birthday --info                    查看数据源与并行度上限
  pi-birthday --info --json             环境信息（JSON）

退出码:
  0 = 找到完整生日    1 = 数据源都查完仍未找到    2 = 参数或运行错误
""")
    ap.add_argument("birthday", nargs="?", help="生日，如 1990-01-01 / 19900101 / 1990/1/1")
    ap.add_argument("--info", action="store_true", help="显示数据源与环境信息后退出")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    g = ap.add_argument_group("搜索范围")
    g.add_argument("--pack", metavar="PATH",
                   help="指定离线数据包路径（默认自动在若干位置查找 pi_1b.pack）")
    g.add_argument("--no-pack", action="store_true", help="不使用离线数据包")
    g.add_argument("--no-compute", action="store_true", help="不做本地计算（只查已有数据）")
    g.add_argument("-d", "--compute-digits", type=int, default=0, metavar="N",
                   help="本地计算到 N 位（默认 0 = 不计算；内存不足会自动下调）")
    g.add_argument("--pack-workers", type=int, default=0, metavar="N",
                   help=f"离线包搜索线程数（默认 {pb.PACK_SEARCH_WORKERS}）")

    g2 = ap.add_argument_group("计算与网络")
    g2.add_argument("-w", "--workers", type=int, default=0,
                    help=f"本地计算进程数（0=自动，最高 {pb.MAX_WORKERS}）")
    g2.add_argument("-c", "--conns", type=int, default=pb.DOWNLOAD_CONNS,
                    help=f"下载并发连接数（最高 {pb.MAX_CONNS}）")
    g2.add_argument("--offline", action="store_true",
                    help="纯离线模式：绝不联网")

    g3 = ap.add_argument_group("输出")
    g3.add_argument("--json", action="store_true", help="输出 JSON（便于脚本处理）")
    g3.add_argument("-q", "--quiet", action="store_true", help="只输出最终结果")
    g3.add_argument("-v", "--verbose", action="store_true", help="输出更多过程信息")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.info:
        return show_info(args)
    if not args.birthday:
        ap.print_help()
        return EXIT_ERROR

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:
        print(f"错误：{type(e).__name__}: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
