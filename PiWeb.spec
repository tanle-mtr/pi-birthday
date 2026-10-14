# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：网页版外壳（pywebview + WebView2）

产出：
  dist/PiBirthdayWeb.exe   单文件版，图形界面（无控制台窗口）

要点：
  * 整个 web/ 目录（HTML/CSS/JS + Anime.js + 圆周率数据）打进 exe，
    运行时 pywebview 用 127.0.0.1 环回服务把它喂给 WebView2
  * 无需打包浏览器内核 —— 复用系统自带的 WebView2 运行时，
    所以体积远小于 Electron（后者通常 200 MB+）
  * pywebview 的平台后端是按名字动态导入的，必须写进 hiddenimports，
    否则冻结后会报 "no suitable GUI backend found"
"""

import os

from PyInstaller.utils.hooks import copy_metadata

datas = [('web', 'web')]      # 整个前端目录（含 favicon）

# gmpy2 的包元数据（必需！）。gmpy2/__init__.py 会调用
# importlib.metadata.version("gmpy2")，不打包 dist-info 的话冻结后
# 导入直接抛 PackageNotFoundError，于是静默退化成纯 Python decimal——
# 慢约 1000 倍，界面上会显示「纯 Python 计算中…」。
datas += copy_metadata('gmpy2')

ICON = os.path.join('assets', 'icon.ico')
if not os.path.exists(ICON):
    print('WARNING: 未找到图标 %s，可运行 python make_icon.py 生成' % ICON)
    ICON = None

a = Analysis(
    ['pi_web_shell.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'clr_loader',
        'pythonnet',
        # 外壳在函数里按需导入它来「继续往后算」，显式列出避免被漏掉
        'pi_birthday',
        'gmpy2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'pandas', 'matplotlib', 'scipy', 'PIL',
        'pytest', 'IPython', 'setuptools', 'pip',
        # 外壳不需要 tkinter（桌面版才用），排掉能省几 MB
        'tkinter', '_tkinter',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PiBirthdayWeb',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # 图形程序，不弹控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
