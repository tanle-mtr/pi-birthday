#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生日在圆周率第几位？ —— 并行加速版
================================================================
加速手段（均为实测有效的路径）：
  1. gmpy2 (GMP) 计算 π           : 比纯 Python decimal 快约 1000 倍
  2. 多进程 binary splitting      : Chudnovsky 级数分块并行计算
  3. 多线程分段 Range 下载         : 8 连接并行拉取，比单连接快约 7 倍
  4. 磁盘二进制缓存               : 第二次查询 0 秒返回
  5. bytes.find (C 实现) 搜索      : 千万位仅需约 12 毫秒
  6. 多进程分块并行搜索 + 提前退出  : 用于 ≥64MB 的超大数据
  7. 自适应策略                   : 自动比较「本地计算」与「网络下载」预估耗时

数据来源：https://stuff.mit.edu/afs/sipb/contrib/pi/pi-billion.txt （10 亿位，支持 Range）
"""

from __future__ import annotations

import os
import sys
import time
import queue
import threading
import multiprocessing as mp

# ============================================================================
# 可选加速库
# ============================================================================
try:
    import gmpy2
    from gmpy2 import mpz

    HAS_GMPY2 = True
except Exception:  # pragma: no cover
    HAS_GMPY2 = False
    mpz = None


APP_NAME = "PiBirthday"

# 数据源：(URL, 该源最多可提供的小数位数, 字节流是否与数字位一一对应)
#   连续=True ：形如 "3.14159…" 的单一字节流，字节偏移可直接换算成数字下标
#   连续=False：含换行/制表等非数字字符，必须清洗后再按「数字下标」切片，
#               否则数据整体错位，会得出**错误**的搜索结果
PI_SOURCES = [
    ("https://stuff.mit.edu/afs/sipb/contrib/pi/pi-billion.txt", 1_000_000_000, True),
    ("https://www.angio.net/pi/digits/pi1000000.txt", 1_000_000, False),
]
PI_URL = PI_SOURCES[0][0]
PI_FIRST_DIGIT_OFFSET = 2            # 连续流中跳过 "3."
PI_MAX_DIGITS = 1_000_000_000        # 最强数据源的上限

# 用于校验取到的数据确实从第 1 位开始，防止错位/取到别的文件
PI_PREFIX_WITH_INT = b"31415926535897932384626433832795028841971693993751"
PI_PREFIX_DEC = PI_PREFIX_WITH_INT[1:]   # 从第一个小数位开始

# 单连接实测约 0.09~0.35 MB/s；8 连接实测约 0.6 MB/s
DOWNLOAD_CONNS = 8

# ---- 并行度上限（可按需调高，但请先读 README「并行度调优」一节）----
MAX_WORKERS = 512          # 计算进程数硬上限
MAX_CONNS = 512            # 下载并发连接数硬上限
# 自动模式下的默认计算进程数上限（超过物理核心数通常反而更慢）
AUTO_WORKERS_CAP = 8
# 每个下载分块至少这么大。分块太小会被 HTTP 往返开销吃掉全部收益：
# 512 连接 × 4 分块 = 2048 块，若总共只有 250KB，每块仅 122 字节，纯属浪费。
MIN_CHUNK_BYTES = 64 * 1024
# Windows 下 spawn 一个进程的实测开销（秒），用于给出启动耗时预警
SPAWN_COST_SEC = 0.4
# 每个 worker 进程的基线内存（Python + gmpy2 解释器开销），用于 OOM 保护
WORKER_BASELINE_BYTES = 20 * 1024 * 1024
# 本地计算的实测速度（位/秒，4 核并行，用于自适应决策）
COMPUTE_RATE = {True: 330_000, False: 10_000}  # gmpy2 / 纯 decimal
# 网络下载速率（字节/秒）保守估计
DOWNLOAD_RATE = 600_000
# 超过该长度才启用多进程并行搜索（否则 bytes.find 已经足够快）
PARALLEL_SEARCH_THRESHOLD = 64 * 1024 * 1024

DIGIT_TIERS = [
    ("10 万位（内置数据，秒出）", 100_000),
    ("100 万位（推荐，内置秒出）", 1_000_000),
    ("1000 万位（内置数据，秒出）", 10_000_000),
    ("1 亿位（需本地计算约 5~11 分钟）", 100_000_000),
]
DEFAULT_TIER_INDEX = 1


# ============================================================================
# 缓存目录
# ============================================================================
def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME, "cache")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        d = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "pi_cache")
        os.makedirs(d, exist_ok=True)
        return d


def cache_path(count: int) -> str:
    return os.path.join(cache_dir(), f"pi_d{count}.bin")


def load_cache(count: int):
    """读取缓存，返回 bytes 或 None。"""
    for c in (count,):
        p = cache_path(c)
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    data = f.read()
                if len(data) >= count:
                    return data[:count]
            except Exception:
                pass
    return None


def save_cache(count: int, data: bytes) -> None:
    try:
        p = cache_path(count)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except Exception:
        pass


# ============================================================================
# 离线模式 + 内置 π 数据
# ============================================================================
# 纯离线时完全不走网络。内置数据被打包进 exe，因此「不联网 + 不等待」也能出结果。
OFFLINE = False

# 内置种子文件（zlib 压缩的纯数字，按优先级）
SEED_FILES = ["pi_seed_10m.z", "pi_seed_1m.z"]
_seed_cache = {}


def set_offline(value: bool) -> None:
    """切换纯离线模式：开启后绝不发起任何网络请求。"""
    global OFFLINE
    OFFLINE = bool(value)


def is_offline() -> bool:
    return OFFLINE


def resource_path(name: str) -> str:
    """定位随程序分发的资源。打包后取 _MEIPASS，未打包时取脚本所在目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def load_seed(log=None):
    """载入内置 π 数据，返回 (文件名, 数字 bytes) 或 (None, None)。

    数据在打包时就已校验过；这里再验一次前缀，防止文件被替换或损坏。
    """
    if _seed_cache:
        v = _seed_cache.get("v")
        return (v if v else (None, None))

    import zlib

    result = (None, None)
    for fn in SEED_FILES:
        p = resource_path(fn)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                raw = zlib.decompress(f.read())
        except Exception as e:
            if log:
                log(f"⚠️ 内置数据 {fn} 解压失败（{e}），忽略")
            continue
        if not raw.startswith(PI_PREFIX_DEC[:16]):
            if log:
                log(f"⚠️ 内置数据 {fn} 前缀校验失败，忽略")
            continue
        result = (fn, raw)
        if log:
            log(f"载入内置 π 数据：{fn}（{len(raw):,} 位，无需联网、无需计算）")
        break

    _seed_cache["v"] = result
    return result


def seed_digits():
    """返回内置数据（bytes）或 None。"""
    return load_seed()[1]


# ============================================================================
# π 生成：gmpy2 + 多进程 binary splitting
# ============================================================================
_C3_OVER_24 = 640320 ** 3 // 24
DIGITS_PER_TERM = 14.181647462725477
GUARD_DIGITS = 32

# 叶子块大小 = 1：即递归到单个项为止（平衡归约）。
#
# 这一项是**实测否决过的优化**，留个记录免得以后有人再走一遍：
# 曾把叶子改成「一次循环算 K 项」以减少约 140 万个 Python 函数调用，
# 孤立测（bench_bs.py）看着有 1.1x，但严格交错 A/B（bench_bs_ab.py）显示
# **反而慢 9%**（0.91x，6 轮里 5 轮方向一致）：
# 叶子内部 `T = qk*T + P*tk` 会让 T 沿单项线性增长，
# 而递归版是平衡归约，乘法规模均匀得多 —— GMP 在大数上的乘法代价低得多。
# 结论：binary splitting 的瓶颈是 GMP 大数乘法本身，不是 Python 函数调用开销。
BS_LEAF_CHUNK = 1
# 分片数不超过这个值时，归约放在父进程里串行做（比并行归约树快，见 compute_pi_gmpy2）
FLAT_REDUCE_MAX_PARTS = 8


