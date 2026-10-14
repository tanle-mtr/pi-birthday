# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：生日在圆周率第几位（并行加速版）

同时产出两个可执行文件：
  dist/PiBirthday.exe     图形界面版（无控制台窗口）
  dist/PiBirthdayCLI.exe  命令行版（带控制台，便于脚本调用）

关键点：
  * copy_metadata('gmpy2') —— gmpy2 的 __init__ 会调用
    importlib.metadata.version('gmpy2')，若不打包 dist-info 元数据，
    运行时会抛 PackageNotFoundError: No package metadata was found for gmpy2
  * 程序内已调用 multiprocessing.freeze_support()，
    否则冻结后创建子进程会无限递归复制自身
"""

from PyInstaller.utils.hooks import copy_metadata

# gmpy2 的包元数据（必需，否则冻结后导入失败）
datas = copy_metadata('gmpy2')

# 内置 π 数据（zlib 压缩的 1000 万位）。
# 有了它，前 1000 万位可以「不联网 + 不计算」直接秒出。
# 存压缩版是因为 onefile 每次启动都要把内置文件解压到临时目录，
# 压缩后每次启动的解压量从 9.5MB 降到 4.5MB。
import os
_SEED = 'pi_seed_10m.z'
if os.path.exists(_SEED):
    datas += [(_SEED, '.')]
else:
    print('WARNING: 未找到内置数据 %s，将构建出不带离线数据的版本！'
          '可运行 python make_seed.py 生成。' % _SEED)

a = Analysis(
    ['pi_birthday.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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

pyz = PYZ(a.pure)

# ---------- 图形界面版：不显示黑色控制台窗口 ----------
exe_gui = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PiBirthday',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------- 命令行版：保留控制台，可用 --cli 参数 ----------
exe_cli = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PiBirthdayCLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
