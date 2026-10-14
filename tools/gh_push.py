# -*- coding: utf-8 -*-
"""通过 GitHub Git Data API 推送本地 git 对象到远端。

为什么需要这个：
  本机 github.com:443 被阻断（git push 走 HTTPS 连不上），
  但 api.github.com:443 是通的 —— 所以用 REST API 把对象一个个传上去，
  再由服务端组装成 commit。功能上等价于 git push，只是通道不同。

用法：
  python tools/gh_push.py <owner/repo> <branch> [--dry-run]

前置：gh CLI 已登录，且 token 有 repo 权限。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import zlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, check=True):
    r = subprocess.run(["git", "-C", _ROOT] + list(args),
                       capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: "
                           f"{r.stderr.decode('utf-8', 'ignore')}")
    return r.stdout


def git_text(*args, check=True):
    return git(*args, check=check).decode("utf-8", "ignore").strip()


def gh_api(path, method="GET", body=None, check=True):
    """调用 GitHub REST API。"""
    cmd = ["gh", "api", "-X", method, path]
    if body is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=(json.dumps(body).encode() if body else None),
                       capture_output=True)
    out = r.stdout.decode("utf-8", "ignore")
    if check and r.returncode != 0:
        err = r.stderr.decode("utf-8", "ignore")
        raise RuntimeError(f"API {method} {path} 失败:\n{out}\n{err}")
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/repo")
    ap.add_argument("branch", nargs="?", default="main")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo, branch = args.repo, args.branch

    # ---- 1. 本地 HEAD 与对象清单 ----
    head = git_text("rev-parse", "HEAD")
    commit_msg = git_text("log", "-1", "--format=%B")
    author_name = git_text("log", "-1", "--format=%an")
    author_email = git_text("log", "-1", "--format=%ae")
    author_date = git_text("log", "-1", "--format=%aI")
    print(f"本地 HEAD   : {head[:12]}")
    print(f"作者        : {author_name} <{author_email}>")
    print(f"提交信息    : {commit_msg.splitlines()[0][:60]}")

    # 列出该提交涉及的全部对象（blob/tree/commit）
    objs = git_text("rev-list", "--objects", "--all").splitlines()
    parsed = []
    for line in objs:
        parts = line.split(maxsplit=1)
        sha = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        otype = git_text("cat-file", "-t", sha)
        parsed.append((sha, otype, path))
    n_blob = sum(1 for _, t, _ in parsed if t == "blob")
    n_tree = sum(1 for _, t, _ in parsed if t == "tree")
    n_commit = sum(1 for _, t, _ in parsed if t == "commit")
    print(f"对象        : {n_blob} blob / {n_tree} tree / {n_commit} commit")
    if args.dry_run:
        print("（dry-run，未推送）")
        return 0

    # ---- 2. 上传 blob ----
    print("\n上传 blob …")
    blob_map = {}
    done = 0
    for sha, otype, path in parsed:
        if otype != "blob":
            continue
        # 已存在的对象服务端会直接复用（返回 201 或 200）
        raw = git("cat-file", "blob", sha)
        b64 = base64.b64encode(raw).decode("ascii")
        res = gh_api(f"repos/{repo}/git/blobs", "POST",
                     {"content": b64, "encoding": "base64"})
        blob_map[sha] = res["sha"]
        done += 1
        if done % 10 == 0 or done == n_blob:
            print(f"  {done}/{n_blob}")

    # ---- 3. 建 tree（自底向上：按路径深度排序）----
    print("\n创建 tree …")
    # 收集每个 commit 的 tree 结构，从 git ls-tree 递归拿
    # 简化做法：对每个 tree 对象，用 ls-tree 列出条目，子 tree 先建
    tree_order = []
    seen_tree = set()

    def walk_tree(tree_sha):
        if tree_sha in seen_tree:
            return
        seen_tree.add(tree_sha)
        entries = git_text("ls-tree", tree_sha).splitlines()
        for e in entries:
            meta, name = e.split("\t", 1)
            mode, otype, sha = meta.split()
            if otype == "tree":
                walk_tree(sha)
        tree_order.append(tree_sha)

    for sha, otype, _ in parsed:
        if otype == "tree":
            walk_tree(sha)

    tree_map = {}
    for i, tsha in enumerate(tree_order, 1):
        entries = []
        for e in git_text("ls-tree", tsha).splitlines():
            meta, name = e.split("\t", 1)
            mode, otype, sha = meta.split()
            if otype == "blob":
                entries.append({"path": name, "mode": mode,
                                "type": "blob", "sha": blob_map[sha]})
            else:
                entries.append({"path": name, "mode": mode,
                                "type": "tree", "sha": tree_map[sha]})
        res = gh_api(f"repos/{repo}/git/trees", "POST", {"tree": entries})
        tree_map[tsha] = res["sha"]
        if i % 20 == 0 or i == len(tree_order):
            print(f"  {i}/{len(tree_order)}")

    # ---- 4. 建 commit ----
    print("\n创建 commit …")
    head_tree = git_text("rev-parse", "HEAD^{tree}")
    # 若有父提交（远端已有历史），需要带上 parents
    parent_sha = git_text("rev-parse", "HEAD^", check=False)
    body = {
        "message": commit_msg,
        "tree": tree_map[head_tree],
        "author": {"name": author_name, "email": author_email,
                   "date": author_date},
        "committer": {"name": author_name, "email": author_email,
                      "date": author_date},
    }
    remote_head = gh_api(f"repos/{repo}/git/ref/heads/{branch}", check=False)
    if isinstance(remote_head, dict) and "object" in remote_head:
        body["parents"] = [remote_head["object"]["sha"]]
        print(f"  远端已有分支，父提交 {remote_head['object']['sha'][:12]}")
    res = gh_api(f"repos/{repo}/git/commits", "POST", body)
    new_commit = res["sha"]
    print(f"  commit = {new_commit[:12]}")

    # ---- 5. 更新/创建 ref ----
    print("\n更新分支 …")
    if isinstance(remote_head, dict) and "object" in remote_head:
        gh_api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH",
               {"sha": new_commit, "force": True})
        print(f"  已更新 {branch}")
    else:
        gh_api(f"repos/{repo}/git/refs", "POST",
               {"ref": f"refs/heads/{branch}", "sha": new_commit})
        print(f"  已创建 {branch}")

    print(f"\n✅ 推送完成：https://github.com/{repo}/tree/{branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
