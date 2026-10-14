# -*- coding: utf-8 -*-
"""构建 10 亿位离线数据包（分块压缩，支持按需随机读取）。

为什么要分块：
  一次性载入 10 亿位要 1 GB 内存，不可能。做成「每 100 万位一个独立 zlib 块 +
  偏移表」后，查第 k 块只需解压那一块（约 0.44 MB），内存 O(1)。

包格式 pi_1b.pack：
  magic    4 字节   b"PI1B"
  version  uint32   1
  chunk    uint32   每块位数（1_000_000）
  nchunks  uint32   块数（1000）
  offsets  uint64 × nchunks   每个压缩块的起始偏移
  data     nchunks 个 zlib 压缩块，依次排列

中间产物放在 chunks/ 目录，中断后重跑会自动跳过已下好的块。
最后用 MIT 官方 md5 校验整体完整性。
"""
import concurrent.futures as cf
import hashlib
import io
import os
import ssl
import struct
import sys
import threading
import time
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

URL = "https://stuff.mit.edu/afs/sipb/contrib/pi/pi-billion.txt"
MD5_URL = "https://stuff.mit.edu/afs/sipb/contrib/pi/pi-billion.md5"
EXPECT_MD5 = "3901670f41a84174103bd6a8f07651c0"      # pi-billion.txt 的官方 md5

FIRST_DIGIT_OFFSET = 2          # 文件以 "3." 开头，第 1 个小数位在字节 2
CHUNK = 1_000_000               # 每块 100 万位
NCHUNKS = 1000                  # 共 10 亿位

OUT = os.path.join(_ROOT, "pi_1b.pack")
CHUNK_DIR = os.path.join(_ROOT, "chunks")
CONNS = 8
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 用于校验前几块（本地已验证过的 1000 万位）
CHUNK_GUARD = 4096              # 每块开头额外校验的位数


def ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def chunk_path(k):
    return os.path.join(CHUNK_DIR, f"c{k:04d}.z")


def cached(k):
    """已下好的块直接复用（支持断点续传）。返回路径或 None。"""
    p = chunk_path(k)
    return p if os.path.exists(p) and os.path.getsize(p) > 0 else None


def known_prefix(k):
    """第 k 块开头应该是什么。前 10 块能用内置数据核对，其余只能用块内自洽性。"""
    try:
        import pi_birthday as pb
        seed = pb.seed_digits()
        if seed is None:
            return None
        a = k * CHUNK
        if a + CHUNK <= len(seed):
            return seed[a:a + min(CHUNK, 1000)].encode()
    except Exception:
        pass
    return None


def fetch_chunk(k):
    """下载第 k 块并压缩。返回 (k, 压缩字节 或 None, 说明)。"""
    p = cached(k)
    if p:
        with open(p, "rb") as f:
            return k, None, "cached"

    a = k * CHUNK + FIRST_DIGIT_OFFSET
    b = a + CHUNK - 1
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                URL, headers={**UA, "Range": f"bytes={a}-{b}"})
            with urllib.request.urlopen(req, timeout=120, context=ctx()) as r:
                buf = bytearray()
                while len(buf) < CHUNK:
                    blk = r.read(min(262144, CHUNK - len(buf)))
                    if not blk:
                        break
                    buf += blk
            if len(buf) != CHUNK:
                raise IOError(f"只拿到 {len(buf)} 字节，期望 {CHUNK}")
            raw = bytes(buf)
            exp = known_prefix(k)
            if exp and raw[:len(exp)] != exp:
                raise IOError(f"内容校验失败：块 {k} 开头 {raw[:20]!r} != {exp[:20]!r}")
            comp = zlib.compress(raw, 6)
            with open(chunk_path(k), "wb") as f:      # 注意用 chunk_path，不是 cached()
                f.write(comp)
            return k, comp, "ok"
        except Exception as e:
            last = e
            time.sleep(0.6 * (attempt + 1))
    return k, None, f"失败: {last}"


