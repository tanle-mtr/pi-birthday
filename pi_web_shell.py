#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生日在圆周率第几位？ —— 网页版 exe 外壳

用 pywebview 承载网页界面：
  · 复用 Windows 自带的 WebView2 运行时（Chromium 内核），
    所以 exe 里**不需要**打包浏览器，体积只有二十几 MB
    （对比 Electron 动辄 200 MB+）
  · 界面是 web/ 目录下的纯 HTML/CSS/JS，改样式不用重新编译

两层数据来源：
  1. 内置 1000 万位（pi_seed_10m.z）—— 秒出，用于绝大多数查询
  2. 网页里搜不到完整生日时，网页通过 js_api 回调到这里，
     用 gmpy2 多进程**继续往后算**（离线，不联网）

注意：搜索在 Python 侧完成，只把「位置 + 上下文」回传给网页。
   如果改成把上亿位数字整体传给 JS，光序列化就要几十 MB，得不偿失。
"""

from __future__ import annotations

import gc
import multiprocessing as mp
import os
import sys
import threading
import time

APP_TITLE = "生日在圆周率第几位？"

# ---------------------------------------------------------------------------
# 逐级往后的目标位数
#
# 两条硬性约束（来自用户明确要求）：
#   1. 内存优先 —— 宁可慢，也不要吃太多内存。所以深层计算固定单进程
#      （多进程时每个 worker 都要持一份大整数，峰值内存按倍数上涨）。
#   2. 完整生日没出现就继续往后算 —— 所以目标不是写死的几级，
#      而是「翻倍往上排，一直排到内存放不下的前一级」。
#
# 实测内存模型（measure_mem.py）：峰值 ≈ 20 MB + 11.7 字节/位。
#   2000 万位 ≈ 250 MB     1 亿位 ≈ 1.2 GB     5 亿位 ≈ 5.8 GB
# ---------------------------------------------------------------------------
LOW_MEMORY_WORKERS = 1          # 深层计算固定单进程
LEVEL_START = 20_000_000        # 第一级目标
LEVEL_MAX_COUNT = 12            # 最多 20M × 2^11（实际先撞内存上限）
# 用户明确要求「一直算到完整生日」，所以把安全余量放宽：
# 允许占用到可用内存的 70%（早期是 55%，但那样在内存紧张时会一级都排不出来）。
MEMORY_HEADROOM = 0.70
BUILTIN_DIGITS = 10_000_000     # 内置数据位数，低于它没有意义


def plan_targets(min_digits: int = 0):
    """按可用内存规划逐级目标。返回 (levels, avail_bytes, cap_digits)。

    min_digits：只排比它更大的目标（「继续往后算」时避免重复算已经算过的级）。
    """
    try:
        import pi_birthday as pb
        avail = pb.available_memory_bytes()
        cap = pb.max_digits_for_memory(avail, headroom=MEMORY_HEADROOM,
                                       workers=LOW_MEMORY_WORKERS)
    except Exception:
        avail, cap = None, 200_000_000

    if cap is None:                     # 拿不到内存信息就不设上限
        cap = 200_000_000

    floor = max(BUILTIN_DIGITS, min_digits)
    levels = []
    n = LEVEL_START
    while len(levels) < LEVEL_MAX_COUNT:
        if n > cap:
            break
        if n > floor:
            levels.append(n)
        n *= 2
    # 内存实在紧张时，至少争取算到 floor 之上一点点
    if not levels and cap > floor:
        levels = [cap]
    return levels, avail, cap


def _targets(min_digits: int = 0):
    """环境变量优先（便于测试），否则按内存规划。"""
    raw = os.environ.get("PIWEB_TARGETS", "").strip()
    if raw:
        out = []
        for part in raw.split(","):
            try:
                v = int(part.strip())
                if v > min_digits:
                    out.append(v)
            except ValueError:
                pass
        if out:
            return sorted(out)
    return plan_targets(min_digits)[0]


def _win_size():
    """窗口尺寸。可用环境变量覆盖：set PIWEB_W=1400 & set PIWEB_H=1000"""
    def _get(name, default):
        try:
            v = int(os.environ.get(name, ""))
            return v if v > 200 else default
        except Exception:
            return default
    return _get("PIWEB_W", 1120), _get("PIWEB_H", 880)


WIN_W, WIN_H = _win_size()


def resource_path(*parts) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def web_dir() -> str:
    return resource_path("web")


# ============================================================================
# 暴露给网页的 Python API
# ============================================================================
class Api:
    """网页通过 window.pywebview.api.* 调用这些方法。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = False
        self._thread = None
        self._state = {
            "running": False, "done": False, "stopped": False, "error": None,
            "level": 0, "levels": 0, "target": 0, "depth": 0,
            "message": "", "elapsed": 0.0,
            "can_continue": False,      # 算完了但没找到时，是否还能再往上算
            "results": {},      # pat -> {"idx": int, "ctx": str}
        }

    # ---------- 状态查询 ----------
    def deep_status(self):
        with self._lock:
            s = dict(self._state)
            s["results"] = dict(self._state["results"])
            return s

    def deep_stop(self):
        self._stop = True
        return True

    def info(self, min_digits=0):
        """环境信息，供网页展示。min_digits：只规划比它更大的级。"""
        try:
            import pi_birthday as pb
            levels, avail, cap = plan_targets(int(min_digits or 0))
            pack = pb.open_pack()
            return {
                "gmpy2": pb.HAS_GMPY2,
                "cores": os.cpu_count() or 1,
                "avail_mb": round(avail / 1048576) if avail else None,
                "targets": _targets(int(min_digits or 0)),
                "max_digits": cap,
                "workers": LOW_MEMORY_WORKERS,
                "per_digit_bytes": pb.PEAK_PER_DIGIT,
                # 离线数据包（有就能一程查到 10 亿位，且几乎不占内存）
                "pack": ({"digits": pack.digits, "chunk": pack.chunk,
                          "size_mb": round(pack.size / 2 ** 20)} if pack else None),
                "pack_workers": pb.PACK_SEARCH_WORKERS,
                "builtin_digits": BUILTIN_DIGITS,
            }
        except Exception as e:
            return {"gmpy2": False, "cores": os.cpu_count() or 1,
                    "avail_mb": None, "targets": [], "max_digits": None,
                    "workers": LOW_MEMORY_WORKERS, "pack": None, "error": str(e)}

    # ---------- 继续往后算 ----------
    def deep_search(self, patterns, targets=None, min_digits=0):
        """patterns: [[label, fmt, pat], ...]

        在后台线程里逐级计算并搜索；网页轮询 deep_status() 取进度与结果。
        min_digits：跳过已经算过的级（「继续往后算」时用）。
        """
        with self._lock:
            if self._state["running"]:
                return {"ok": False, "msg": "任务已在运行"}
            self._state.update({
                "running": True, "done": False, "stopped": False, "error": None,
                "level": 0, "target": 0, "depth": 0,
                "message": "准备中…", "elapsed": 0.0, "results": {},
                "can_continue": False,
            })
        self._stop = False
        tg = list(targets or _targets(int(min_digits or 0)))
        self._state["levels"] = len(tg)
        self._thread = threading.Thread(target=self._run, args=(patterns, tg), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _run(self, patterns, targets):
        t0 = time.time()
        try:
            import pi_birthday as pb

            if not targets:
                avail = pb.available_memory_bytes()
                self._set(running=False, done=True, error=(
                    f"可用内存不足以继续往后算（当前仅 "
                    f"{avail / 2 ** 20:.0f} MB）。\n"
                    f"本次计算按「内存优先」策略固定单进程，"
                    f"每 1000 万位约需 112 MB。\n"
                    f"请关闭一些程序后重试。"))
                return

            # 没有 gmpy2 就绝不要往前算：纯 Python decimal 慢约 1000 倍，
            # 2000 万位要算好几个小时，只会让用户以为程序卡死。
            if not pb.HAS_GMPY2:
                self._set(running=False, done=True, error=(
                    "计算模块 gmpy2 不可用，无法继续往后算。\n"
                    "（纯 Python 回退方案慢约 1000 倍，不适合上千万位的计算）\n"
                    "请使用完整的 PiBirthdayWeb.exe，或检查打包是否带上 gmpy2 元数据。"))
                return

            # 开头先报一次总体计划，让用户知道要算到哪、大概吃多少内存
            try:
                plan = "、".join(f"{t // 10_000}万" for t in targets)
                self._set(message=f"计划逐级计算：{plan} 位（单进程省内存）")
            except Exception:
                pass

            def log(msg):
                self._set(message=msg)

            # ---- 第 0 阶段：离线数据包（如果有）----
            # 用户要求：有离线资源就先按 100 万位一块、一块一块往后查，
            # 查的时候后台默默解压后面的块。
            # 查 10 亿位总共只要几十秒，比本地计算快几个数量级。
            pack = pb.open_pack(log=log)
            stage0_depth = 0
            if pack is not None:
                # 必须从 0 开始搜，而不是从内置边界（1000 万）开始。
                # 早期从 1000 万起搜，短模式（如 900101）会匹配到「更晚」的一次出现，
                # 然后覆盖掉内置里更早的正确结果 —— 实测 2,568,574 被错报成 10,007,222。
                # 包本身包含前 1000 万位，所以从 0 搜的结果就是真正的首次出现。
                start = 0
                pats = [(pat, pat.encode("ascii")) for _l, _f, pat in patterns]
                stop_on = patterns[0][2] if patterns else None
                nw = pb.PACK_SEARCH_WORKERS
                self._set(level=0, levels=len(targets) + 1, depth=pack.chunk,
                          message=f"查离线数据包（{pack.digits:,} 位）："
                                  f"{nw} 线程并行解压+搜索，按块序取结果…")

                def on_step(scanned, total, nfound, start=start):
                    self._set(depth=scanned, elapsed=time.time() - t0,
                              message=f"离线包并行搜索中（{nw} 线程）…"
                                      f"已到第 {scanned:,} 位"
                                      f"（共 {total:,} 位，命中 {nfound} 项）")

                hits = pack.find_many(pats, start_digit=start, on_step=on_step,
                                      should_stop=lambda: self._stop,
                                      stop_on=stop_on, workers=nw)
                stage0_depth = pack.digits

                if self._stop:
                    self._set(running=False, done=True, stopped=True,
                              message="已手动停止")
                    return

                res = {}
                for _label, _fmt, pat in patterns:
                    i = hits.get(pat, -1)
                    if i >= 0:
                        a = max(0, i - 30)
                        ctx = pack.slice(a, i + len(pat) + 30)
                        res[pat] = {"idx": i, "pre": i - a, "ctx": ctx.decode("ascii")}
                    else:
                        res[pat] = {"idx": -1, "pre": 0, "ctx": ""}

                with self._lock:
                    self._state["results"] = res
                    self._state["depth"] = pack.digits
                    self._state["elapsed"] = time.time() - t0

                full = patterns[0][2] if patterns else None
                if full and res.get(full, {}).get("idx", -1) >= 0:
                    self._set(running=False, done=True, can_continue=False,
                              message=f"🎉 在离线数据包第 {res[full]['idx'] + 1:,} 位"
                                      f"找到了完整生日！")
                    return
                log(f"离线包（{pack.digits:,} 位）里没有完整生日，转本地计算")

            for lvl, target in enumerate(targets, start=1):
                if self._stop:
                    self._set(running=False, done=True, stopped=True,
                              message="已手动停止")
                    return

                # 内存提示：用实测模型（20MB + 11.7 字节/位）而不是拍脑袋估计
                try:
                    mem = pb.memory_report(target, workers=LOW_MEMORY_WORKERS)
                except Exception:
                    mem = ""

                self._set(level=lvl, target=target,
                          message=f"第 {lvl}/{len(targets)} 级：正在计算前 "
                                  f"{target:,} 位圆周率（单进程省内存）· {mem}")

                def progress(pct, text, target=target):
                    self._set(depth=target,
                              elapsed=time.time() - t0,
                              message=f"计算前 {target:,} 位 · {text}")

                def log(msg, target=target):
                    self._set(message=f"计算前 {target:,} 位 · {msg}")

                # 固定单进程：内存优先。多进程时每个 worker 都要持一份大整数，
                # 峰值内存按倍数上涨，宁可慢也不要 swap。
                digits = pb.acquire_digits(target, method="compute",
                                           workers=LOW_MEMORY_WORKERS,
                                           progress=progress, log=log)
                if self._stop:
                    self._set(running=False, done=True, stopped=True,
                              message="已手动停止")
                    return

                self._set(message=f"第 {lvl}/{len(targets)} 级：正在前 "
                                  f"{len(digits):,} 位中搜索…")
                res = {}
                for label, fmt, pat in patterns:
                    i = digits.find(pat.encode("ascii"))
                    if i >= 0:
                        a = max(0, i - 30)
                        b = min(len(digits), i + len(pat) + 30)
                        # pre = 命中前有多少个字符，网页据此切分高亮
                        res[pat] = {"idx": i, "pre": i - a,
                                    "ctx": digits[a:b].decode("ascii")}
                    else:
                        res[pat] = {"idx": -1, "pre": 0, "ctx": ""}

                with self._lock:
                    self._state["results"] = res
                    self._state["depth"] = len(digits)
                    self._state["elapsed"] = time.time() - t0

                # 完整生日命中就收工
                full = patterns[0][2] if patterns else None
                if full and res.get(full, {}).get("idx", -1) >= 0:
                    self._set(running=False, done=True, can_continue=False,
                              message=f"🎉 在第 {res[full]['idx'] + 1:,} 位找到了完整生日！")
                    return

                # 这一级用完就把结果放掉，别把上一级的数据一直留在内存里
                del digits, res
                gc.collect()

            # 把能算的都算完了还没找到 —— 告诉用户「还能不能继续、需要什么条件」
            last = targets[-1]
            try:
                avail = pb.available_memory_bytes()
                nxt = last * 2
                need = pb.estimate_peak_memory(nxt, LOW_MEMORY_WORKERS)
                if avail and need < avail:
                    more = f"内存允许，可继续算到 {nxt:,} 位"
                    can = True
                else:
                    more = (f"再往上算到 {nxt:,} 位需要约 {need / 2 ** 20:.0f} MB，"
                            f"当前可用 {avail / 2 ** 20:.0f} MB —— 请关闭一些程序后点「继续往后算」")
                    can = False
            except Exception:
                more, can = "可点「继续往后算」再试", True

            self._set(running=False, done=True, depth=last, can_continue=can,
                      message=f"已算到 {last:,} 位，仍未出现完整生日。{more}")

        except MemoryError:
            try:
                import pi_birthday as pb
                avail = pb.available_memory_bytes()
                a = f"{avail / 2 ** 20:.0f} MB" if avail else "未知"
            except Exception:
                a = "未知"
            self._set(running=False, done=True, can_continue=True,
                      error=f"内存不足，计算中断（当前可用 {a}）。\n"
                            f"这是「内存优先」策略下的正常保护 —— 请关闭一些程序后"
                            f"点「继续往后算」，程序会从当前进度接着往下算。")
        except Exception as e:
            self._set(running=False, done=True, can_continue=True,
                      error=f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._state["elapsed"] = time.time() - t0


# ============================================================================
def main() -> int:
    mp.freeze_support()          # 冻结后创建子进程必需，且要在最前面

    try:
        import webview
    except Exception as e:
        print(f"无法载入 pywebview：{e}", file=sys.stderr)
        return 1

    index = os.path.join(web_dir(), "index.html")
    if not os.path.exists(index):
        print(f"找不到界面文件：{index}", file=sys.stderr)
        return 1

    seed = os.path.join(web_dir(), "pi_seed_10m.z")
    seed_mb = os.path.getsize(seed) / 1024 / 1024 if os.path.exists(seed) else 0.0

    print(f"{APP_TITLE} —— 网页版外壳")
    print(f"  界面目录 : {web_dir()}")
    print(f"  内置数据 : {'pi_seed_10m.z（%.1f MB）' % seed_mb if seed_mb else '⚠️ 缺失'}")
    print(f"  联网状态 : 纯本地（仅 127.0.0.1 环回；深层计算走 gmpy2 本机计算）")
    print("  关闭窗口即可退出程序")

    api = Api()
    webview.create_window(
        APP_TITLE,
        url=index,
        js_api=api,
        width=WIN_W,
        height=WIN_H,
        min_size=(760, 620),
        background_color="#06060b",
        text_select=False,
        easy_drag=False,
    )

    webview.start(http_server=True, private_mode=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
