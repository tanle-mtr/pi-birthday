<div align="center">

# 🎂 生日在圆周率第几位？

**查一查你的生日，藏在 π 的小数点后第几位。**

内置 1000 万位 · 可选 10 亿位离线包 · 纯离线可用 · 不联网也能跑

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#-安装)
[![Release](https://img.shields.io/github/v/release/tanle-mtr/pi-birthday?include_prereleases&label=download)](../../releases/latest)

</div>

---

## 这是什么

输入生日，它告诉你这个数字串在圆周率小数中**第一次出现**的位置。

```
🎂 生日 2012-05-28   （规范化：20120528）
================================================================

  完整生日 YYYYMMDD  20120528       第 123,503,148 位   [离线数据包]
                 727431497806199437258202417479【20120528】430939643845866791135639433729
  年月日 YYMMDD     120528             第 491,521 位   [内置数据]
                 505415782822449079858408700586【120528】447163358777233043808270913739
  月日年 MMDDYY     052812           第 2,295,315 位   [内置数据]
  日月年 DDMMYY     280512           第 1,224,270 位   [内置数据]
  月日 MMDD        0528                第 18,916 位   [内置数据]

----------------------------------------------------------------
  内置数据：10,000,000 位，命中 4 项
  离线数据包：1,000,000,000 位，命中 5 项，1.05s
  总耗时：1.19 秒

✅ 完整生日 20120528 出现在圆周率第 123,503,148 位
```

---

## ✨ 特性

| | |
|---|---|
| 🔍 **五种格式一起查** | `YYYYMMDD`、`YYMMDD`、`MMDDYY`、`DDMMYY`、`MMDD` 一次搜完 |
| 📴 **纯离线** | 内置 1000 万位，不联网、不等待、不算 π，开箱即出结果 |
| 🚀 **可选 10 亿位** | 下载离线数据包后，8 位生日命中率从 10% 提到 **99.99%** |
| 🧠 **内存优先** | 本地计算按可用内存自动规划，宁可慢也不 OOM |
| ⚡ **多线程按块搜索** | 10 亿位全扫约 **1 秒**（热缓存），内存占用仅 **41 MB** |
| 🖥️ **三种界面** | 命令行 / 桌面 GUI（tkinter）/ 网页版（pywebview + WebView2） |
| 📦 **机器可读输出** | `--json` 便于脚本集成，退出码区分「找到 / 没找到 / 出错」 |

---

## 📥 安装

### 方式一：直接下载可执行文件（推荐）

到 [**Releases**](../../releases/latest) 下载：

| 文件 | 大小 | 说明 |
|---|---|---|
| `PiBirthdayWeb.exe` | 23 MB | **网页版**（推荐）—— 深色渐变界面、动效、最完整的功能 |
| `PiBirthday.exe` | 19 MB | 桌面版（tkinter）单文件 |
| `PiBirthdayCLI.exe` | 19 MB | 命令行版 |
| `pi_1b.pack` | 449 MB | **10 亿位离线数据包**（可选，放在 exe 旁边即自动识别） |

双击即用，**不需要装 Python，也不需要联网**。

> **Windows 首次运行提示「未知发布者」**：因为没买代码签名证书。点「更多信息」→「仍要运行」即可。
>
> **网页版需要 WebView2 运行时**：Win10/11 和 Edge 自带，一般无需额外安装。

### 方式二：从源码运行

```bash
git clone https://github.com/tanle-mtr/pi-birthday.git
cd pi-birthday
pip install -r requirements.txt      # 主要是 gmpy2
python cli.py 1990-01-01
```

---

## 🚀 快速开始

```bash
# 自动逐级查（内置数据 → 离线包 → 本地计算）
python cli.py 1990-01-01

# 机器可读输出
python cli.py 1990-01-01 --json

# 查看环境与数据源状态
python cli.py --info

# 只查内置 + 离线包，不做本地计算（快）
python cli.py 1990-01-01 --no-compute

# 本地计算到 5000 万位
python cli.py 1990-01-01 -d 50000000
```

### 命令行参数

| 参数 | 说明 |
|---|---|
| `birthday` | 生日，支持 `1990-01-01` / `19900101` / `1990/1/1` |
| `--info` | 显示数据源、内存上限、并行度上限 |
| `--json` | 输出 JSON |
| `-q, --quiet` | 只输出最终结果 |
| `-v, --verbose` | 输出更多过程信息 |
| `--pack PATH` | 指定离线数据包路径 |
| `--no-pack` | 不使用离线数据包 |
| `--no-compute` | 不做本地计算 |
| `-d, --compute-digits N` | 本地计算到 N 位 |
| `--pack-workers N` | 离线包搜索线程数（默认 8） |
| `-w, --workers N` | 本地计算进程数（0=自动） |
| `-c, --conns N` | 下载并发连接数 |
| `--offline` | 纯离线模式，绝不联网 |

**退出码**：`0` 找到完整生日 · `1` 数据源都查完仍未找到 · `2` 参数或运行错误

---

## 🗂️ 三级数据来源

程序按下面顺序逐级搜索，**找到完整生日就停**：

```
① 内置 1000 万位（随包分发，秒出）
        ↓ 没找到
② 离线数据包 pi_1b.pack —— 10 亿位，多线程按块搜索（约 1 秒）
        ↓ 没找到
③ 本地 gmpy2 计算 —— 按可用内存逐级翻倍，最多约 4 亿位
```

### ① 内置数据（开箱即用）

`pi_seed_10m.z` 是 1000 万位 π 的 zlib 压缩包（9.5 MB → 4.5 MB）。
8 位完整生日在这范围内的命中率约 **10%**，但 4 位（月日）和 6 位几乎必中。

### ② 离线数据包（强烈建议）

到 [Releases](../../releases/latest) 下载 `pi_1b.pack`（449 MB），放在 exe 旁边即被自动识别。

| 指标 | 实测 |
|---|---|
| 搜索范围 | **10 亿位** |
| 8 位生日命中率 | **99.99%** |
| 全扫耗时 | 冷缓存 6.7~22s / 热缓存 **0.8~1.0s** |
| 进程内存 | **41 MB**（与总位数无关） |
| 压缩率 | 47.1%（十进制数字熵 3.32 bit/位，理论下限 41.5%） |

**为什么 449 MB 只占 41 MB 内存**：包格式是「每 100 万位一个独立 zlib 块 + 偏移表」，
查第 k 块只解压那一块（约 0.47 MB），内存占用与总位数无关。

```
magic "PI1B" | version | chunk=1000000 | nchunks=1000 | offsets[1000] | zlib块 × 1000
```

**并行搜索**：8 线程并行解压+搜索。但「首次出现」不能谁先算完算谁 ——
所以是**并行计算、按块序号顺序消费结果**，命中即取消未开始的任务。

| 线程 | 全扫 10 亿位 | I/O 放大 |
|---|---|---|
| 1 | 23.90s | 1.00x |
| 4 | 10.76s | 1.00x |
| **8** | **6.72s** | **1.00x** |

自己生成（约 20 分钟，一次性）：

```bash
python tools/make_offline_pack.py
```

会用 8 并发连接按块下载 MIT 的 10 亿位源，逐块压缩、支持断点续传，
最后用**官方 md5 校验完整性**。

### ③ 本地计算（受内存限制）

没有离线包时，用 gmpy2 + Chudnovsky 级数本地计算。**固定单进程**（内存优先）：

| 可用内存 | 可算上限 | 8 位生日命中率 |
|---|---|---|
| 1 GB | 56 M | 43.0% |
| 2 GB | 114 M | 68.0% |
| 4 GB | 230 M | 89.9% |
| 7 GB | 403 M | 98.2% |

内存模型（实测）：**峰值 ≈ 20 MB + 13 字节 × 位数**。

---

## 🖥️ 图形界面

### 网页版（推荐）

```bash
python pi_web_shell.py          # 需要 pip install pywebview
```

深色渐变 + 玻璃拟态界面，年月日三个下拉框，找不到完整生日会自动继续往后算。

界面全部在 `web/` 目录，是纯 HTML/CSS/JS —— **改样式不用重新编译**。

### 桌面版

```bash
python pi_birthday.py           # tkinter，无需额外依赖
```

---

## 📦 自己打包 exe

```bash
pip install pyinstaller gmpy2 pywebview

# 网页版（23 MB，复用系统 WebView2，不需要打包浏览器）
python -m PyInstaller --clean -y --distpath dist_web --workpath build_web PiWeb.spec

# 桌面版单文件（各 19 MB）
python -m PyInstaller --clean -y --distpath dist --workpath build PiBirthday.spec

# 桌面版快速启动（文件夹 38 MB，启动快约 4 倍）
python -m PyInstaller --clean -y --distpath dist_onedir --workpath build_onedir PiBirthdayOnedir.spec
```

> 打包前请先关闭正在运行的 exe，否则会报 `PermissionError: [WinError 5] 拒绝访问`。

---

## 🧪 测试

```bash
python tests/selftest.py        # π 数字正确性（100 万位对照 angio.net 逐字节比对）
                                # + 串并行一致性 + 边界用例 + 纯离线「网络炸弹」测试
python tests/test_pack.py       # 离线包：逐块正确性、乱序随机访问、跨块匹配、
                                # 并行/串行结果一致性、预取、内存占用
python tests/web_uitest.py      # 网页版 UI：读真实 DOM 断言（需 pywebview）
python tests/check_download.py  # 下载数据源与本地计算结果逐字节比对（需联网）
```

所有测试都会**独立校验数据正确性**，不是只跑通就算过。

---

## ⚙️ 性能说明

### 各阶段占比（1000 万位，单进程，CPU 时间）

| 阶段 | CPU 时间 | 占比 |
|---|---|---|
| **binary splitting** | 16.5s | **69.2%** |
| 进制转换（mpz→十进制） | 2.9s | 11.6% |
| 最终除法 `// T` | 1.9s | 8.7% |
| `isqrt` | 1.4s | 5.5% |
| `*(sqrt_c)` | 0.9s | 4.0% |

**结论：瓶颈是 GMP 的大数乘法本身，Python 层面已无优化空间。**

### 实测否决过的优化（记录在此，免得再走一遍）

| 方案 | 预期 | 实测 |
|---|---|---|
| 叶子成块（省 140 万次函数调用） | 更快 | **慢 9%** |
| 迭代式遍历替代递归 | 更快 | 1.06x（噪声内） |
| `gmpy2.const_pi()`（MPFR 内置 π） | C 实现应快得多 | **慢 1.5 倍** |

`const_pi` 最反直觉：MPFR 的是**通用**高精度实现（走 AGM），而
Chudnovsky + binary splitting 正是为「算 π 到 n 位」这个特定任务优化的。

叶子成块为什么反而慢：叶子内 `T = qk*T + P*tk` 让 T 沿单项线性增长，
而递归版是**平衡归约**，乘法规模均匀得多。

### 测量方法也很重要

这台机器墙钟时间噪声极大：**同一份代码不同轮次能差近 2 倍**。所以基准测试统一：

- 用 `time.process_time()`（CPU 时间）而非墙钟
- **交错**跑 A/B，而非先跑完 A 再跑 B
- 多轮取**最小值**

复现方式见 `tools/bench/`。

---

## 📁 项目结构

```
pi-birthday/
├── cli.py                    # 命令行入口（开源主入口）
├── pi_birthday.py            # 核心库：π 生成、离线包、下载、搜索
├── pi_web_shell.py           # 网页版外壳（pywebview）
├── web/                      # 网页版界面（HTML/CSS/JS）
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── vendor/anime.min.js
├── assets/                   # 图标
├── tools/                    # 构建与开发工具
│   ├── make_seed.py          #   生成内置 1000 万位数据
│   ├── make_offline_pack.py  #   生成 10 亿位离线包
│   ├── make_icon.py          #   程序化生成图标
│   └── bench/                #   性能基准与剖析
├── tests/                    # 测试
├── *.spec                    # PyInstaller 配置
└── requirements.txt
```

---

## ❓ 常见问题

**Q：为什么我的完整生日找不到？**

8 位数字在 π 中首次出现的位置服从均匀分布，命中率 = `1 - e^(-N/1e8)`：

| 搜索位数 | 命中率 |
|---|---|
| 1000 万（内置） | 约 10% |
| 1 亿 | 约 63% |
| **10 亿（离线包）** | **约 99.99%** |

**找不到是概率使然，不是程序问题。** 下载 10 亿位离线包即可解决。

**Q：为什么 `--json` 的 `position` 和 `position_0based` 不一样？**

`position` 从 **1** 起算（「第几位」的日常说法），`position_0based` 是数组下标。
π 小数第一位是 `1`（3.**1**4159…）。

**Q：离线包 449 MB 会不会很吃内存？**

不会。分块压缩格式让它只占 **41 MB**，与总位数无关。

**Q：能查比 10 亿位更远吗？**

可以，但需要自己造更大的数据包。修改 `tools/make_offline_pack.py` 里的
`NCHUNKS` 并准备对应数据源即可。当前 MIT 源的公开数据到 10 亿位。

**Q：支持其他格式的生日吗？**

目前支持 `YYYYMMDD`、`YYMMDD`、`MMDDYY`、`DDMMYY`、`MMDD` 五种。
要加新格式改 `pi_birthday.py` 的 `build_patterns()`。

---

## 📜 许可证

[MIT](LICENSE) —— 随便用，包括商用。

## 🙏 数据来源

- [stuff.mit.edu/afs/sipb/contrib/pi](https://stuff.mit.edu/afs/sipb/contrib/pi/) —— 10 亿位，支持 Range 分段下载
- [angio.net/pi](https://www.angio.net/pi/digits/) —— 100 万位，用于交叉校验
