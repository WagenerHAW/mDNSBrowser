# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH).parent

datas = [
    (str(project_root / 'assets'), 'assets'),
]

a = Analysis(
    [str(project_root / 'src/app.py')],
    pathex=[str(project_root / 'src')],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'zeroconf._utils.ipaddress', 'zeroconf._handlers.answers', 'psutil',
        'async_upnp_client.search', 'async_upnp_client.ssdp',
        'network_interfaces', 'os_qt_tools', 'upnp',
    ],
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
    icon=[str(project_root / 'assets/default_icon.ico')],
)
