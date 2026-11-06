# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: genera dist\KORVEX-Disk-Manager.exe (un solo archivo, pide UAC al abrir)
#   python -m PyInstaller --noconfirm --clean korvex-disk-manager.spec

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('KorvexLogo.ico', '.'),
        ('KorvexLogo-256.png', '.'),
        ('KorvexLogo-128.png', '.'),
    ],
    hiddenimports=['win_ops', 'operations', 'rawio', 'diag'],
    hookspath=[],
    runtime_hooks=[],
    # El helper de root y su cliente solo se usan en Linux
    excludes=['korvex_helper', 'helper_client', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='KORVEX-Disk-Manager',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon='KorvexLogo.ico',
    uac_admin=True,          # Manifiesto requireAdministrator
)
