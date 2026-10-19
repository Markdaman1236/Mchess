# -*- mode: python ; coding: utf-8 -*-
# Build the one-file Chess.exe:  python -m PyInstaller --noconfirm --clean Chess.spec
import os

here = SPECPATH

a = Analysis(
    [os.path.join(here, 'chess_app.py')],
    datas=[
        (os.path.join(here, 'piece_meshes.json'), '.'),
        (os.path.join(here, 'Resources'), 'Resources'),
        (os.path.join(here, 'engine'), 'engine'),
    ],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Chess',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
