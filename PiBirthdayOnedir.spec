# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（onedir 快速启动版）

与 onefile 版的区别：
  onefile 每次启动都要把约 24MB 运行时解压到 %TEMP%，实测中位约 3.8s；
  onedir 免解压，启动只需约 1s。代价是产出一个文件夹而非单个 exe。

产出：
  dist/onedir/PiBirthday/PiBirthday.exe        图形界面版
  dist/onedir/PiBirthday/PiBirthdayCLI.exe     命令行版
"""

from PyInstaller.utils.hooks import copy_metadata
import os

datas = copy_metadata('gmpy2')

_SEED = 'pi_seed_10m.z'
if os.path.exists(_SEED):
    datas += [(_SEED, '.')]
else:
    print('WARNING: 未找到内置数据 %s！' % _SEED)

a = Analysis(
    ['pi_birthday.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy', 'PIL',
              'pytest', 'IPython', 'setuptools', 'pip'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='PiBirthday',
    debug=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)

exe_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='PiBirthdayCLI',
    debug=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
)

coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='PiBirthday',
)
