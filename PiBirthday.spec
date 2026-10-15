# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：生日在圆周率第几位

产出两个可执行文件：
  dist/PiBirthday.exe     桌面图形界面版（无控制台窗口）
  dist/PiBirthdayCLI.exe  命令行版（带控制台）

⚠️ 两个 exe 的**入口脚本不同**，这是踩过坑的地方：
  · GUI 版入口 = pi_birthday.py，不带参数时进 tkinter 界面
  · CLI 版入口 = cli.py，纯命令行，支持 --json / --info / 退出码

早期版本两个 EXE 共用 pi_birthday.py 作为入口，导致
`PiBirthdayCLI.exe 1990-01-01 --json` 这种新 CLI 的用法完全不被识别 ——
老入口只认 `--cli --birthday`，而缺少必填参数时它会 fallback 到
run_gui()，在无显示环境下打开 tkinter 窗口**永久卡死**
（表现为：进程起来了、占 60 MB 内存、stdout 一个字节都不输出、永不退出）。

关键点：
  * copy_metadata('gmpy2') —— gmpy2 的 __init__ 会调用
    importlib.metadata.version('gmpy2')，若不打包 dist-info 元数据，
    运行时会抛 PackageNotFoundError
  * 程序内已调用 multiprocessing.freeze_support()，
    否则冻结后创建子进程会无限递归复制自身
"""

import os

from PyInstaller.utils.hooks import copy_metadata

# gmpy2 的包元数据（必需，否则冻结后导入失败）
datas = copy_metadata('gmpy2')

# 内置 π 数据（zlib 压缩的 1000 万位）。
# 有了它，前 1000 万位可以「不联网 + 不计算」直接秒出。
# 存压缩版是因为 onefile 每次启动都要把内置文件解压到临时目录，
# 压缩后每次启动的解压量从 9.5MB 降到 4.5MB。
_SEED = 'pi_seed_10m.z'
if os.path.exists(_SEED):
    datas += [(_SEED, '.')]
else:
    print('WARNING: 未找到内置数据 %s，将构建出不带离线数据的版本！'
          '可运行 python tools/make_seed.py 生成。' % _SEED)

_COMMON = dict(
    pathex=[],
    binaries=[],
    datas=datas,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 这些库本项目用不到，排除以减小体积
    excludes=[
        'numpy', 'pandas', 'matplotlib', 'scipy', 'PIL',
        'pytest', 'IPython', 'setuptools', 'pip',
    ],
    noarchive=False,
    optimize=0,
)

_EXE_COMMON = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------- 图形界面版：入口 pi_birthday.py，不显示控制台 ----------
a_gui = Analysis(['pi_birthday.py'], **_COMMON)
pyz_gui = PYZ(a_gui.pure)

exe_gui = EXE(
    pyz_gui, a_gui.scripts, a_gui.binaries, a_gui.datas, [],
    name='PiBirthday',
    console=False,
    **_EXE_COMMON,
)

# ---------- 命令行版：入口 cli.py，保留控制台 ----------
a_cli = Analysis(['cli.py'], **_COMMON)
pyz_cli = PYZ(a_cli.pure)

exe_cli = EXE(
    pyz_cli, a_cli.scripts, a_cli.binaries, a_cli.datas, [],
    name='PiBirthdayCLI',
    console=True,
    **_EXE_COMMON,
)
