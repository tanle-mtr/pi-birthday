# -*- coding: utf-8 -*-
"""带重试的单次上传 Release 资产。

背景（实测数据）：
  本机到 uploads.github.com 约 0.31 MB/s，449 MB 需要约 24 分钟。
  `gh release upload` 中途断连（4.7 分钟时），且不支持重试。

为什么不做分片续传：
  试过用 Content-Range 分片，但 GitHub 的 Release 资产上传**不支持**
  这种简单分片（第二片返回 422），它要求先创建 upload session 再逐片 PUT。
  而 curl 的 --data-binary 会把整个文件发出，--range 并不裁剪请求体，
  所以那条路走不通。

本脚本的策略：
  · 整文件一次 POST（实测 30 MB 单次可成功）
  · 失败自动重试，且每次重试前删除服务端残片（避免 422 冲突）
  · 大文件建议配合 --resume-from 用「先传前半、再传后半」不行 ——
    改为：失败就整体重试，因为 24 分钟里大部分时间网络是稳的

用法：
  python tools/gh_upload.py <owner/repo> <tag> <file> [--max-retry 5]
"""
import argparse
import json
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def api(path, method="GET", body=None):
    cmd = ["gh", "api", "-X", method, path]
    if body is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd,
                       input=(json.dumps(body).encode() if body else None),
                       capture_output=True)
    out = r.stdout.decode("utf-8", "ignore").strip()
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(f"{method} {path}:\n{out}\n{err}")
    return json.loads(out) if out else None


def token():
    r = subprocess.run(["gh", "auth", "token"], capture_output=True)
    return r.stdout.decode().strip()


def get_release(repo, tag):
    """draft 状态的 release 不通过 /releases/tags/ 暴露，需遍历列表。"""
    try:
        return api(f"repos/{repo}/releases/tags/{tag}")
    except RuntimeError:
        pass
    for r in api(f"repos/{repo}/releases"):
        if r.get("tag_name") == tag:
            return r
    raise RuntimeError(f"找不到 tag={tag} 的 release")


def find_asset(repo, rid, name):
    for a in api(f"repos/{repo}/releases/{rid}/assets"):
        if a["name"] == name:
            return a
    return None


def upload_once(url, tok, path, timeout=7200):
    """整文件一次 POST，返回 (http_code, curl_exit)。"""
    r = subprocess.run([
        "curl", "-s", "-o", os.devnull, "-w", "%{http_code}",
        "-X", "POST",
        "-H", f"Authorization: Bearer {tok}",
        "-H", "Content-Type: application/octet-stream",
        "--data-binary", f"@{path}",
        "--max-time", str(timeout),
        url,
    ], capture_output=True)
    code = r.stdout.decode("utf-8", "ignore").strip()
    return code, r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("tag")
    ap.add_argument("file")
    ap.add_argument("--max-retry", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=7200, help="单次上传超时（秒）")
    args = ap.parse_args()

    path = args.file if os.path.isabs(args.file) else os.path.join(_ROOT, args.file)
    if not os.path.exists(path):
        print(f"文件不存在：{path}")
        return 1

    name = os.path.basename(path)
    total = os.path.getsize(path)
    tok = token()
    rel = get_release(args.repo, args.tag)
    rid = rel["id"]
    url = (f"https://uploads.github.com/repos/{args.repo}"
           f"/releases/{rid}/assets?name={name}")

    print(f"Release : {args.tag} (id={rid})")
    print(f"文件    : {name}  {total / 2 ** 20:.1f} MB")
    print(f"地址    : {url}")
    print()

    existing = find_asset(args.repo, rid, name)
    if existing:
        if existing.get("state") == "uploaded" and existing.get("size") == total:
            print(f"✅ 已完整上传（{existing['size']:,} 字节），跳过")
            return 0
        print(f"发现残片 {existing['size']:,} 字节（state={existing['state']}），删除")
        api(f"repos/{args.repo}/releases/assets/{existing['id']}", "DELETE")

    for attempt in range(1, args.max_retry + 1):
        print(f"[第 {attempt}/{args.max_retry} 次尝试] 开始上传 "
              f"{total / 2 ** 20:.1f} MB …", flush=True)
        t0 = time.time()
        code, rc = upload_once(url, tok, path, args.timeout)
        dt = time.time() - t0
        rate = total / 2 ** 20 / max(0.01, dt)
        print(f"  HTTP {code}  curl_exit={rc}  耗时 {dt / 60:.1f} 分  "
              f"平均 {rate:.2f} MB/s", flush=True)

        if code == "201":
            a = find_asset(args.repo, rid, name)
            if a and a.get("size") == total and a.get("state") == "uploaded":
                print(f"\n✅ 上传完成：{name}  {total:,} 字节")
                print(f"   {a['browser_download_url']}")
                return 0
            print(f"  ⚠️ 服务端状态异常：{a}")

        # 失败：清理残片再重试
        a = find_asset(args.repo, rid, name)
        if a:
            print(f"  清理残片 {a.get('size', 0):,} 字节", flush=True)
            try:
                api(f"repos/{args.repo}/releases/assets/{a['id']}", "DELETE")
            except Exception as e:
                print(f"  删除失败：{str(e)[:100]}")
        if attempt < args.max_retry:
            wait = min(60, 5 * attempt)
            print(f"  {wait}s 后重试…", flush=True)
            time.sleep(wait)

    print(f"\n❌ {args.max_retry} 次尝试均失败。")
    print("   网络到 uploads.github.com 不稳定（实测约 0.31 MB/s）。")
    print("   可稍后重跑本脚本。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
