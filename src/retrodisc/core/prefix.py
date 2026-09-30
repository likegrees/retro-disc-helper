"""Wine prefix tweaks: expose the extracted CD as drive R: with the original label/serial.

Wine maps a Unix path to the deepest drive whose root contains it, so once R: points at the
extracted CD folder a Steam shortcut targeting an .exe inside that folder runs from R:\\.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from retrodisc.core.iso import VolumeInfo

# Proton deletes S: and T: on every launch (its "game drive" and "steam drive" options) and
# Wine gives removable devices the first free letter from C:/D: upward (the microSD is D:).
CD_LETTER = "r"
LEGACY_CD_LETTERS = ("s",)  # used by earlier versions of this app
LABEL_FILE = ".windows-label"
SERIAL_FILE = ".windows-serial"
DRIVES_KEY = r"Software\\Wine\\Drives"


class PrefixError(Exception):
    pass


@dataclass(frozen=True)
class CdDriveStatus:
    prefix_exists: bool
    link_target: Path | None
    link_ok: bool
    label: str | None
    serial: str | None
    registry_ok: bool

    @property
    def ok(self) -> bool:
        return (
            self.prefix_exists
            and self.link_ok
            and self.label is not None
            and self.serial is not None
            and self.registry_ok
        )


def drive_link(pfx: Path, letter: str = CD_LETTER) -> Path:
    return pfx / "dosdevices" / f"{letter}:"


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def cd_drive_status(pfx: Path, cd_dir: Path | None = None) -> CdDriveStatus:
    link = drive_link(pfx)
    target = Path(str(link.readlink())) if link.is_symlink() else None
    link_ok = target is not None and target.is_dir()
    if cd_dir is not None and target is not None:
        link_ok = link_ok and target.resolve() == cd_dir.resolve()
    folder = target if target is not None else cd_dir
    return CdDriveStatus(
        prefix_exists=(pfx / "system.reg").exists(),
        link_target=target,
        link_ok=link_ok,
        label=_read_optional(folder / LABEL_FILE) if folder else None,
        serial=_read_optional(folder / SERIAL_FILE) if folder else None,
        registry_ok=registry_drive_type(pfx) == "cdrom",
    )


def setup_cd_drive(
    pfx: Path, cd_dir: Path, volume: VolumeInfo, other_cd_dirs: list[Path] | None = None
) -> None:
    """Link R: to `cd_dir`, write label/serial files, mark R: as a CD-ROM in the registry.

    The game must not be running (Wine rewrites system.reg on exit).
    """
    if not (pfx / "system.reg").exists():
        raise PrefixError(
            "The Wine prefix does not exist yet. Launch the game once from Steam, then close it."
        )
    insert_disc(pfx, cd_dir, volume)
    set_registry_drive_type(pfx, "cdrom")
    remove_legacy_drives(pfx)
    repoint_install_paths(pfx, [cd_dir, *(other_cd_dirs or [])])


def remove_legacy_drives(pfx: Path) -> None:
    """Drop the S: drive earlier versions created: Proton deletes its link at every launch."""
    for letter in LEGACY_CD_LETTERS:
        if registry_drive_type(pfx, letter) == "cdrom":
            write_reg_value(pfx / "system.reg", DRIVES_KEY, f"{letter}:", None)


def _windows_forms(cd_dir: Path) -> list[str]:
    """The CD folder as Windows programs saw it through Z: (plain and symlink-resolved)."""
    paths = {str(cd_dir.absolute()), str(cd_dir.resolve())}
    return ["Z:" + p.replace("/", "\\") for p in paths if p.isascii()]


def repoint_install_paths(pfx: Path, cd_dirs: list[Path], dry_run: bool = False) -> int:
    """Rewrite registry paths into the CD folder via Z: (e.g. "InstallSource") to the CD drive.

    Installers record where they ran from. When that was the extracted folder, the game later
    looks for its disc on Z:, which is not a CD drive. Returns how many values (would) change.
    """
    drive = f"{CD_LETTER.upper()}:\\\\"  # R:\\ as written in .reg files
    patterns = [
        re.compile(
            r'(?<=")' + re.escape(form.replace("\\", "\\\\")) + r'(?:\\\\|(?="))',
            re.IGNORECASE,
        )
        for cd_dir in cd_dirs
        for form in _windows_forms(cd_dir)
    ]
    total = 0
    for reg in (pfx / "system.reg", pfx / "user.reg"):
        if not reg.exists():
            continue
        text = _read_reg(reg)
        changed = text
        for pattern in patterns:
            changed, count = pattern.subn(lambda _m: drive, changed)
            total += count
        if changed != text and not dry_run:
            _write_reg(reg, changed)
    return total


def write_volume_files(cd_dir: Path, volume: VolumeInfo) -> None:
    """The label and serial Wine reports for a drive whose root is `cd_dir`."""
    (cd_dir / LABEL_FILE).write_text(volume.label)
    (cd_dir / SERIAL_FILE).write_text(volume.serial)


def insert_disc(pfx: Path, cd_dir: Path, volume: VolumeInfo) -> None:
    """Point R: at `cd_dir`, like putting that disc in the drive. Safe while the game runs."""
    if not cd_dir.is_dir():
        raise PrefixError(f"CD folder not found: {cd_dir}")
    if not (pfx / "dosdevices").is_dir():
        raise PrefixError(
            "The Wine prefix does not exist yet. Launch the game once from Steam, then close it."
        )
    write_volume_files(cd_dir, volume)
    link = drive_link(pfx)
    tmp = link.with_name(f"{CD_LETTER}:.new")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(cd_dir.resolve(), target_is_directory=True)
    tmp.replace(link)  # atomic: R: is never missing while a game polls it


# ---- Windows version reported to programs ---------------------------------------------

WINE_KEY = r"Software\\Wine"
# Wine's names, newest first. None = Proton's default (Windows 10).
WINDOWS_VERSIONS: dict[str, str] = {
    "win10": "Windows 10",
    "win7": "Windows 7",
    "winxp": "Windows XP",
    "win2k": "Windows 2000",
    "win98": "Windows 98",
}


def windows_version(pfx: Path) -> str | None:
    """The version set for the whole prefix, or None when Proton's default applies."""
    return read_reg_value(pfx / "user.reg", WINE_KEY, "Version")


