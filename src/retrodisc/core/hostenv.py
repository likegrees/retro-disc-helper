"""Environment for launching host programs (xdg-open, steam, pgrep) from the AppImage.

The PyInstaller bootloader points LD_LIBRARY_PATH at the bundled libraries (Qt, libstdc++,
...). Host programs inherit it and then load those instead of the system ones: KDE's
kde-open, which xdg-open uses on SteamOS, fails with "GLIBCXX_... not found" and the
steam:// link is silently lost. Children must get the environment the user started us with.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

# Variables the bootloader or the Qt runtime hooks may point into the bundle.
BUNDLE_VARS = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
    "PYTHONHOME",
    "PYTHONPATH",
)


def host_env(
    environ: Mapping[str, str] | None = None, frozen: bool | None = None
) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    if not (getattr(sys, "frozen", False) if frozen is None else frozen):
        return env
    bundle = getattr(sys, "_MEIPASS", None)
    for var in BUNDLE_VARS:
        original = env.pop(f"{var}_ORIG", None)
        if original is not None:
            env[var] = original
        elif var in env and (var.startswith("LD_") or bundle is None or bundle in env[var]):
            del env[var]
    return env
