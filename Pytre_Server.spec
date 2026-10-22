# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, SPECPATH)
from Pytre_Core.about import SERVER_BASENAME

if sys.platform == "win32":
    from version_info import version_info

datas = [('Pytre_Core/res', 'res'), ('Pytre_Server/templates', 'Pytre_Server/templates')]
binaries = []
hiddenimports = [
    'uuid',
    'keyring.backends',
    'uvicorn.loops.auto','uvicorn.protocols.http.auto', 'uvicorn.lifespan.on',
    'asyncpg', 'asyncpg.pgproto.pgproto',
]

block_cipher = None

a = Analysis(['Pytre_Server/__main__.py'],
             pathex=['.'],
             binaries=binaries,
             datas=datas,
             hiddenimports=hiddenimports,
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name=SERVER_BASENAME,
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=False,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True,
          icon='Pytre_Core/res/app.ico',
          version=version_info('Pytre Server', f'{SERVER_BASENAME}.exe') if sys.platform == "win32" else None
        )