def _bs_worker(args):
    """[a, b) 区间的 binary splitting，返回 (P, Q, T)。必须是模块级函数以便 pickle。

    实现要点（都经过实测，别凭直觉改）：
      · 叶子 = 单项，保持平衡归约（见 BS_LEAF_CHUNK 的注释）
      · 叶子内部全程用 mpz，不要混用 Python int ——
        混用会让每次运算都在 int 与 mpz 间来回转换。
    """
    a, b = args
    from gmpy2 import mpz as M

    c3 = M(_C3_OVER_24)

    def bs(lo, hi):
        if hi - lo == 1:
            if lo == 0:
                P = Q = M(1)
            else:
                kk = M(lo)
                P = (6 * kk - 5) * (2 * kk - 1) * (6 * kk - 1)
                Q = kk * kk * kk * c3
            T = P * (13591409 + 545140134 * lo)
            if lo & 1:
                T = -T
        else:
            m = (lo + hi) // 2
            P1, Q1, T1 = bs(lo, m)
            P2, Q2, T2 = bs(m, hi)
            P = P1 * P2
            Q = Q1 * Q2
            T = Q2 * T1 + P1 * T2
        return P, Q, T

    return bs(a, b)


def _combine_worker(pair):
    """并行合并两段 binary splitting 结果。"""
    (P1, Q1, T1), (P2, Q2, T2) = pair
    return (P1 * P2, Q1 * Q2, Q2 * T1 + P1 * T2)


def _split_ranges(total: int, parts: int):
    step = max(1, total // parts)
    edges = [i * step for i in range(parts)] + [total]
    out = []
    for i in range(parts):
        a, b = edges[i], edges[i + 1]
        if b > a:
            out.append((a, b))
    return out


def available_memory_bytes():
    """当前可用物理内存（字节）；取不到返回 None。"""
    if os.name == "nt":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            m = _MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullAvailPhys)
        except Exception:
            return None
    return None