def set_windows_version(pfx: Path, version: str | None) -> None:
    """Make every program in the prefix see `version` (None: back to Proton's default).

    The game must not be running (Wine rewrites user.reg on exit).
    """
    if version is not None and version not in WINDOWS_VERSIONS:
        raise PrefixError(f"Unknown Windows version: {version}")
    reg = pfx / "user.reg"
    if not reg.exists():
        raise PrefixError(
            "The Wine prefix does not exist yet. Launch the game once from Steam, then close it."
        )
    write_reg_value(reg, WINE_KEY, "Version", version)


# ---- registry file editing (system.reg = HKLM, user.reg = HKCU) --------------------------


def _read_reg(reg: Path) -> str:
    # surrogateescape keeps any non-UTF-8 byte intact when the file is written back.
    return reg.read_text(encoding="utf-8", errors="surrogateescape")


def _write_reg(reg: Path, text: str) -> None:
    tmp = reg.with_name(reg.name + ".retrodisc-tmp")
    tmp.write_text(text, encoding="utf-8", errors="surrogateescape")
    tmp.replace(reg)


def _section_bounds(lines: list[str], key: str) -> tuple[int, int] | None:
    header = f"[{key}]".lower()
    for i, line in enumerate(lines):
        if line.lower().startswith(header):
            end = i + 1
            while end < len(lines) and not lines[end].startswith("["):
                end += 1
            return i, end
    return None


def _value_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf'^"{re.escape(name)}"=', re.IGNORECASE)


def read_reg_value(reg: Path, key: str, name: str) -> str | None:
    try:
        lines = _read_reg(reg).splitlines()
    except OSError:
        return None
    bounds = _section_bounds(lines, key)
    if bounds is None:
        return None
    pattern = re.compile(rf'^"{re.escape(name)}"="([^"]*)"', re.IGNORECASE)
    for line in lines[bounds[0] + 1 : bounds[1]]:
        if m := pattern.match(line):
            return m.group(1)
    return None


def write_reg_value(reg: Path, key: str, name: str, value: str | None) -> None:
    """Set a string value (or delete it when `value` is None) in a Wine registry file."""
    lines = _read_reg(reg).splitlines()
    entry = [f'"{name}"="{value}"'] if value is not None else []
    bounds = _section_bounds(lines, key)
    if bounds is None:
        if not entry:
            return
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{key}] {int(time.time())}", *entry, ""]
    else:
        start, end = bounds
        pattern = _value_pattern(name)
        body = [line for line in lines[start + 1 : end] if not pattern.match(line)]
        # Keep the trailing blank line that separates sections.
        while body and not body[-1].strip():
            body.pop()
        lines[start + 1 : end] = [*body, *entry, ""]
    _write_reg(reg, "\n".join(lines) + "\n")


def registry_drive_type(pfx: Path, letter: str = CD_LETTER) -> str | None:
    return read_reg_value(pfx / "system.reg", DRIVES_KEY, f"{letter}:")


def set_registry_drive_type(pfx: Path, drive_type: str, letter: str = CD_LETTER) -> None:
    write_reg_value(pfx / "system.reg", DRIVES_KEY, f"{letter}:", drive_type)
