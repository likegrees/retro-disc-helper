"""Wine prefix tweaks: expose the extracted CD as drive S: with the original label/serial.

Wine maps a Unix path to the deepest drive whose root contains it, so once S: points at the
extracted CD folder a Steam shortcut targeting an .exe inside that folder runs from S:\\.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from retrodisc.core.iso import VolumeInfo

CD_LETTER = "s"  # D:/E: get reassigned to the microSD by Wine on every start
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


def setup_cd_drive(pfx: Path, cd_dir: Path, volume: VolumeInfo) -> None:
    """Link S: to `cd_dir`, write label/serial files, mark S: as a CD-ROM in the registry.

    The game must not be running (Wine rewrites system.reg on exit).
    """
    if not (pfx / "system.reg").exists():
        raise PrefixError(
            "The Wine prefix does not exist yet. Launch the game once from Steam, then close it."
        )
    insert_disc(pfx, cd_dir, volume)
    set_registry_drive_type(pfx, "cdrom")


def write_volume_files(cd_dir: Path, volume: VolumeInfo) -> None:
    """The label and serial Wine reports for a drive whose root is `cd_dir`."""
    (cd_dir / LABEL_FILE).write_text(volume.label)
    (cd_dir / SERIAL_FILE).write_text(volume.serial)


def insert_disc(pfx: Path, cd_dir: Path, volume: VolumeInfo) -> None:
    """Point S: at `cd_dir`, like putting that disc in the drive. Safe while the game runs."""
    if not cd_dir.is_dir():
        raise PrefixError(f"CD folder not found: {cd_dir}")
    if not (pfx / "dosdevices").is_dir():
        raise PrefixError(
            "The Wine prefix does not exist yet. Launch the game once from Steam, then close it."
        )
    write_volume_files(cd_dir, volume)
    link = drive_link(pfx)
    tmp = link.with_name("s:.new")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(cd_dir.resolve(), target_is_directory=True)
    tmp.replace(link)  # atomic: S: is never missing while a game polls it


# ---- system.reg (HKEY_LOCAL_MACHINE) editing ------------------------------------------


def _section_bounds(lines: list[str], key: str) -> tuple[int, int] | None:
    header = f"[{key}]".lower()
    for i, line in enumerate(lines):
        if line.lower().startswith(header):
            end = i + 1
            while end < len(lines) and not lines[end].startswith("["):
                end += 1
            return i, end
    return None


def registry_drive_type(pfx: Path, letter: str = CD_LETTER) -> str | None:
    try:
        lines = (pfx / "system.reg").read_text(errors="replace").splitlines()
    except OSError:
        return None
    bounds = _section_bounds(lines, DRIVES_KEY)
    if bounds is None:
        return None
    pattern = re.compile(rf'^"{letter}:"="([^"]*)"', re.IGNORECASE)
    for line in lines[bounds[0] + 1 : bounds[1]]:
        if m := pattern.match(line):
            return m.group(1)
    return None


def set_registry_drive_type(pfx: Path, drive_type: str, letter: str = CD_LETTER) -> None:
    reg = pfx / "system.reg"
    text = reg.read_text(errors="replace")
    lines = text.splitlines()
    value = f'"{letter}:"="{drive_type}"'
    pattern = re.compile(rf'^"{letter}:"=', re.IGNORECASE)
    bounds = _section_bounds(lines, DRIVES_KEY)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{DRIVES_KEY}] {int(time.time())}", value, ""]
    else:
        start, end = bounds
        body = [line for line in lines[start + 1 : end] if not pattern.match(line)]
        # Keep the trailing blank line that separates sections.
        while body and not body[-1].strip():
            body.pop()
        lines[start + 1 : end] = [*body, value, ""]
    reg.write_text("\n".join(lines) + "\n")
