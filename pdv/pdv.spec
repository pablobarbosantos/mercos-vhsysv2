# -*- mode: python ; coding: utf-8 -*-
# Gerar (da raiz do projeto):
#   .\venv\Scripts\pyinstaller.exe pdv\pdv.spec --distpath pdv --workpath pdv\build
# Executavel: pdv\PDV_PabloAgro\PDV_PabloAgro.exe

import os
block_cipher = None

# Assets opcionais (logo.png/logo.ico podem nao existir ainda)
_assets_dir = os.path.join(os.path.dirname(SPEC), 'assets')
_datas = [
    ('templates',  'templates'),
    ('../.env',    '.'),
]
if os.path.isdir(_assets_dir):
    _datas.append(('assets', 'assets'))

_icon = os.path.join(_assets_dir, 'logo.ico')
_icon = _icon if os.path.exists(_icon) else None

a = Analysis(
    ['main.py'],
    pathex=['..'],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'pdv.server', 'pdv.vhsys', 'pdv.database',
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'fastapi', 'starlette', 'anyio', 'anyio._backends._asyncio',
        'jinja2', 'aiofiles',
        'webview', 'webview.platforms.winforms',
        'clr', 'System', 'System.Windows.Forms',
        'requests', 'dotenv', 'fuse',
        'PIL', 'PIL.Image', 'PIL.ImageTk',
        'tkinter', 'tkinter.ttk',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDV_PabloAgro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PDV_PabloAgro',
)
