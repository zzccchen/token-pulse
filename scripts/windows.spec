# PyInstaller spec for an inspectable, replaceable-DLL Windows distribution.
import os
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "scripts"))
from windows_bundle import keep_binary

staging = Path(os.environ["TOKEN_PULSE_BUILD_STAGING"])
a = Analysis(
    [str(root / "src/token_pulse/__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtNetwork", "PySide6.QtSvg", "ssl", "_ssl", "lzma", "_lzma", "bz2", "_bz2"],
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries if keep_binary(entry[0])]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TokenPulse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(staging / "TokenPulse.ico"),
    version=str(staging / "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TokenPulse")
