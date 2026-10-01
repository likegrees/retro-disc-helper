# PyInstaller spec: onedir build, later wrapped into an AppImage.
#
# Two programs share one bundle:
# - retrodisc: the app (PySide6 / Qt 6.11);
# - retrodisc-die: Detect It Easy (its own Qt 6.7). Built from a separate analysis that
#   excludes PySide6, so PySide6's runtime hook never loads Qt 6.11 into it. The two Qt
#   builds clash if they ever share a process.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
SRC = str(ROOT / "src")

die_datas, die_binaries, die_hidden = collect_all("die")

app = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[SRC],
    datas=[(str(ROOT / "src" / "retrodisc" / "i18n"), "retrodisc/i18n")],
    excludes=["tkinter", "die", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtMultimedia", "PySide6.QtCharts"],
)
die_scan = Analysis(
    [str(ROOT / "packaging" / "die_launcher.py")],
    pathex=[SRC],
    datas=die_datas,
    binaries=die_binaries,
    hiddenimports=die_hidden,
    excludes=["tkinter", "PySide6", "shiboken6"],
)

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="retrodisc",
              console=False)
die_exe = EXE(PYZ(die_scan.pure), die_scan.scripts, [], exclude_binaries=True,
              name="retrodisc-die", console=True)
coll = COLLECT(app_exe, app.binaries, app.datas, die_exe, die_scan.binaries, die_scan.datas,
               name="retrodisc")