def auto_workers(terms: int, cores: int) -> int:
    """按工作量自动选择计算进程数。

    实测数据（本机 4 核，1,000,000 位，见 bench_workers.py）：
        1 进程 3.15s | 2 进程 3.09s | 4 进程 3.31s
        8 进程 4.05s | 16 进程 6.21s | 32 进程 9.18s
    小数据量下并行只会更慢 —— 因为 binary splitting 只占小头，
    合并 / isqrt / 最终除法 / 转十进制字符串全是串行的，再加每进程 spawn 开销。
    只有数据量足够大（约 ≥1000 万位）并行才划得来。
    """
    by_work = max(1, terms // 40_000)
    return max(1, min(cores, AUTO_WORKERS_CAP, by_work))


# ============================================================================
# 内存模型
# ============================================================================
# 实测（单进程，GetProcessMemoryInfo 取真实峰值，见 measure_mem.py / measure_phases.py）：
#     100 万位  33.0 MB      200 万位  43.8 MB
#     400 万位  67.3 MB      800 万位 112.4 MB     2000 万位 251.6 MB
# 拟合：峰值 ≈ 20 MB（解释器+gmpy2 基线）+ 11.7 字节/位。
# 但逐阶段剖析显示 2000 万位实测 12.8 字节/位，且峰值 78% 集中在
# 「binary splitting」与「最终乘除」两步（GMP 的大数乘法/除法要大量草稿空间）。
# 取 13 作为偏保守的规划值，宁可少算一级，也不要算到一半 OOM。
PEAK_BASE_BYTES = 20 * 1024 * 1024
PEAK_PER_DIGIT = 13.0


def estimate_peak_memory(digits: int, workers: int = 1) -> int:
    """估算计算 digits 位时的峰值内存（字节）。

    多进程时随规模增长的那部分会放大（每个 worker 都要持一份大整数），
    这里按 worker 数线性放大 —— 偏保守，宁可低估进程数。
    """
    growth = PEAK_PER_DIGIT * max(0, digits) * max(1, workers)
    return int(PEAK_BASE_BYTES + growth)


def max_digits_for_memory(avail_bytes, headroom: float = 0.55,
                          workers: int = 1):
    """在给定可用内存下，最多能算多少位。

    headroom 是留出的安全余量（0.55 表示最多占用可用内存的 55%）。
    返回 None 表示拿不到内存信息（不做限制）。
    """
    if not avail_bytes:
        return None
    budget = avail_bytes * headroom - PEAK_BASE_BYTES
    if budget <= 0:
        return 0
    return int(budget / (PEAK_PER_DIGIT * max(1, workers)))


def memory_report(digits: int, workers: int = 1) -> str:
    """给一行「预计内存 / 可用内存」的说明，用于界面提示。"""
    need = estimate_peak_memory(digits, workers)
    avail = available_memory_bytes()
    if not avail:
        return f"预计峰值内存约 {need / 2 ** 30:.2f} GB"
    pct = need / avail * 100
    flag = "✅" if pct < 55 else ("⚠️" if pct < 85 else "❌")
    return (f"{flag} 预计峰值内存 {need / 2 ** 20:.0f} MB / 可用 "
            f"{avail / 2 ** 20:.0f} MB（{pct:.0f}%）")


def check_worker_memory(workers: int, emit) -> int:
    """按可用内存限制进程数，避免 OOM。返回（可能下调后的）进程数。"""
    avail = available_memory_bytes()
    if not avail:
        return workers
    need = workers * WORKER_BASELINE_BYTES
    if need <= avail * 0.6:
        return workers
    safe = max(1, int(avail * 0.6 // WORKER_BASELINE_BYTES))
    emit(f"⚠️ {workers} 个进程预计占用约 {need / 2 ** 30:.1f} GB，"
         f"而当前可用内存只有 {avail / 2 ** 30:.1f} GB —— 继续运行会 OOM 或严重换页。")
    emit(f"   已自动下调为 {safe} 个进程。要真正用满 {workers} 进程，"
         f"需要更大的内存（每个进程基线约 {WORKER_BASELINE_BYTES // 2 ** 20} MB）。")
    return safe


def compute_pi_gmpy2(count: int, workers: int = 0, progress=None, log=None) -> bytes:
    """用 gmpy2 + 多进程 binary splitting 计算 π 的 count 位小数，返回纯数字 bytes。

    workers: 0 或负数 = 自动（取 min(AUTO_WORKERS_CAP, CPU 核数)）；
             显式指定时最高可到 MAX_WORKERS（512）。
    """
    if not HAS_GMPY2:
        raise RuntimeError("gmpy2 不可用")

    def emit(msg):
        if log:
            log(msg)

    cores = os.cpu_count() or 1
    total_digits = count + GUARD_DIGITS
    terms = int(total_digits / DIGITS_PER_TERM) + 2

    auto = workers is None or workers <= 0
    if auto:
        workers = auto_workers(terms, cores)
    else:
        workers = int(workers)
        if workers > MAX_WORKERS:
            emit(f"⚠️ 请求 {workers} 个进程，超过上限 {MAX_WORKERS}，已截断")
            workers = MAX_WORKERS

    # 进程数不能超过级数项数，否则会产生空区间、白白 spawn
    if workers > terms:
        emit(f"提示：级数只有 {terms:,} 项，进程数由 {workers} 下调为 {terms:,}")
        workers = terms
    workers = max(1, workers)

    # 内存保护：512 个 Python+gmpy2 进程约需 10 GB，本机不可能满足
    workers = check_worker_memory(workers, emit)

    emit(f"计算模式：gmpy2 (GMP) + {workers} 进程并行 binary splitting"
         f"（本机 {cores} 个逻辑核心{('，自动选择' if auto else '，手动指定')}）")
    emit(f"Chudnovsky 级数项数：{terms:,}")

    # ---- 超配预警：CPU 密集型任务开得比核心数多通常更慢 ----
    if workers > cores:
        emit(f"⚠️ 注意：{workers} 个进程 > {cores} 个逻辑核心。"
             f"实测超配不会更快，调度开销会抵消收益（见 bench_workers.py）。")
    if workers >= 64:
        emit(f"⚠️ {workers} 个进程在 Windows 下仅 spawn 就约需 "
             f"{workers * SPAWN_COST_SEC:.0f} 秒（每个约 {SPAWN_COST_SEC}s）。")

    t0 = time.time()
    ranges = _split_ranges(terms, workers)
    if len(ranges) < workers:
        emit(f"提示：实际有效区间 {len(ranges)} 个（部分区间为空已合并）")

    if len(ranges) == 1:
        P, Q, T = _bs_worker(ranges[0])
    else:
        with mp.Pool(len(ranges)) as pool:
            if progress:
                progress(5, f"并行计算中：{len(ranges)} 个进程…")
            parts = pool.map(_bs_worker, ranges)

            if len(ranges) <= FLAT_REDUCE_MAX_PARTS:
                # 父进程里串行归约。实测比「并行归约树」更快：
                # 树每层都要把 P/Q/T 这三个大整数 pickle 到子进程再传回来，
                # 而层数越靠上数字越大（1000 万位时 Q/T 各约 9 MB）。
                # 实测 4 进程 4 分片：串行归约 16.45s vs 归约树 18.79s。
                P, Q, T = parts[0]
                for P2, Q2, T2 in parts[1:]:
                    P, Q, T = P * P2, Q * Q2, Q2 * T + P * T2
            else:
                # 分片太多时串行归约会成为新的瓶颈（实测 16 分片：24.25s），
                # 这时才用并行归约树
                while len(parts) > 1:
                    pairs = [(parts[i], parts[i + 1])
                             for i in range(0, len(parts) - 1, 2)]
                    merged = pool.map(_combine_worker, pairs)
                    if len(parts) % 2 == 1:
                        merged.append(parts[-1])
                    parts = merged
                P, Q, T = parts[0]

    t1 = time.time()
    emit(f"binary splitting 完成：{t1 - t0:.2f}s")
    if progress:
        progress(70, "正在做最终除法（串行）…")

    # 直接算 10^(2n)，而不是先算 10^n 再平方：
    # 实测 isqrt 阶段 1.39s vs 1.72s（1000 万位，CPU 时间，1.24x）。
    # 两者数学上等价（10005·(10^n)² == 10005·10^(2n)），但少一次大数乘法。
    sqrt_c = gmpy2.isqrt(mpz(10005) * mpz(10) ** (2 * total_digits))

    # 先把小常数并进 Q 再做唯一那次大乘法：实测 2.44s vs 2.72s（1.11x）
    value = ((Q * 426880) * sqrt_c) // T
    del sqrt_c, P, Q, T

    t2 = time.time()
    emit(f"isqrt + 除法完成：{t2 - t1:.2f}s")
    if progress:
        progress(85, "正在转换为十进制…")

    # format(mpz, 'd') 比 str(mpz) 快：实测 2.84s vs 3.48s（1.23x，1000 万位）。
    # 千万别先 int(value) 再 str()：CPython 的 int→str 在这里要 27.6s，慢 5.5 倍。
    s = format(value, "d")
    del value
    # s = "3" + 小数位；取前 count 位小数
    decimals = s[1:1 + count]
    if len(decimals) < count:
        decimals = decimals.ljust(count, "0")

    t3 = time.time()
    emit(f"十进制转换完成：{t3 - t2:.2f}s（总计 {t3 - t0:.2f}s）")
    if progress:
        progress(95, "计算完成")
    return decimals.encode("ascii")


def compute_pi_decimal(count: int, progress=None, log=None) -> bytes:
    """无 gmpy2 时的降级方案：纯 Python decimal（很慢，仅适合小位数）。"""
    from decimal import Decimal, getcontext, localcontext

    def emit(msg):
        if log:
            log(msg)

    emit("警告：未检测到 gmpy2，降级为纯 Python decimal 计算（速度慢约 1000 倍）")
    if count > 200_000:
        emit(f"位数 {count:,} 过大，纯 Python 计算可能需要很久…")

    sys.setrecursionlimit(100000)
    c3 = Decimal(640320) ** 3 / 24

    def bs(a, b):
        if b - a == 1:
            if a == 0:
                P = Q = Decimal(1)
            else:
                P = Decimal((6 * a - 5) * (2 * a - 1) * (6 * a - 1))
                Q = Decimal(a * a * a) * c3
            T = P * (13591409 + 545140134 * a)
            if a & 1:
                T = -T
        else:
            m = (a + b) // 2
            P1, Q1, T1 = bs(a, m)
            P2, Q2, T2 = bs(m, b)
            P = P1 * P2
            Q = Q1 * Q2
            T = Q2 * T1 + P1 * T2
        return P, Q, T

    with localcontext() as ctx:
        ctx.prec = count + GUARD_DIGITS + 20
        terms = int((count + GUARD_DIGITS) / DIGITS_PER_TERM) + 2
        if progress:
            progress(20, "纯 Python 计算中…")
        P, Q, T = bs(0, terms)
        C = 426880 * Decimal(10005).sqrt()
        val = +((C * Q) / T)
    s = str(val)
    decimals = s.split(".")[1] if "." in s else s[1:]
    decimals = decimals[:count].ljust(count, "0")
    if progress:
        progress(95, "计算完成")
    return decimals.encode("ascii")


# ============================================================================
# 下载：多线程分段 Range 并行
# ============================================================================
def _http_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


class RangeUnsupported(Exception):
    """服务器忽略了 Range 头，返回的是从字节 0 开始的完整内容。"""


def _fetch_range_stream(url, a, b, ctx, timeout=90, attempts=2, allow_full=False):
    """流式下载字节区间 [a, b]（a=None 表示整个文件）。

    返回 (data, total_size, range_ok)。
    range_ok=False 表示服务器不支持 Range；此时 data 是从字节 0 开始的完整内容，
    调用方**不能**把它当作 [a, b] 的内容使用（否则数据错位）。
    """
    import urllib.request

    last_err = None
    for k in range(max(1, attempts)):
        try:
            headers = dict(_UA)
            if a is not None:
                headers["Range"] = f"bytes={a}-{b}" if b is not None else f"bytes={a}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                total, range_ok = None, True
                cr = resp.headers.get("Content-Range")
                if cr and "/" in cr:
                    try:
                        total = int(cr.rsplit("/", 1)[1])
                    except ValueError:
                        pass
                if a is not None and not cr:
                    # 请求了 Range 却没有 Content-Range：服务器忽略了它
                    range_ok = False
                if total is None and (a in (None, 0) or not range_ok):
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        try:
                            total = int(cl)
                        except ValueError:
                            pass

                want = None if (a is None or b is None) else (b - a + 1)
                buf = bytearray()
                while True:
                    blk = resp.read(262144)
                    if not blk:
                        break
                    buf += blk
                    if want is not None and len(buf) >= want:
                        break
                data = bytes(buf)

                if not range_ok and not allow_full:
                    raise RangeUnsupported(f"{url} 不支持 Range 请求")
                # 服务器多给了内容时截断，避免分块之间互相污染
                if range_ok and want is not None and len(data) > want:
                    data = data[:want]
                return data, total, range_ok
        except RangeUnsupported:
            raise
        except Exception as e:
            last_err = e
            if k + 1 < max(1, attempts):
                time.sleep(0.4 * (k + 1))
    raise RuntimeError(str(last_err))


def _clamp_conns(conns: int, log=None) -> int:
    """把下载并发数限制在 [1, MAX_CONNS]，并对高并发给出提醒。"""
    try:
        conns = int(conns)
    except (TypeError, ValueError):
        conns = DOWNLOAD_CONNS
    if conns <= 0:
        conns = DOWNLOAD_CONNS
    if conns > MAX_CONNS:
        if log:
            log(f"⚠️ 请求 {conns} 个连接，超过上限 {MAX_CONNS}，已截断")
        conns = MAX_CONNS
    if conns > 32 and log:
        log(f"⚠️ {conns} 个并发连接对 mit.edu / angio.net 这类公共服务器属于"
            f"高压访问，很可能被限流、超时甚至封 IP。请确认对方允许后再用。")
    return conns


def download_pi_parallel(count: int, progress=None, log=None, conns: int = DOWNLOAD_CONNS) -> bytes:
    """按优先级依次尝试多个数据源，任一成功即返回。"""
    conns = _clamp_conns(conns, log)
    errors = []
    for url, limit, contiguous in PI_SOURCES:
        if count > limit:
            if log:
                log(f"跳过 {url}（最多提供 {limit:,} 位，少于所需的 {count:,} 位）")
            continue
        try:
            return _download_one(url, count, contiguous, progress=progress, log=log, conns=conns)
        except Exception as e:
            errors.append(f"{url} -> {e}")
            if log:
                log(f"⚠️ 数据源不可用，切换下一个：{url}（{e}）")
    raise RuntimeError("所有数据源均失败：" + " | ".join(errors))


def _clean_and_verify(raw: bytes, starts_at_zero: bool, url: str, emit) -> bytes:
    """清洗出纯数字并校验开头确实是 π，返回小数部分。"""
    digits = clean_digits(raw)
    expected = PI_PREFIX_WITH_INT if starts_at_zero else PI_PREFIX_DEC
    if not digits.startswith(expected[:16]):
        raise RuntimeError(
            f"数据校验失败：{url} 返回的开头为 {digits[:16]!r}，"
            f"期望以 {expected[:16]!r} 开头（数据可能错位）"
        )
    dec = digits[1:] if starts_at_zero else digits
    emit(f"取得 {len(dec):,} 位小数，已通过 π 前缀校验")
    return dec


def _download_one(url: str, count: int, contiguous: bool,
                  progress=None, log=None, conns: int = DOWNLOAD_CONNS) -> bytes:
    """从单个源取得 count 位小数（返回纯数字 bytes，不含整数部分的 3）。"""
    import concurrent.futures as cf

    def emit(msg):
        if log:
            log(msg)

    ctx = _http_ctx()
    count = min(count, PI_MAX_DIGITS)
    conns = _clamp_conns(conns)
    emit(f"URL: {url}")
    emit(f"并发连接数：{conns}")

    # ---- 1. 探针：判断源是否可用、是否真的支持 Range ----
    t_probe = time.time()
    probe, total_size, range_ok = _fetch_range_stream(
        url, 0, 1023, ctx, timeout=25, attempts=1, allow_full=True
    )
    if not probe:
        raise RuntimeError("探测失败：返回空数据")
    emit(f"数据源可用（探测 {time.time() - t_probe:.1f}s，"
         f"文件大小 {f'{total_size:,} 字节' if total_size else '未知'}，"
         f"支持 Range：{'是' if range_ok else '否'}）")

    # ---- 2. 源忽略 Range：只能单次完整下载 ----
    # 若强行分块，各块都会拿回「从字节 0 开始」的完整内容，拼接后互相污染。
    if not range_ok:
        emit("该源忽略 Range 请求 → 改为单次完整下载（避免分块内容重复污染）")
        if progress:
            progress(10, "整文件下载中…")
        if total_size and len(probe) >= total_size:
            raw = probe                       # 探针已把整个文件取回
        else:
            raw, _, _ = _fetch_range_stream(url, None, None, ctx,
                                            timeout=180, attempts=2, allow_full=True)
        emit(f"下载 {len(raw) / 1024 / 1024:.2f} MB 完成")
        dec = _clean_and_verify(raw, True, url, emit)
        if progress:
            progress(95, "下载完成")
        return dec[:count]

    # ---- 3. 计算需要下载的字节区间 ----
    if contiguous:
        first = PI_FIRST_DIGIT_OFFSET
        last = first + count - 1
        note = (f"连续字节流，取字节 {first}~{last}"
                f"（{(last - first + 1) / 1024 / 1024:.1f} MB）")
    else:
        # 按探针测得的「数字密度」推算，留 5% 余量
        density = max(0.1, len(clean_digits(probe)) / max(1, len(probe)))
        need = int((count + 64) / density * 1.05) + 4096
        first = 0
        last = need - 1 if not total_size else min(need, total_size) - 1
        note = (f"非连续字节流（含换行），取字节 0~{last}"
                f"（约 {(last + 1) / 1024 / 1024:.1f} MB）后清洗、按数字下标切片")

    emit(f"{conns} 连接并行下载：{note}")

    # ---- 4. 切块并行下载 ----
    # 分块数同时受两个约束：连接数（吃满并发）× 每块最小字节数（避免 HTTP 往返开销）。
    # 若只按 conns*4 切，512 连接会把 250KB 切成每块 122 字节，纯属浪费。
    want_bytes = last - first + 1
    nchunks = max(conns * 4, 16)
    by_size = max(1, want_bytes // MIN_CHUNK_BYTES)
    capped = nchunks > by_size
    if capped:
        nchunks = by_size
    span = max(1, want_bytes // nchunks)

    jobs, p, i = [], first, 0
    while p <= last:
        q = min(p + span - 1, last)
        jobs.append((i, p, q))
        p = q + 1
        i += 1
    real_conns = min(conns, len(jobs))
    if capped:
        emit(f"数据仅 {want_bytes / 1024 / 1024:.2f} MB，按每块至少 "
             f"{MIN_CHUNK_BYTES // 1024} KB 切分（否则 HTTP 往返开销会吃掉全部收益）")
    emit(f"实际分块 {len(jobs)} 个，并发连接 {real_conns} 条，"
         f"每块约 {span / 1024:.0f} KB")

    results = [None] * len(jobs)
    done = [0]
    lock = threading.Lock()
    t0 = time.time()

    def fetch(job):
        idx, a, b = job
        data, _, _ = _fetch_range_stream(url, a, b, ctx, timeout=90, attempts=2)
        with lock:
            done[0] += len(data)
            if progress:
                el = max(0.001, time.time() - t0)
                progress(
                    min(5 + int(done[0] / max(1, want_bytes) * 85), 92),
                    f"已下载 {done[0] / 1024 / 1024:.1f}/{want_bytes / 1024 / 1024:.1f} MB"
                    f"（{done[0] / el / 1024 / 1024:.2f} MB/s）",
                )
        return idx, data

    with cf.ThreadPoolExecutor(max_workers=real_conns) as ex:
        for idx, data in ex.map(fetch, jobs):
            results[idx] = data

    raw = b"".join(r for r in results if r)
    dt = time.time() - t0
    emit(f"下载 {len(raw) / 1024 / 1024:.2f} MB 完成，耗时 {dt:.1f}s"
         f"（{len(raw) / 1024 / 1024 / max(0.001, dt):.2f} MB/s）")

    # ---- 5. 清洗 + 位置校验 ----
    # 清洗是按字节的，逐块清洗再拼接与整体清洗等价，故直接拼原始字节。
    dec = _clean_and_verify(raw, first == 0, url, emit)
    if len(dec) < count:
        emit(f"提示：该源仅取得 {len(dec):,} 位（请求 {count:,} 位）")
    if progress:
        progress(95, "下载完成")
    return dec[:count]


def clean_digits(raw: bytes) -> bytes:
    """C 速度剔除所有非 0-9 字节（比 re.sub 快约 100 倍）。"""
    delete = bytes(b for b in range(256) if not (48 <= b <= 57))
    return raw.translate(None, delete)


# ============================================================================
# 离线数据包：分块压缩，按需随机读取
# ============================================================================
# 为什么必须分块：10 亿位没法一次性载入（要 1 GB 内存）。
# 做成「每 100 万位一个独立 zlib 块 + 偏移表」后，查第 k 块只解压那一块
# （约 0.47 MB），内存占用与总位数无关。
#
# 包格式：
#   magic    4 字节   b"PI1B"
#   version  uint32
#   chunk    uint32   每块位数
#   nchunks  uint32   块数
#   offsets  uint64 × nchunks
#   data     nchunks 个 zlib 块依次排列
PACK_MAGIC = b"PI1B"
PACK_NAMES = ["pi_1b.pack", "pi_offline.pack"]
PACK_CACHE_CHUNKS = 6           # 内存里最多留几块解压结果
# 并行搜索的线程数。zlib 解压会释放 GIL，加上大量时间在等磁盘 I/O，
# 所以即使 4 核也能用 8 线程（实测 1 线程 5.47s → 8 线程 1.44s，3.8x）。
PACK_SEARCH_WORKERS = 8


def find_pack(log=None):
    """在若干可能位置找离线数据包。找不到返回 None（程序照常工作）。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    except Exception:
        exe_dir = base
    dirs, seen = [], set()
    for d in (exe_dir, base, os.path.dirname(base), os.path.dirname(exe_dir)):
        if d and d not in seen:
            seen.add(d)
            dirs.append(d)
    for d in dirs:
        for n in PACK_NAMES:
            p = os.path.join(d, n)
            try:
                if os.path.exists(p) and os.path.getsize(p) > 64:
                    if log:
                        log(f"发现离线数据包：{p}（{os.path.getsize(p) / 2 ** 20:.0f} MB）")
                    return p
            except Exception:
                pass
    return None


class OfflinePack:
    """分块压缩的圆周率数据包，支持按需随机读取与后台预取。

    线程安全：读取时各自开文件句柄，缓存用锁保护。
    """

    def __init__(self, path):
        import struct
        self.path = path
        self.size = os.path.getsize(path)
        with open(path, "rb") as f:
            if f.read(4) != PACK_MAGIC:
                raise RuntimeError(f"不是有效的离线包：{path}")
            ver, self.chunk, self.nchunks, _res = struct.unpack("<IIII", f.read(16))
            if ver != 1:
                raise RuntimeError(f"离线包版本不支持：{ver}")
            self.offsets = struct.unpack(f"<{self.nchunks}Q", f.read(8 * self.nchunks))
        self.digits = self.chunk * self.nchunks
        self._cache = {}
        self._order = []
        self.cache_limit = PACK_CACHE_CHUNKS
        self._lock = threading.Lock()
        self._disk_reads = 0         # 真实读盘次数（诊断缓存抖动用）
        self._bytes_read = 0
        self._inflight = {}          # k -> Event，防止两个线程重复解压同一块
        self._prefetch = None
        self._prefetch_target = None
        self._stop = False

    # ---------- 基本读取 ----------
    def get(self, k):
        """取第 k 块（chunk 位）。带 LRU 缓存与「在途去重」。

        在途去重是必需的：预取线程和搜索线程可能同时要同一块，
        没有去重的话两边各解压一遍，实测反而比不开预取更慢。
        """
        if k < 0 or k >= self.nchunks:
            raise IndexError(k)
        with self._lock:
            hit = self._cache.get(k)
            if hit is not None:
                return hit
            ev = self._inflight.get(k)
            if ev is None:
                ev = threading.Event()
                self._inflight[k] = ev
                owner = True
            else:
                owner = False

        if not owner:
            ev.wait(timeout=15)                 # 等别人解压完
            with self._lock:
                hit = self._cache.get(k)
            if hit is not None:
                return hit
            # 对方失败了，自己来
            with self._lock:
                self._inflight[k] = threading.Event()
                ev = self._inflight[k]

        try:
            a = self.offsets[k]
            b = self.offsets[k + 1] if k + 1 < self.nchunks else self.size
            import zlib
            with open(self.path, "rb") as f:    # 独立句柄 → 多线程安全
                f.seek(a)
                raw = f.read(b - a)
            with self._lock:
                self._disk_reads += 1        # 统计真实读盘次数，用于诊断缓存抖动
                self._bytes_read += len(raw)
            data = zlib.decompress(raw)
            with self._lock:
                self._cache[k] = data
                self._order.append(k)
                while len(self._order) > self.cache_limit:
                    old = self._order.pop(0)
                    if old != k:
                        self._cache.pop(old, None)
            return data
        finally:
            with self._lock:
                e = self._inflight.pop(k, None)
            if e is not None:
                e.set()

    def slice(self, start, end):
        """取 [start, end) 的数字。跨块自动拼接。"""
        out = bytearray()
        k = start // self.chunk
        while len(out) < end - start and k < self.nchunks:
            base = k * self.chunk
            blk = self.get(k)
            a = max(0, start - base)
            b = min(len(blk), end - base)
            out += blk[a:b]
            k += 1
        return bytes(out[:end - start])

    # ---------- 后台预取（「默默解压」） ----------
    def start_prefetch(self, from_chunk):
        """后台线程预先解压「下一块」。

        注意必须**只领先一块**：早期版本让预取线程一路往下解压，
        结果把缓存里搜索线程正要用到的块挤掉了，反而更慢
        （实测 1.27 亿位：关闭预取 2.36s，开启后 2.88s）。
        现在由搜索线程设置 _prefetch_target，预取线程只盯这一块。
        """
        self.stop_prefetch()
        self._stop = False
        self._prefetch_target = from_chunk + 1

        def worker():
            while not self._stop:
                k = self._prefetch_target
                if k is None or k >= self.nchunks:
                    time.sleep(0.005)
                    continue
                if k not in self._cache:
                    try:
                        self.get(k)
                    except Exception:
                        return
                time.sleep(0.002)

        self._prefetch = threading.Thread(target=worker, daemon=True)
        self._prefetch.start()

    def stop_prefetch(self):
        self._stop = True
        t = self._prefetch
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._prefetch = None

    def _scan_chunk(self, k, pats, overlap):
        """在线程池里搜索单块，返回 {key: 块内偏移}。

        块尾要拼上下一块的开头 overlap 个字符，否则跨块的匹配会漏掉。
        """
        blk = self.get(k)
        if k + 1 < self.nchunks:
            blk = blk + self.get(k + 1)[:overlap]
        out = {}
        for key, p in pats:
            i = blk.find(p)
            if i >= 0:
                out[key] = i
        return out

    def find_many(self, patterns, start_digit=0, on_step=None,
                  should_stop=None, stop_on=None, prefetch=True, workers=0):
        """搜索多个模式，返回 {key: 全局下标 或 -1}。

        workers > 1 时**并行**解压+搜索多块（zlib 会释放 GIL，多线程有真实收益）。
        但结果一律按「块序号」顺序消费 —— 因为要找的是首次出现，
        谁先算完不能算数。命中 stop_on 后立刻取消尚未开始的任务。

        进度回调 on_step(scanned, total, found_count) 每若干块一次。
        """
        todo = dict(patterns)
        found = {k: -1 for k, _ in patterns}
        overlap = max((len(p) for _, p in patterns), default=1) - 1
        k0 = max(0, start_digit // self.chunk)

        if workers is None:
            workers = 0
        if workers <= 0:
            workers = min(PACK_SEARCH_WORKERS, os.cpu_count() or 4)

        # ---- 串行路径（workers<=1，也用于对照测试）----
        if workers <= 1:
            if prefetch:
                self.start_prefetch(k0)
            try:
                for k in range(k0, self.nchunks):
                    if should_stop and should_stop():
                        break
                    self._prefetch_target = k + 1
                    res = self._scan_chunk(k, list(todo.items()), overlap)
                    base = k * self.chunk
                    for key, off in res.items():
                        pos = base + off
                        if pos >= start_digit and found.get(key, -1) < 0:
                            found[key] = pos
                            todo.pop(key, None)
                    if not todo:
                        break
                    if stop_on is not None and found.get(stop_on, -1) >= 0:
                        break
                    if on_step and (k - k0) % 25 == 0:
                        on_step(min(base + self.chunk, self.digits), self.digits,
                                sum(1 for v in found.values() if v >= 0))
                return found
            finally:
                if prefetch:
                    self.stop_prefetch()

        # ---- 并行路径 ----
        import collections
        import concurrent.futures as cf

        WINDOW = max(2, workers * 3)      # 在途任务上限，兼顾内存与吞吐
        # 缓存必须容得下整个在途窗口，否则刚解压好的块立刻被挤掉、
        # 又要重解一遍（实测那样 8 线程只有 1.6x，白加线程）。
        old_limit = self.cache_limit
        self.cache_limit = max(old_limit, WINDOW + 8)
        pending = collections.deque()
        nxt = k0

        def fill():
            nonlocal nxt
            while nxt < self.nchunks and len(pending) < WINDOW:
                pending.append((nxt, ex.submit(self._scan_chunk,
                                               nxt, list(todo.items()), overlap)))
                nxt += 1

        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            fill()
            while pending:
                if should_stop and should_stop():
                    break
                k, fut = pending.popleft()
                base = k * self.chunk
                try:
                    res = fut.result()
                except Exception:
                    res = {}
                for key, off in res.items():
                    pos = base + off
                    if pos >= start_digit and found.get(key, -1) < 0:
                        found[key] = pos
                        todo.pop(key, None)
                if on_step and (k - k0) % 25 == 0:
                    on_step(min(base + self.chunk, self.digits), self.digits,
                            sum(1 for v in found.values() if v >= 0))
                if stop_on is not None and found.get(stop_on, -1) >= 0:
                    break
                if not todo:
                    break
                fill()
            for _k, f in pending:         # 收工，别再算了
                f.cancel()
        self.cache_limit = old_limit
        return found

    # ---------- 逐块搜索 ----------
    def find(self, pattern: bytes, start_digit: int = 0,
             on_step=None, should_stop=None, prefetch=True):
        """从 start_digit 起按块查找，返回全局下标或 -1。

        on_step(done_digits, total_digits) 每块回调一次，用于进度显示。
        should_stop() 返回 True 时提前结束（用户点了停止）。
        跨块的匹配用「块尾多取 len(pattern)-1 位」处理。
        """
        pat = pattern
        overlap = len(pat) - 1
        k0 = max(0, start_digit // self.chunk)
        # 从 start_digit 所在的块开始；若起点不在块首，整块一起搜再过滤
        if prefetch:
            self.start_prefetch(k0)
        try:
            for k in range(k0, self.nchunks):
                if should_stop and should_stop():
                    return -1
                base = k * self.chunk
                blk = self.get(k)
                if k + 1 < self.nchunks:
                    nxt = self.get(k + 1)
                    blk = blk + nxt[:overlap]        # 拼上下一块开头，处理跨块
                i = blk.find(pat)
                if i >= 0:
                    pos = base + i
                    if pos >= start_digit:
                        return pos
                if on_step and (k - k0) % 20 == 0:
                    on_step(min(base + self.chunk, self.digits), self.digits)
            return -1
        finally:
            if prefetch:
                self.stop_prefetch()


_pack_singleton = {"path": None, "pack": None}


def open_pack(log=None):
    """打开离线包（单例）。返回 OfflinePack 或 None。"""
    p = find_pack(log=log)
    if not p:
        return None
    if _pack_singleton["path"] == p and _pack_singleton["pack"] is not None:
        return _pack_singleton["pack"]
    try:
        pk = OfflinePack(p)
    except Exception as e:
        if log:
            log(f"⚠️ 离线包打不开（{e}），忽略")
        return None
    if log:
        log(f"离线包就绪：{pk.nchunks} 块 × {pk.chunk:,} 位 = {pk.digits:,} 位")
    _pack_singleton["path"] = p
    _pack_singleton["pack"] = pk
    return pk


# ============================================================================
# 自适应调度
# ============================================================================
def estimate_compute_seconds(count: int) -> float:
    rate = COMPUTE_RATE[HAS_GMPY2]
    # 实测约 O(n^1.35)，用 10M 处标定
    base = count / rate
    return base * (1.0 + 0.35 * (count / 10_000_000) ** 0.35)


def estimate_download_seconds(count: int) -> float:
    return count / DOWNLOAD_RATE


def acquire_digits(count: int, method: str = "auto", progress=None, log=None,
                   workers: int = 0, conns: int = DOWNLOAD_CONNS) -> bytes:
    """获取 count 位 π 小数（bytes）。method: auto / compute / download

    workers: 计算进程数，0=自动，最高 MAX_WORKERS(512)
    conns  : 下载并发连接数，最高 MAX_CONNS(512)
    """
    def emit(msg):
        if log:
            log(msg)

    # ---- 1. 磁盘缓存 ----
    cached = load_cache(count)
    if cached is not None:
        emit(f"命中磁盘缓存：{cache_path(count)}（{len(cached):,} 位，0 秒返回）")
        if progress:
            progress(95, "已从缓存载入")
        return cached

    # ---- 2. 内置 π 数据：无需联网、无需计算，直接切片 ----
    seed_name, seed = load_seed(log)
    if seed is not None and len(seed) >= count:
        emit(f"使用内置 π 数据（{seed_name}）直接切片，无需联网、无需计算")
        data = seed[:count]
        if progress:
            progress(95, "已从内置数据载入")
        save_cache(count, data)
        return data
    if seed is not None:
        emit(f"内置数据只有 {len(seed):,} 位，不足所需的 {count:,} 位，需要另行获取")

    # ---- 3. 离线模式：绝不联网 ----
    if OFFLINE:
        if method == "download":
            emit("⚠️ 当前为纯离线模式，已忽略「网络下载」请求，改用本地计算")
        method = "compute"
        emit("纯离线模式：不会发起任何网络请求")
    elif method == "auto":
        tc = estimate_compute_seconds(count) if HAS_GMPY2 else float("inf")
        td = estimate_download_seconds(count)
        method = "download" if td < tc else "compute"
        emit(f"自动选择：本地计算预估 {tc:.1f}s，网络下载预估 {td:.1f}s → 采用"
             f"{'网络下载' if method == 'download' else '本地计算'}")
    else:
        emit(f"指定模式：{'网络下载' if method == 'download' else '本地计算'}")

    data = None
    if method == "compute":
        try:
            if HAS_GMPY2:
                data = compute_pi_gmpy2(count, workers=workers, progress=progress, log=log)
            else:
                data = compute_pi_decimal(count, progress=progress, log=log)
        except Exception as e:
            if OFFLINE:
                emit(f"本地计算失败：{e}")
                data = None
            else:
                emit(f"本地计算失败（{e}），改用网络下载…")
                data = None
    else:
        try:
            data = download_pi_parallel(count, progress=progress, log=log, conns=conns)
        except Exception as e:
            emit(f"网络下载失败（{e}），改用本地计算…")
            data = None

    if data is None:
        if OFFLINE:
            raise RuntimeError(
                f"纯离线模式下无法取得 {count:,} 位圆周率数据。\n"
                f"  · 内置数据上限 {len(seed) if seed else 0:,} 位\n"
                f"  · 本地计算{'不可用（缺少 gmpy2）' if not HAS_GMPY2 else '失败'}\n"
                f"请改用较小的位数档位。"
            )
        raise RuntimeError("本地计算与网络下载均失败，无法获取圆周率数据")

    if len(data) < count:
        emit(f"警告：仅取得 {len(data):,} 位（请求 {count:,} 位），搜索结果可能不完整")

    save_cache(count, data)
    emit(f"已写入缓存：{cache_path(count)}")
    return data


# ============================================================================
# 搜索
# ============================================================================
def _find_chunk_worker(args):
    data, pattern, base = args
    i = data.find(pattern)
    return None if i < 0 else base + i


def search_parallel(patterns, data: bytes, workers: int = 0, progress=None, log=None):
    """多进程分块并行搜索，命中即提前退出（保证返回最小下标）。

    只有数据量 >= PARALLEL_SEARCH_THRESHOLD 时才会走到这里；
    实测 bytes.find 在千万位上仅需约 12 毫秒，所以这一路径是为
    亿级及以上的超大数据准备的。
    """
    workers = workers or min(4, os.cpu_count() or 1)

    def emit(msg):
        if log:
            log(msg)

    emit(f"并行搜索：{workers} 进程，数据 {len(data) / 1024 / 1024:.0f} MB")
    results = {}

    for name, pat in patterns:
        p = pat.encode("ascii")
        overlap = len(p) - 1
        per = max(1, (len(data) + workers * 8 - 1) // (workers * 8))
        chunks = []
        pos = 0
        while pos < len(data):
            end = min(len(data), pos + per)
            tail = min(len(data), end + overlap)  # 重叠区，避免漏掉跨块匹配
            chunks.append((data[pos:tail], p, pos))
            pos = end

        # 每个模式用独立的池：命中后可安全地 terminate 提前退出
        pool = mp.Pool(workers)
        found = None
        try:
            for r in pool.imap(_find_chunk_worker, chunks):
                if r is not None:
                    found = r          # imap 保持顺序，首个命中即最小下标
                    emit(f"  {name} {pat}：在第 {r + 1:,} 位命中，提前退出")
                    break
        finally:
            pool.terminate()
            pool.join()
        # 统一以「模式字符串」为键，未找到返回 -1（与 bytes.find 约定一致）
        results[pat] = -1 if found is None else found

    return results


def search_sequential(patterns, data: bytes, log=None):
    """返回 {模式字符串: 下标}，未找到为 -1（与 bytes.find 约定一致）。"""
    return {pat: data.find(pat.encode("ascii")) for _label, pat in patterns}


def build_patterns(birthday: str):
    """birthday: YYYYMMDD。返回 [(标签, 模式), ...]"""
    yyyy, mm, dd = birthday[0:4], birthday[4:6], birthday[6:8]
    yy = yyyy[2:]
    pats = [
        ("完整生日 YYYYMMDD", birthday),
        ("年月日 YYMMDD", yy + mm + dd),
        ("月日年 MMDDYY", mm + dd + yy),
        ("日月年 DDMMYY", dd + mm + yy),
        ("月日 MMDD", mm + dd),
    ]
    # 去重
    seen, out = set(), []
    for label, pat in pats:
        if pat not in seen:
            seen.add(pat)
            out.append((label, pat))
    return out


def format_position(idx: int) -> str:
    return f"第 {idx + 1:,} 位" if idx is not None and idx >= 0 else "未出现"


def context_snippet(data: bytes, idx: int, length: int, span: int = 30) -> str:
    """渲染命中位置的上文【命中】下文，每 10 位一组便于阅读。"""
    if idx is None or idx < 0:
        return ""
    a = max(0, idx - span)
    b = min(len(data), idx + length + span)
    left = data[a:idx].decode("ascii", "ignore")
    mid = data[idx:idx + length].decode("ascii", "ignore")
    right = data[idx + length:b].decode("ascii", "ignore")

    def group(s: str) -> str:
        return " ".join(s[i:i + 10] for i in range(0, len(s), 10))

    return f"{group(left)} 【{mid}】{group(right)}"


# ============================================================================
# 核心流程
# ============================================================================
def parse_birthday(text: str):
    import re
    m = re.search(r"(\d{4})[-/.]?(\d{1,2})[-/.]?(\d{1,2})", text.strip())
    if not m:
        raise ValueError("请输入有效生日，例如 1990-01-01 或 19900101")
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        raise ValueError("月份或日期不合法")
    return f"{y}{mo:02d}{d:02d}"


def run_search(birthday: str, count: int, method: str, progress=None, log=None,
               workers: int = 0, conns: int = DOWNLOAD_CONNS):
    """执行完整查询，返回 (结果文本, 明细 dict)

    birthday 可以是任意格式（1990-01-01 / 19900101 / 1990/1/1），内部统一解析。
    """
    birthday = parse_birthday(birthday)
    t_start = time.time()
    data = acquire_digits(count, method=method, progress=progress, log=log,
                          workers=workers, conns=conns)
    actual = len(data)

    patterns = build_patterns(birthday)
    t0 = time.time()
    if actual >= PARALLEL_SEARCH_THRESHOLD:
        results = search_parallel(patterns, data, progress=progress, log=log)
    else:
        results = search_sequential(patterns, data, log=log)
    t_search = time.time() - t0

    if log:
        log(f"搜索完成：{len(patterns)} 个模式，耗时 {t_search * 1000:.1f} 毫秒")

    lines = []
    yyyy, mm, dd = birthday[0:4], birthday[4:6], birthday[6:8]
    lines.append(f"🎂 生日：{yyyy}-{mm}-{dd}")
    lines.append(f"📐 搜索范围：圆周率小数前 {actual:,} 位")
    lines.append(f"⏱️  总耗时：{time.time() - t_start:.2f} 秒（其中搜索 {t_search * 1000:.1f} 毫秒）")
    lines.append("")
    lines.append("─" * 46)

    for label, pat in patterns:
        idx = results.get(pat)
        lines.append(f"{label:<18} {pat:<10} → {format_position(idx)}")
        snip = context_snippet(data, idx, len(pat))
        if snip:
            lines.append(f"    {snip}")
        lines.append("")

    best = results.get(birthday)
    if best is not None and best >= 0:
        lines.append("─" * 46)
        lines.append(f"✅ 你的完整生日 {birthday} 首次出现在圆周率第 {best + 1:,} 位（小数位，从 1 起算）")
    else:
        lines.append("─" * 46)
        lines.append(f"😔 在前 {actual:,} 位中没有找到完整生日 {birthday}。")
        lines.append("   8 位数字在前 1 亿位中出现的概率约 63%，可尝试更大的位数档位。")

    return "\n".join(lines), results


# ============================================================================
# 控制台编码（GBK 控制台下打印 emoji 会崩溃）
# ============================================================================
def setup_console_utf8() -> None:
    """让 CLI 在 Windows GBK 控制台下也能正常输出中文与 emoji。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ============================================================================
# 命令行模式
# ============================================================================
def run_cli(argv):
    import argparse

    setup_console_utf8()

    ap = argparse.ArgumentParser(
        description="生日在圆周率第几位（并行加速版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"并行度上限：计算进程 {MAX_WORKERS}，下载连接 {MAX_CONNS}\n"
               f"提示：CPU 密集型计算开得比核心数多通常更慢；高并发下载易被限流。",
    )
    ap.add_argument("--birthday", "-b", required=True, help="生日，如 1990-01-01")
    ap.add_argument("--digits", "-d", type=int, default=1_000_000, help="搜索位数")
    ap.add_argument("--method", "-m", choices=["auto", "compute", "download"], default="auto")
    ap.add_argument("--workers", "-w", type=int, default=0,
                    help=f"计算进程数，0=自动（min(8,核数)），最高 {MAX_WORKERS}")
    ap.add_argument("--conns", "-c", type=int, default=DOWNLOAD_CONNS,
                    help=f"下载并发连接数，默认 {DOWNLOAD_CONNS}，最高 {MAX_CONNS}")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：绝不联网，只用内置数据或本地计算")
    ap.add_argument("--no-cache", action="store_true", help="忽略磁盘缓存")
    args = ap.parse_args(argv)

    if args.offline:
        set_offline(True)

    if args.no_cache:
        p = cache_path(args.digits)
        if os.path.exists(p):
            os.remove(p)

    birthday = parse_birthday(args.birthday)
    seed_name, seed = load_seed()
    print(f"生日解析为：{birthday}")
    print(f"gmpy2 可用：{HAS_GMPY2}     CPU 核数：{os.cpu_count()}")
    print(f"联网状态：{'🚫 纯离线（绝不联网）' if OFFLINE else '允许联网'}")
    print(f"内置 π 数据：{seed_name + f'（{len(seed):,} 位）' if seed is not None else '无'}")
    print(f"计算进程数：{'自动' if args.workers <= 0 else args.workers}    "
          f"下载连接数：{args.conns}")
    print("=" * 58)

    def log(msg):
        print(f"  · {msg}", flush=True)

    text, _ = run_search(birthday, args.digits, args.method, log=log,
                         workers=args.workers, conns=args.conns)
    print("=" * 58)
    print(text)
    return 0


# ============================================================================
# GUI
# ============================================================================
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext

    root = tk.Tk()
    root.title("🎂 生日在圆周率第几位？ — 并行加速版")
    root.geometry("820x700")
    root.minsize(720, 600)

    msg_q: "queue.Queue" = queue.Queue()
    state = {"running": False, "thread": None}

    pad = {"padx": 12, "pady": 6}
    outer = ttk.Frame(root)
    outer.pack(fill=tk.BOTH, expand=True, **pad)

    ttk.Label(outer, text="输入生日，查看它在圆周率小数中首次出现的位置",
              font=("Microsoft YaHei UI", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))

    # --- 输入区 ---
    row = ttk.Frame(outer)
    row.pack(fill=tk.X, pady=3)
    ttk.Label(row, text="生日：", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
    bd_var = tk.StringVar(value="1990-01-01")
    ent = ttk.Entry(row, textvariable=bd_var, width=18, font=("Consolas", 13))
    ent.pack(side=tk.LEFT, padx=(0, 14))

    ttk.Label(row, text="搜索位数：", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
    tier_var = tk.StringVar(value=DIGIT_TIERS[DEFAULT_TIER_INDEX][0])
    ttk.Combobox(row, textvariable=tier_var, state="readonly", width=26,
                 values=[t[0] for t in DIGIT_TIERS]).pack(side=tk.LEFT, padx=(0, 14))

    meth_var = tk.StringVar(value="本地计算")
    ttk.Label(row, text="模式：", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
    meth_combo = ttk.Combobox(row, textvariable=meth_var, state="readonly", width=10,
                              values=["自动", "本地计算"])
    meth_combo.pack(side=tk.LEFT)

    # --- 离线开关 ---
    row_off = ttk.Frame(outer)
    row_off.pack(fill=tk.X, pady=(6, 0))
    offline_var = tk.BooleanVar(value=True)
    off_chk = ttk.Checkbutton(row_off, text="🚫 纯离线模式（绝不联网）", variable=offline_var)
    off_chk.pack(side=tk.LEFT)
    seed_name, seed = load_seed()
    seed_text = (f"已内置 {seed_name}（{len(seed):,} 位），开启后无需联网、无需计算即可出结果"
                 if seed is not None else "⚠️ 未找到内置数据，离线时只能靠本地计算")
    off_hint = ttk.Label(row_off, text="　" + seed_text, foreground="#666")
    off_hint.pack(side=tk.LEFT)

    # --- 并行度 ---
    rowp = ttk.Frame(outer)
    rowp.pack(fill=tk.X, pady=3)

    ttk.Label(rowp, text="计算进程数：", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
    workers_var = tk.StringVar(value="0")
    ttk.Spinbox(rowp, textvariable=workers_var, width=6, from_=0, to=MAX_WORKERS,
                font=("Consolas", 11)).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(rowp, text=f"（0=自动，最高 {MAX_WORKERS}）",
              foreground="#888").pack(side=tk.LEFT, padx=(0, 16))

    ttk.Label(rowp, text="下载连接数：", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
    conns_var = tk.StringVar(value=str(DOWNLOAD_CONNS))
    conns_spin = ttk.Spinbox(rowp, textvariable=conns_var, width=6, from_=1, to=MAX_CONNS,
                             font=("Consolas", 11))
    conns_spin.pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(rowp, text=f"（最高 {MAX_CONNS}）", foreground="#888").pack(side=tk.LEFT)

    warn_lbl = ttk.Label(outer,
                         text="⚠️ 计算进程数超过 CPU 核心数、或下载连接数开得过大，通常不会更快，"
                              "反而可能更慢或被服务器限流。",
                         foreground="#b06000", wraplength=740, justify=tk.LEFT)
    warn_lbl.pack(anchor=tk.W, pady=(2, 0))

    row2 = ttk.Frame(outer)
    row2.pack(fill=tk.X, pady=(8, 4))
    go_btn = ttk.Button(row2, text="🔍  开始查找")
    go_btn.pack(side=tk.LEFT)
    clear_btn = ttk.Button(row2, text="清空缓存")
    clear_btn.pack(side=tk.LEFT, padx=8)

    info = ttk.Label(row2, text=f"gmpy2: {'✅ 已启用' if HAS_GMPY2 else '❌ 未安装'}   "
                                f"CPU: {os.cpu_count()} 核   搜索: bytes.find (C 实现)",
                     foreground="#666")
    info.pack(side=tk.LEFT, padx=10)

    # --- 进度 ---
    pvar = tk.StringVar(value="就绪")
    ttk.Label(outer, textvariable=pvar, foreground="#444").pack(anchor=tk.W, pady=(6, 2))
    pbar = ttk.Progressbar(outer, mode="determinate", maximum=100)
    pbar.pack(fill=tk.X, pady=(0, 8))

    # --- 日志 ---
    nb = ttk.Notebook(outer)
    nb.pack(fill=tk.BOTH, expand=True)

    res_frame = ttk.Frame(nb)
    nb.add(res_frame, text=" 结果 ")
    result = scrolledtext.ScrolledText(res_frame, wrap=tk.WORD,
                                       font=("Consolas", 11), height=14)
    result.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
    result.insert("1.0", "结果将显示在这里…")
    result.config(state=tk.DISABLED)

    log_frame = ttk.Frame(nb)
    nb.add(log_frame, text=" 运行日志（耗时明细） ")
    logbox = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD,
                                       font=("Consolas", 9), height=14,
                                       background="#1e1e1e", foreground="#d4d4d4")
    logbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
    logbox.config(state=tk.DISABLED)

    def ui_log(msg):
        logbox.config(state=tk.NORMAL)
        logbox.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        logbox.see(tk.END)
        logbox.config(state=tk.DISABLED)

    def ui_progress(pct, text):
        pbar["value"] = pct
        pvar.set(text)

    def ui_result(text):
        result.config(state=tk.NORMAL)
        result.delete("1.0", tk.END)
        result.insert("1.0", text)
        result.config(state=tk.DISABLED)
        nb.select(0)

    def poll():
        try:
            while True:
                kind, payload = msg_q.get_nowait()
                if kind == "log":
                    ui_log(payload)
                elif kind == "progress":
                    ui_progress(*payload)
                elif kind == "result":
                    ui_result(payload)
                elif kind == "error":
                    messagebox.showerror("出错了", payload)
                    ui_log(f"错误：{payload}")
                elif kind == "done":
                    state["running"] = False
                    go_btn.config(state=tk.NORMAL)
                    pbar["value"] = 100
        except queue.Empty:
            pass
        root.after(120, poll)

    def worker(birthday, count, method, workers, conns):
        try:
            text, _ = run_search(
                birthday, count, method,
                progress=lambda p, t: msg_q.put(("progress", (p, t))),
                log=lambda m: msg_q.put(("log", m)),
                workers=workers, conns=conns,
            )
            msg_q.put(("result", text))
        except Exception as e:
            import traceback
            msg_q.put(("log", traceback.format_exc()))
            msg_q.put(("error", str(e)))
        finally:
            msg_q.put(("done", None))

    def _read_int(var, default, lo, hi, name):
        """读取并校验并行度输入，越界时截断并提示。"""
        raw = (var.get() or "").strip()
        try:
            v = int(raw)
        except ValueError:
            raise ValueError(f"{name}必须是整数，当前输入为「{raw}」")
        if v < lo:
            v = lo
        if v > hi:
            messagebox.showwarning("超出上限", f"{name}最高 {hi}，已按 {hi} 处理。")
            v = hi
        return v

    def on_toggle_offline():
        """离线开关联动：关闭联网时禁用「网络下载」及连接数设置。"""
        off = bool(offline_var.get())
        set_offline(off)
        if off:
            meth_combo.config(values=["本地计算"])
            if meth_var.get() not in ("本地计算",):
                meth_var.set("本地计算")
            conns_spin.config(state=tk.DISABLED)
            off_hint.config(text="　已开启：不会发起任何网络请求（可用内置数据或本地计算）",
                            foreground="#1a7f37")
            warn_lbl.config(text="⚠️ 计算进程数超过 CPU 核心数通常不会更快，反而可能更慢。")
        else:
            meth_combo.config(values=["自动", "本地计算", "网络下载"])
            conns_spin.config(state=tk.NORMAL)
            off_hint.config(text="　已允许联网：数据源不可用时会自动回退到本地计算",
                            foreground="#b06000")
            warn_lbl.config(text="⚠️ 计算进程数超过 CPU 核心数、或下载连接数开得过大，"
                                 "通常不会更快，反而可能更慢或被服务器限流。")

    def on_go():
        if state["running"]:
            return
        try:
            birthday = parse_birthday(bd_var.get())
            workers = _read_int(workers_var, 0, 0, MAX_WORKERS, "计算进程数")
            conns = _read_int(conns_var, DOWNLOAD_CONNS, 1, MAX_CONNS, "下载连接数")
        except ValueError as e:
            messagebox.showwarning("输入有误", str(e))
            return
        count = dict((t[0], t[1]) for t in DIGIT_TIERS)[tier_var.get()]
        set_offline(offline_var.get())
        method = {"自动": "auto", "本地计算": "compute", "网络下载": "download"}[meth_var.get()]
        if OFFLINE and method == "download":
            method = "compute"

        state["running"] = True
        go_btn.config(state=tk.DISABLED)
        pbar["value"] = 0
        pvar.set("准备中…")
        ui_log(f"=== 开始：生日 {birthday}，目标 {count:,} 位，模式 {method}，"
               f"进程 {'自动' if workers <= 0 else workers}，连接 {conns}，"
               f"{'纯离线' if OFFLINE else '允许联网'} ===")
        ui_result("正在获取圆周率数据，请稍候…\n\n"
                  "（内置数据可直接秒出；更大位数会本地计算，之后走磁盘缓存）")

        th = threading.Thread(target=worker,
                              args=(birthday, count, method, workers, conns), daemon=True)
        state["thread"] = th
        th.start()

    def on_clear_cache():
        d = cache_dir()
        n = 0
        for f in os.listdir(d):
            if f.startswith("pi_d") and f.endswith(".bin"):
                try:
                    os.remove(os.path.join(d, f))
                    n += 1
                except Exception:
                    pass
        ui_log(f"已清空 {n} 个缓存文件（{d}）")
        messagebox.showinfo("缓存", f"已清空 {n} 个缓存文件。\n\n{d}")

    go_btn.config(command=on_go)
    clear_btn.config(command=on_clear_cache)
    off_chk.config(command=on_toggle_offline)
    ent.bind("<Return>", lambda e: on_go())
    on_toggle_offline()          # 按默认值（纯离线）初始化界面状态

    ui_log(f"程序启动。gmpy2={'可用' if HAS_GMPY2 else '不可用'}，CPU={os.cpu_count()} 核")
    ui_log(f"内置 π 数据：{seed_name + f'（{len(seed):,} 位）' if seed is not None else '未找到'}")
    ui_log(f"联网状态：{'纯离线（绝不联网）' if OFFLINE else '允许联网'}")
    ui_log(f"缓存目录：{cache_dir()}")
    ui_log("提示：内置数据已覆盖前 1000 万位，这三个档位无需联网、无需计算即可出结果。")
    root.after(120, poll)
    root.mainloop()


# ============================================================================
def main():
    mp.freeze_support()  # PyInstaller 打包后多进程必需
    argv = sys.argv[1:]
    if "--cli" in argv:
        argv = [a for a in argv if a != "--cli"]
        sys.exit(run_cli(argv))
    run_gui()


if __name__ == "__main__":
    main()
