from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
kcl_data, kcl_binaries, kcl_imports = collect_all("kcl")

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=kcl_binaries,
    datas=kcl_data + copy_metadata("zoo-kcl") + copy_metadata("zoo-kcl-check-cli"),
    hiddenimports=kcl_imports + ["kcl.kcl", "zoo_kcl_check_cli"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name="kcl-check",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
