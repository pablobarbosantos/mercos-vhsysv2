# -*- mode: python ; coding: utf-8 -*-
# Gerar (da raiz do projeto):
#   .\venv\Scripts\pyinstaller.exe consulta_vhsys\consulta_vhsys.spec --distpath consulta_vhsys --workpath consulta_vhsys\build
# Executavel: consulta_vhsys\Consulta_VHSys\Consulta_VHSys.exe

import os
block_cipher = None

_project_root = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..'))
_pkg_dir = os.path.dirname(SPEC)
_assets_dir = os.path.join(_pkg_dir, 'assets')
_datas = [
    (os.path.join(_pkg_dir, 'templates'), 'templates'),
]
if os.path.isdir(_assets_dir):
    _datas.append((_assets_dir, 'assets'))

_icon = os.path.join(_assets_dir, 'logo.ico')
_icon = _icon if os.path.exists(_icon) else None

a = Analysis(
    [os.path.join(os.path.dirname(SPEC), 'main.py')],
    pathex=[_project_root],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'consulta_vhsys.server',
        'consulta_vhsys.database.database',
        'consulta_vhsys.services.vhsys_adapter',
        'consulta_vhsys.services.product_lookup',
        'consulta_vhsys.services.sync_service',
        'consulta_vhsys.services.duplicidade_service',
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'fastapi', 'starlette', 'anyio', 'anyio._backends._asyncio',
        'webview', 'webview.platforms.winforms',
        'clr', 'System', 'System.Windows.Forms',
        'requests', 'dotenv',
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
    name='Consulta_VHSys',
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
    name='Consulta_VHSys',
)
