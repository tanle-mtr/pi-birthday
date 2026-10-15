# 贡献指南

感谢愿意帮忙！这个项目不大，几条约定说清楚就好。

---

## 🐛 报告问题

请用 [Issue 模板](https://github.com/tanle-mtr/pi-birthday/issues/new/choose)，并**务必附上**：

```bash
python cli.py --info          # 环境信息（Python/gmpy2/内存/数据源）
```

以及你运行的**完整命令**和**完整输出**。

### 关于「我的生日找不到」

**这不是 bug。** 8 位数字在 π 中首次出现的位置服从均匀分布，
命中率 = `1 - e^(-N/1e8)`：

| 搜索位数 | 命中率 |
|---|---|
| 1000 万（内置） | 约 10% |
| 1 亿 | 约 63% |
| 10 亿（离线包） | 约 99.99% |

先确认你下载了 `pi_1b.pack` 并放在 exe 同目录，再提 issue。

---

## 🔧 提交代码

```bash
git clone https://github.com/tanle-mtr/pi-birthday.git
cd pi-birthday
pip install -r requirements-dev.txt
```

### 提交前请自测

```bash
python tests/selftest.py        # 核心正确性（必须全绿）
python tests/test_pack.py       # 离线包（需要 pi_1b.pack）
python tests/web_uitest.py      # 网页版 UI（需要 pywebview）
```

**任何改动都必须保持 `tests/selftest.py` 全绿。** 这个项目的核心是「算得对」，
不是「跑得快」。

---

## ⚠️ 改性能代码前请先读这个

项目在性能上已经做过一轮系统优化，**踩过的坑都写在代码注释和 README 里**。
几条硬规矩：

### 1. 用 CPU 时间测，不要用墙钟

这台机器墙钟噪声极大 —— **同一份代码不同轮次能差近 2 倍**。

```python
# ❌ 不可信
t = time.time(); f(); print(time.time() - t)

# ✅ 用 CPU 时间 + 交错多轮 + 取最小值
c0 = time.process_time(); f(); dt = time.process_time() - c0
```

现成的框架见 `tools/bench/bench_cpu.py`、`tools/bench/bench_bs_ab.py`。

### 2. 被否决过的方案，别再试

| 方案 | 实测结果 |
|---|---|
| 叶子成块（省 140 万次函数调用） | **慢 9%** |
| 迭代式遍历替代递归 | 1.06x（噪声内） |
| `gmpy2.const_pi()` | **慢 1.5 倍** |
| 多进程超配（> 核心数） | 显著更慢 |

**瓶颈是 GMP 的大数乘法本身**，Python 层面已无优化空间。
如果你认为找到了新突破，请附上**可复现的基准脚本**。

### 3. 内存优先是硬约束

本地计算**固定单进程**（多进程峰值内存按倍数上涨）。
任何改动都不能突破「峰值 ≈ 20 MB + 13 字节 × 位数」这个模型太多。

---

## 📐 代码风格

- Python 3.9+ 语法，**中文注释**（项目注释都是中文，保持一致）
- 关键决策要写**为什么**，而不是复述代码在做什么
- 实测数据写进注释，标注测量脚本名，方便后人复核

```python
# ✅ 好注释：说了为什么 + 附实测依据
# 直接算 10^(2n)，而不是先算 10^n 再平方：
# 实测 isqrt 阶段 1.39s vs 1.72s（1000 万位，CPU 时间，1.24x）。
sqrt_c = gmpy2.isqrt(mpz(10005) * mpz(10) ** (2 * total_digits))

# ❌ 无信息量
# 计算 sqrt_c
sqrt_c = ...
```

---

## 🧪 加新测试

测试的价值在于**独立校验正确性**，不是「跑通就算过」。好的测试长这样：

```python
# ✅ 与独立来源逐字节比对
assert generated == reference_from_angio_net

# ❌ 只是没报错
generated = compute()
print("ok")
```

`tests/selftest.py` 里还有个「网络炸弹」测试 —— 把 `urllib.request.urlopen`
换成直接抛异常的桩，用来证明离线模式真的不发网络请求。这类思路很欢迎。

---

## 📦 打包

```bash
python -m PyInstaller --clean -y --distpath dist_web --workpath build_web PiWeb.spec
```

注意：打包前**先关闭正在运行的 exe**，否则报 `PermissionError: [WinError 5]`。

### ⚠️ 打包后必须实测 exe，不能只测源码

**踩过的坑（v1.0.0 差点带着它发布）**：`PiBirthday.spec` 里两个 EXE
（GUI 版和 CLI 版）**共用 `pi_birthday.py` 作为入口**，而新的 `cli.py`
根本没被打进去。结果：

```
PiBirthdayCLI.exe 1990-01-01 --json
```

老入口不认这些参数，缺少必填的 `--birthday` 时它 **fallback 到 `run_gui()`**，
在无显示环境下打开 tkinter 窗口 → **永久卡死**。

症状很有迷惑性：
- 进程起来了，占 60 MB 内存（看起来正常）
- stdout **一个字节都不输出**
- **永不退出**（等一个永远不出现的 GUI 窗口）
- 文件和本地构建版**哈希完全一致** —— 所以怎么比对文件都查不出问题

**排查绕了很远**：先怀疑下载损坏（哈希一致，排除）→ 怀疑网络标记
Zone.Identifier（没有，排除）→ 怀疑路径冲突（换目录仍复现）→
最后看**进程树**才发现应用进程起来了、只是不干活。

**教训**：源码测试和打包测试是两件事。改完 CLI 一定要跑一遍

```bash
python tests/verify_release.py     # 从 GitHub 下载发布产物并实际运行
```

或者在打包后直接手工验一遍所有子命令与退出码。

---

## 📜 许可证

提交代码即表示同意以 [MIT](LICENSE) 许可证发布。
