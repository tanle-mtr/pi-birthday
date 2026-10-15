# -*- coding: utf-8 -*-
"""从 GitHub Release 实际下载 CLI exe 并运行，验证发布产物真的可用。

这是最重要的一步 —— 前面的测试都是本地构建的 exe，
而用户拿到的是从 Release 下载的那一份。
"""
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = "tanle-mtr/pi-birthday"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(path):
    r = subprocess.run(["gh", "api", path], capture_output=True)
    return json.loads(r.stdout.decode("utf-8", "ignore"))


def main():
    rel = api(f"repos/{REPO}/releases/latest")
    assets = {a["name"]: a for a in rel["assets"]}
    name = "PiBirthdayCLI.exe"
    if name not in assets:
        print(f"❌ Release 里没有 {name}")
        return 1

    workdir = os.path.join(tempfile.gettempdir(), "pibirthday_release_test")
    os.makedirs(workdir, exist_ok=True)
    exe = os.path.join(workdir, name)

    # ---- 下载 ----
    url = assets[name]["browser_download_url"]
    want = assets[name]["size"]
    print(f"下载 {name}  ({want:,} 字节) …")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300, context=CTX) as r, \
            open(exe, "wb") as f:
        got = 0
        while True:
            blk = r.read(262144)
            if not blk:
                break
            f.write(blk)
            got += len(blk)
    dt = time.time() - t0
    size = os.path.getsize(exe)
    print(f"  实际 {size:,} 字节，耗时 {dt:.1f}s（{size / 2**20 / dt:.2f} MB/s）")
    if size != want:
        print(f"  ❌ 大小不符（期望 {want:,}）")
        return 1
    print("  ✅ 大小一致")

    # ---- 运行 --info ----
    print(f"\n运行 {name} --info …")
    r = subprocess.run([exe, "--info"], capture_output=True, timeout=180)
    out = r.stdout.decode("utf-8", "ignore")
    print("  " + "\n  ".join(out.strip().splitlines()[:16]))
    if r.returncode != 0:
        print(f"  ❌ 退出码 {r.returncode}")
        print("  stderr:", r.stderr.decode("utf-8", "ignore")[:400])
        return 1
    print("  ✅ --info 正常")

    # ---- 实际查询（无离线包，只查内置数据）----
    print(f"\n运行 {name} 2019-09-14（无离线包，应命中内置数据）…")
    r = subprocess.run([exe, "2019-09-14", "--no-pack", "--no-compute", "-q"],
                       capture_output=True, timeout=300)
    out = r.stdout.decode("utf-8", "ignore")
    print("  " + "\n  ".join(out.strip().splitlines()))
    print(f"  退出码 = {r.returncode}")
    # 20190914 在内置数据第 244 位
    if "244" in out and r.returncode == 0:
        print("  ✅ 查询结果正确（第 244 位）")
    else:
        print("  ❌ 结果不符")
        print("  stderr:", r.stderr.decode("utf-8", "ignore")[:400])
        return 1

    # ---- JSON 输出 ----
    print(f"\n运行 {name} 2019-09-14 --json …")
    r = subprocess.run([exe, "2019-09-14", "--no-pack", "--no-compute", "--json"],
                       capture_output=True, timeout=300)
    try:
        d = json.loads(r.stdout.decode("utf-8", "ignore"))
        full = next(x for x in d["results"] if x["format"] == "YYYYMMDD")
        print(f"  found={d['found_full']}  position={full['position']}  "
              f"source={full['source']}")
        ok = d["found_full"] and full["position"] == 244
        print(f"  {'✅ JSON 输出正确' if ok else '❌ JSON 结果不符'}")
        if not ok:
            return 1
    except Exception as e:
        print(f"  ❌ JSON 解析失败：{e}")
        print("  输出:", r.stdout.decode("utf-8", "ignore")[:300])
        return 1

    print("\n" + "=" * 56)
    print("✅ Release 产物端到端验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
