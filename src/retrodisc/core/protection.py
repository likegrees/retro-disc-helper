"""Detection of copy protections that Wine/Proton usually cannot handle.

Two sources of evidence:
- telltale files on the disc (older SafeDisc/SecuROM/LaserLock/StarForce);
- the game's .exe itself. The rules in `detect_executable` are ported from the
  protector signatures of Detect It Easy (https://github.com/horsicq/Detect-It-Easy,
  MIT license; rule authors ELF_7719116, DosX, hypn0, horsicq), plus section names
  added by the wrappers (e.g. SecuROM 4's .cms_t/.cms_d).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from retrodisc.core import pe as pe_reader

SIGNATURES: dict[str, frozenset[str]] = {
    "SafeDisc": frozenset(
        {"00000001.tmp", "clcd16.dll", "clcd32.dll", "clokspl.exe", "dplayerx.dll", "secdrv.sys"}
    ),
    "SecuROM": frozenset({"cms16.dll", "cms_95.dll", "cms32_95.dll", "cmsd.dll"}),
    "LaserLock": frozenset({"laserlok.in", "laserlok.o10", "laserlok.o11"}),
    "StarForce": frozenset({"protect.dll", "protect.exe"}),
    "TAGES": frozenset(),
}


@dataclass(frozen=True)
class Detection:
    name: str
    version: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}".strip()


@dataclass(frozen=True)
class ProtectionHit:
    name: str
    files: tuple[str, ...]  # telltale files, or the protected .exe names
    version: str = ""  # when an .exe told us

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}".strip()


# DIE: protector_StarForce.2.sg (entry point patterns, in the same order)
STARFORCE_ENTRY_POINTS = (
    ("68........ff25....63", "3.0"),
    ("68........ff25....57", "1.1 ProActive"),
    (
        "5768..0d01006800....00e850..ffff68......0068......0068......0068......0068......00",
        "",  # protection driver
    ),
    ("e8........000000000000", "3.X"),
    ("68........ff25........0000000000", "3.X"),
)
SAFEDISC_ENTRY_POINT = "558bec60bb........33c98a0d........85c974..b8........2bc383e8..eb"
LASERLOK_ENTRY_POINT = (
    "eb$$eb$$5055e8$$$$$$$$5d508bc581ed........2d........3e2b85........3e8985........"
    "608d85........508d9d........2bd853"
)
TAGES_ENTRY_POINT = (
    "8925........e8$$$$$$$$6a..6a..c705................e8$$$$$$$$8b4424..0faf4424..506a.."
    "ff15........50ff15........c3"
)


def detect_executable(path: Path) -> list[Detection]:
    """Protections wrapped around this .exe, with their version when it can be read."""
    pe = pe_reader.parse(path)
    if pe is None:
        return []
    found: list[Detection] = []

    # SecuROM (DIE: protector_SecuROM.2.sg)
    if pe.sections and pe.sections[-1].name == ".securom":
        found.append(Detection("SecuROM", "pre-8.03.03"))
    elif pe.has_section(".dsstext"):
        found.append(Detection("SecuROM", "8.03.03+"))
    elif pe.overlay.startswith(b"AddD\x03"):
        found.append(Detection("SecuROM", pe.string_at(pe.overlay_offset + 8, 16)))
    elif pe.has_section(".cms_t") or pe.has_section(".cms_d"):
        found.append(Detection("SecuROM", "4.x"))

    # SafeDisc (DIE: protector_Safedisc.2.sg; stxt sections from SafeDisc 2+)
    if (
        pe.matches_at_entry(SAFEDISC_ENTRY_POINT)
        or pe.has_section("stxt774")
        or (pe.has_section("stxt371"))
    ):
        found.append(Detection("SafeDisc"))

    # StarForce (DIE: protector_StarForce.2.sg)
    starforce = next(
        (v for pattern, v in STARFORCE_ENTRY_POINTS if pe.matches_at_entry(pattern)), None
    )
    if (
        starforce is None
        and pe.matches_at_entry("60e8000000005883c008")
        and pe.has_section(".brick")
    ):
        starforce = "3.4"
    if starforce is None and ("protect.dll" in pe.imports or pe.has_section(".ps4")):
        starforce = "4.X-5.X" if pe.has_section(".ps4") else "3.X"
    if starforce is None and (pe.has_section(".sforce") or pe.has_section(".sforce3")):
        starforce = "3.X"
    if starforce is not None:
        found.append(Detection("StarForce", starforce))

    # LaserLok and TAGES (DIE: protector_Laserlok.2.sg, protector_Tages.2.sg)
    if pe.matches_at_entry(LASERLOK_ENTRY_POINT):
        found.append(Detection("LaserLock"))
    if pe.matches_at_entry(TAGES_ENTRY_POINT):
        found.append(Detection("TAGES"))
    return found


def scan_executable(path: Path) -> list[str]:
    """Names of the protections wrapped around this .exe."""
    return [d.name for d in detect_executable(path)]


def merge(hits: Iterable[ProtectionHit]) -> list[ProtectionHit]:
    """Combine hits from several folders (all discs, the installed game) per protection."""
    files: dict[str, set[str]] = {}
    versions: dict[str, str] = {}
    for hit in hits:
        files.setdefault(hit.name, set()).update(hit.files)
        if hit.version and not versions.get(hit.name):
            versions[hit.name] = hit.version
    return [
        ProtectionHit(name, tuple(sorted(files[name])), versions.get(name, ""))
        for name in SIGNATURES
        if name in files
    ]


def scan(root: Path, max_depth: int = 3, extra_exes: Iterable[Path] = ()) -> list[ProtectionHit]:
    """Look for protection files and protected .exe files near the root of a folder."""
    found: dict[str, set[str]] = {}
    versions: dict[str, str] = {}
    base_depth = len(root.parts)
    exes = list(extra_exes)
    for path in root.rglob("*") if root.is_dir() else ():
        if len(path.parts) - base_depth > max_depth:
            continue
        name = path.name.lower()
        for prot, sig in SIGNATURES.items():
            if name in sig:
                found.setdefault(prot, set()).add(name)
        if name.endswith(".exe") and path.is_file() and path not in exes:
            exes.append(path)
    for exe in exes:
        for detection in detect_executable(exe):
            found.setdefault(detection.name, set()).add(exe.name)
            if detection.version and not versions.get(detection.name):
                versions[detection.name] = detection.version
    return [
        ProtectionHit(name=prot, files=tuple(sorted(found[prot])), version=versions.get(prot, ""))
        for prot in SIGNATURES
        if prot in found
    ]
