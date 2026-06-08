# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import customtkinter


block_cipher = None
project_root = Path(SPECPATH).parent
customtkinter_root = Path(customtkinter.__file__).resolve().parent


a = Analysis(
    [str(project_root / "run.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(customtkinter_root), "customtkinter"),
    ],
    hiddenimports=[
        "PIL._tkinter_finder",
        "sv_ttk",
        "bworkflow_sql.master_service",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="B-Workflow-SQL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=str(project_root / "packaging" / "assets" / "bworkflow_icon.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
