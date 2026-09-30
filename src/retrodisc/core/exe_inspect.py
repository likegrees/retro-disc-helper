"""Windows executable inspection: 16-bit detection and installer discovery on a CD."""

from __future__ import annotations

import struct
from enum import StrEnum
from pathlib import Path

INSTALLER_NAMES = ("setup.exe", "install.exe", "installer.exe")
INSTALLER_SUBDIRS = ("", "install", "setup", "win95", "win9x", "win32")
LAUNCHER_NAMES = ("autorun.exe", "autoplay.exe")


class ExeKind(StrEnum):
    DOS = "dos"
    WIN16 = "win16"  # NE: Proton cannot run it
    WIN32 = "win32"  # PE
    UNKNOWN = "unknown"


def exe_kind(path: Path) -> ExeKind:
    try:
        with path.open("rb") as f:
            header = f.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return ExeKind.UNKNOWN
            (e_lfanew,) = struct.unpack_from("<I", header, 0x3C)
            f.seek(e_lfanew)
            sig = f.read(4)
    except OSError:
        return ExeKind.UNKNOWN
    if sig == b"PE\x00\x00":
        return ExeKind.WIN32
    if sig[:2] == b"NE":
        return ExeKind.WIN16
    return ExeKind.DOS


def _children_ci(folder: Path) -> dict[str, Path]:
    try:
        return {p.name.lower(): p for p in folder.iterdir()}
    except OSError:
        return {}


def find_installers(cd_root: Path) -> list[Path]:
    """Installer candidates, best first. autorun-style launchers come last."""
    found: list[Path] = []
    root_children = _children_ci(cd_root)
    for sub in INSTALLER_SUBDIRS:
        folder = root_children.get(sub) if sub else cd_root
        if folder is None or not folder.is_dir():
            continue
        children = _children_ci(folder)
        found += [children[n] for n in INSTALLER_NAMES if n in children]
    found += [root_children[n] for n in LAUNCHER_NAMES if n in root_children]
    return found


def find_exes(folder: Path, newer_than: float | None = None) -> list[Path]:
    """All .exe files below `folder`, largest first, optionally only recently created ones."""
    exes = [
        p
        for p in folder.rglob("*")
        if p.suffix.lower() == ".exe"
        and p.is_file()
        and (newer_than is None or p.stat().st_mtime >= newer_than)
    ]
    uninstall = ("unins", "uninst", "setup", "install", "vcredist", "dxsetup", "directx")

    def rank(p: Path) -> tuple[int, int, int]:
        name = p.name.lower()
        in_program_files = any(part.lower().startswith("program files") for part in p.parts)
        return (
            1 if any(name.startswith(u) for u in uninstall) else 0,
            0 if in_program_files else 1,
            -p.stat().st_size,
        )

    return sorted(exes, key=rank)