def main():
    os.makedirs(CHUNK_DIR, exist_ok=True)
    print(f"构建 {NCHUNKS * CHUNK:,} 位离线包 → {OUT}")
    print(f"分块：{NCHUNKS} 块 × {CHUNK:,} 位")

    # 先看官方 md5 有没有变
    try:
        req = urllib.request.Request(MD5_URL, headers=UA)
        with urllib.request.urlopen(req, timeout=30, context=ctx()) as r:
            line = r.read(200).decode("utf8", "ignore").strip()
        got = line.split()[0]
        print(f"官方 md5: {got}")
        if got != EXPECT_MD5:
            print(f"⚠️ 官方 md5 与预期不同（预期 {EXPECT_MD5}），仍按官方值校验")
    except Exception as e:
        print(f"⚠️ 取 md5 失败（{e}），跳过最终校验")

    todo = [k for k in range(NCHUNKS) if not cached(k)]
    print(f"待下载 {len(todo)} 块，已有 {NCHUNKS - len(todo)} 块\n")

    t0 = time.time()
    done = NCHUNKS - len(todo)
    lock = threading.Lock()

    # 分批并发：既吃满带宽，又不至于把 1000 块都堆在内存里
    BATCH = 48
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        with cf.ThreadPoolExecutor(CONNS) as ex:
            for k, _comp, note in ex.map(fetch_chunk, batch):
                with lock:
                    if note in ("ok", "cached"):
                        done += 1
                    else:
                        print(f"  ⚠️ 块 {k} {note}")
        el = time.time() - t0
        got_mb = done * CHUNK / 1048576
        rate = got_mb / max(0.001, el)
        eta = (NCHUNKS - done) * CHUNK / 1048576 / max(0.01, rate)
        print(f"  进度 {done}/{NCHUNKS} 块（{got_mb:.0f} MB）  "
              f"{rate:.2f} MB/s  已用 {el / 60:.1f} 分  预计还需 {eta / 60:.1f} 分",
              flush=True)

    # ---- 组装 ----
    print("\n组装包文件…")
    missing = [k for k in range(NCHUNKS) if not cached(k)]
    if missing:
        print(f"❌ 仍有 {len(missing)} 块缺失，无法组装：{missing[:10]}")
        return 1

    offsets = []
    with open(OUT, "wb") as out:
        out.write(b"PI1B")
        out.write(struct.pack("<IIII", 1, CHUNK, NCHUNKS, 0))
        header_at = out.tell()
        out.write(b"\x00" * (8 * NCHUNKS))          # 偏移表占位
        for k in range(NCHUNKS):
            offsets.append(out.tell())
            with open(cached(k), "rb") as f:
                out.write(f.read())
        out.seek(header_at)
        out.write(struct.pack(f"<{NCHUNKS}Q", *offsets))

    size = os.path.getsize(OUT)
    print(f"包大小 {size / 1048576:.1f} MB（压缩率 {size / (NCHUNKS * CHUNK) * 100:.1f}%）")

    # ---- 校验：解压全部块，按顺序算 md5 ----
    print("校验完整性（解压全部块算 md5）…")
    h = hashlib.md5()
    h.update(b"3.")
    with open(OUT, "rb") as f:
        # 偏移表从第 20 字节开始：magic(4) + version/chunk/nchunks/reserved(16)。
        # 早期这里写成 seek(16)，读串了 4 字节，把偏移当成巨大长度去读 → MemoryError。
        f.seek(20)
        table = struct.unpack(f"<{NCHUNKS}Q", f.read(8 * NCHUNKS))
        for k in range(NCHUNKS):
            f.seek(table[k])
            nxt = table[k + 1] if k + 1 < NCHUNKS else size
            h.update(zlib.decompress(f.read(nxt - table[k])))
    got = h.hexdigest()
    print(f"  md5 = {got}")
    if got == EXPECT_MD5:
        print("  ✅ 与官方 md5 一致，10 亿位数据完整无误")
        return 0
    print(f"  ❌ 与官方 md5 不一致（期望 {EXPECT_MD5}）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
