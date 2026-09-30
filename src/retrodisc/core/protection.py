"""Heuristic detection of copy protections that Wine usually cannot handle."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from retrodisc.core import exe_inspect

SIGNATURES: dict[str, frozenset[str]] = {
    "SafeDisc": frozenset(
        {"00000001.tmp", "clcd16.dll", "clcd32.dll", "clokspl.exe", "dplayerx.dll", "secdrv.sys"}
    ),
    "SecuROM": frozenset({"cms16.dll", "cms_95.dll", "cms32_95.dll", "cmsd.dll"}),
    "LaserLock": frozenset({"laserlok.in", "laserlok.o10", "laserlok.o11"}),
    "StarForce": frozenset({"protect.dll", "protect.exe"}),
}


# Sections the protection wrappers add to the game's own .exe. Later versions leave no
# telltale files on the disc, only these (e.g. SecuROM 4 in Le Mans 24 Hours: .cms_t/.cms_d).
SECTION_SIGNATURES: dict[str, frozenset[str]] = {
    "SafeDisc": frozenset({"stxt774", "stxt371"}),
    "SecuROM": frozenset({".cms_t", ".cms_d", ".securom"}),
    "StarForce": frozenset({".sforce", ".sforce3"}),
}


@dataclass(frozen=True)
class ProtectionHit:
    name: str
    files: tuple[str, ...]  # telltale files, or the protected .exe names


def scan_executable(path: Path) -> list[str]:
    """Protections wrapped around this .exe, from its section names."""
    sections = {s.lower() for s in exe_inspect.pe_sections(path)}
    return [prot for prot, sig in SECTION_SIGNATURES.items() if sig & sections]


def scan(root: Path, max_depth: int = 3, extra_exes: Iterable[Path] = ()) -> list[ProtectionHit]:
    """Look for protection files and protected .exe files near the root of a folder."""
    found: dict[str, set[str]] = {}
    base_depth = len(root.parts)
    exes = list(extra_exes)
    for path in root.rglob("*") if root.is_dir() else ():
        if len(path.parts) - base_depth > max_depth:
            continue
        name = path.name.lower()
        for prot, sig in SIGNATURES.items():
            if name in sig:
                found.setdefault(prot, set()).add(name)
        if name.endswith(".exe") and path.is_file():
            exes.append(path)
    for exe in exes:
        for prot in scan_executable(exe):
            found.setdefault(prot, set()).add(exe.name)
    return [
        ProtectionHit(name=prot, files=tuple(sorted(found[prot])))
        for prot in SIGNATURES
        if prot in found
    ]
