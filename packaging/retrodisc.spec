# PyInstaller spec: onedir build, later wrapped into an AppImage.
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(ROOT / "src" / "retrodisc" / "i18n"), "retrodisc/i18n")],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtMultimedia", "PySide6.QtCharts"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="retrodisc", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="retrodisc")
