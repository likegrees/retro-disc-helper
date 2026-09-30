"""ISO inspection and extraction: volume label, Wine-compatible serial, file extraction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pycdlib

from retrodisc.core.cue import USER_DATA_SIZE

PVD_SECTOR = 16

# Called with (files_done, files_total, current_path); return False to cancel.
ExtractProgress = Callable[[int, int, str], bool]


class IsoError(Exception):
    pass


@dataclass(frozen=True)
class VolumeInfo:
    label: str
    serial: str


def read_pvd(iso: Path) -> bytes:
    with iso.open("rb") as f:
        f.seek(PVD_SECTOR * USER_DATA_SIZE)
        pvd = f.read(USER_DATA_SIZE)
    if len(pvd) != USER_DATA_SIZE or pvd[1:6] != b"CD001":
        raise IsoError(f"{iso.name} is not an ISO9660 image")
    return pvd


def volume_info(iso: Path) -> VolumeInfo:
    """Label and serial exactly as Wine derives them for a real CD (see the guide)."""
    pvd = read_pvd(iso)
    label = pvd[40:72].decode("ascii", errors="replace").strip()
    sums = [0, 0, 0, 0]
    for i in range(0, USER_DATA_SIZE, 4):
        for j in range(4):
            sums[j] = (sums[j] + pvd[i + j]) & 0xFF
    return VolumeInfo(label=label, serial="".join(f"{b:02x}" for b in sums))


def _open(iso: Path) -> tuple[pycdlib.PyCdlib, str]:
    """Open the image and pick the richest namespace available."""
    cd = pycdlib.PyCdlib()
    try:
        cd.open(str(iso))
    except pycdlib.pycdlibexception.PyCdlibException as exc:
        raise IsoError(f"Cannot read {iso.name}: {exc}") from exc
    if cd.has_udf():
        return cd, "udf_path"
    if cd.has_joliet():
        return cd, "joliet_path"
    if cd.has_rock_ridge():
        return cd, "rr_path"
    return cd, "iso_path"


def _clean(name: str, facade: str) -> str:
    if facade == "iso_path":
        name = name.split(";", 1)[0]
        if name.endswith("."):
            name = name[:-1]
    return name


def _walk(cd: pycdlib.PyCdlib, facade: str) -> list[tuple[str, list[str], list[str]]]:
    return list(cd.walk(**{facade: "/"}))


def list_files(iso: Path) -> list[str]:
    cd, facade = _open(iso)
    try:
        return [
            str(PurePosixPath(root, _clean(f, facade)))
            for root, _dirs, files in _walk(cd, facade)
            for f in files
        ]
    finally:
        cd.close()


def extract(iso: Path, dest: Path, progress: ExtractProgress | None = None) -> Path:
    """Extract every file of `iso` into `dest`, keeping the directory structure."""
    cd, facade = _open(iso)
    try:
        entries = [(root, name) for root, _dirs, files in _walk(cd, facade) for name in files]
        dest.mkdir(parents=True, exist_ok=True)
        for done, (root, name) in enumerate(entries, start=1):
            rel = PurePosixPath(root.lstrip("/"), _clean(name, facade))
            target = dest.joinpath(*[_clean(p, facade) for p in rel.parts])
            if not target.resolve().is_relative_to(dest.resolve()):
                raise IsoError(f"Unsafe path in image: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = str(PurePosixPath(root, name))
            with target.open("wb") as out:
                cd.get_file_from_iso_fp(out, **{facade: source})
            if progress is not None and not progress(done, len(entries), str(rel)):
                raise IsoError("Extraction cancelled")
    finally:
        cd.close()
    return dest
