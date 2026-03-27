# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para PDV Pablo Agro.
Gerar: pyinstaller pdv/pdv.spec  (rodar da raiz do projeto)
"""

import os
block_cipher = None

a = Analysis(
    ['pdv/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('pdv/templates',  'pdv/templates'),
        ('pdv/assets',     'assets'),          # logo.png fica ao lado do .exe
        ('.env',           '.'),               # credenciais ao lado do .exe
    ],
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
    console=False,                         # sem janela de console
    icon='pdv/assets/logo.ico',            # ícone do .exe (opcional)
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
