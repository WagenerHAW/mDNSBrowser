# -*- mode: python ; coding: utf-8 -*-

datas = [
    ('assets', 'assets'),
    ('src/os_qt_tools.py', '.')
]

a = Analysis(
    ['src\\app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['zeroconf._utils.ipaddress','zeroconf._handlers.answers', 'psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='mDNS Browser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\default_icon.ico'],
)
