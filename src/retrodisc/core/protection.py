"""Heuristic detection of copy protections that Wine usually cannot handle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SIGNATURES: dict[str, frozenset[str]] = {
    "SafeDisc": frozenset(
        {"00000001.tmp", "clcd16.dll", "clcd32.dll", "clokspl.exe", "dplayerx.dll", "secdrv.sys"}
    ),
    "SecuROM": frozenset({"cms16.dll", "cms_95.dll", "cms32_95.dll", "cmsd.dll"}),
    "LaserLock": frozenset({"laserlok.in", "laserlok.o10", "laserlok.o11"}),
    "StarForce": frozenset({"protect.dll", "protect.exe"}),
}


@dataclass(frozen=True)
class ProtectionHit:
    name: str
    files: tuple[str, ...]


def scan(cd_root: Path, max_depth: int = 3) -> list[ProtectionHit]:
    """Look for well-known protection files near the root of an extracted CD."""
    names: set[str] = set()
    base_depth = len(cd_root.parts)
    for path in cd_root.rglob("*"):
        if len(path.parts) - base_depth > max_depth:
            continue
        names.add(path.name.lower())
    return [
        ProtectionHit(name=prot, files=tuple(sorted(sig & names)))
        for prot, sig in SIGNATURES.items()
        if sig & names
    ]
