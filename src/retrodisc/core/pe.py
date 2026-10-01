"""Minimal reader for Windows PE executables: just what protection detection needs."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MAX_SECTIONS = 96
MAX_IMPORTS = 512


@dataclass(frozen=True)
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int


@dataclass
class PEFile:
    path: Path
    data: bytes
    sections: list[Section]
    entry_rva: int
    overlay_offset: int  # where data appended after the last section starts
    imports: list[str] = field(default_factory=list)  # imported DLL names, lower case

    @property
    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]

    def has_section(self, name: str) -> bool:
        return any(s.name.lower() == name.lower() for s in self.sections)

    @property
    def overlay(self) -> bytes:
        return self.data[self.overlay_offset :] if self.overlay_offset < len(self.data) else b""

    def rva_to_offset(self, rva: int) -> int | None:
        for s in self.sections:
            size = max(s.virtual_size, s.raw_size)
            if s.virtual_address <= rva < s.virtual_address + size:
                offset = s.raw_offset + rva - s.virtual_address
                return offset if offset < len(self.data) else None
        return None

    def byte_at_rva(self, rva: int) -> int | None:
        offset = self.rva_to_offset(rva)
        return self.data[offset] if offset is not None else None

    def int_at_rva(self, rva: int, size: int) -> int | None:
        offset = self.rva_to_offset(rva)
        if offset is None or offset + size > len(self.data):
            return None
        fmt = {1: "<b", 4: "<i"}[size]
        value: int = struct.unpack_from(fmt, self.data, offset)[0]
        return value

    def string_at(self, offset: int, limit: int = 64) -> str:
        chunk = self.data[offset : offset + limit]
        return chunk.split(b"\0", 1)[0].decode("latin-1", errors="replace")

    def matches_at_entry(self, pattern: str) -> bool:
        """DIE's compareEP(): hex bytes, ".." any byte, "$$"/"$$$$$$$$" follow a rel8/rel32 jump."""
        return match_pattern(self, self.entry_rva, pattern)


def parse(path: Path, max_size: int = 64 * 1024 * 1024) -> PEFile | None:
    """Parse `path`, or return None if it is not a (sane) PE file."""
    try:
        if path.stat().st_size > max_size:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    try:
        return _parse(path, data)
    except (struct.error, IndexError, ValueError):
        return None


def _parse(path: Path, data: bytes) -> PEFile | None:
    if len(data) < 64 or data[:2] != b"MZ":
        return None
    (pe_offset,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        return None
    count, opt_size = (
        struct.unpack_from("<H", data, pe_offset + 6)[0],
        struct.unpack_from("<H", data, pe_offset + 20)[0],
    )
    opt = pe_offset + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    (entry_rva,) = struct.unpack_from("<I", data, opt + 16)
    dirs_at = opt + (96 if magic == 0x10B else 112)
    (dir_count,) = struct.unpack_from("<I", data, dirs_at - 4)

    sections: list[Section] = []
    table = opt + opt_size
    for i in range(min(count, MAX_SECTIONS)):
        at = table + i * 40
        raw_name = data[at : at + 8]
        vsize, vaddr, rsize, roff = struct.unpack_from("<IIII", data, at + 8)
        sections.append(
            Section(raw_name.rstrip(b"\0").decode("latin-1"), vaddr, vsize, roff, rsize)
        )
    ends = [s.raw_offset + s.raw_size for s in sections if s.raw_size]
    overlay_offset = max(ends) if ends else len(data)

    pe = PEFile(path, data, sections, entry_rva, overlay_offset)
    if dir_count > 1:
        import_rva = struct.unpack_from("<I", data, dirs_at + 8)[0]
        pe.imports = _read_imports(pe, import_rva)
    return pe


def _read_imports(pe: PEFile, import_rva: int) -> list[str]:
    names: list[str] = []
    offset = pe.rva_to_offset(import_rva) if import_rva else None
    while offset is not None and len(names) < MAX_IMPORTS and offset + 20 <= len(pe.data):
        name_rva = struct.unpack_from("<I", pe.data, offset + 12)[0]
        if name_rva == 0:
            break
        name_offset = pe.rva_to_offset(name_rva)
        if name_offset is not None:
            names.append(pe.string_at(name_offset).lower())
        offset += 20
    return names


def match_pattern(pe: PEFile, rva: int, pattern: str) -> bool:
    """Match a DIE byte pattern starting at `rva`, following relative jumps like DIE does."""
    p = pattern.replace(" ", "").lower()
    i = 0
    while i < len(p):
        if p.startswith("$$$$$$$$", i):
            rel = pe.int_at_rva(rva, 4)
            if rel is None:
                return False
            rva = rva + 4 + rel
            i += 8
        elif p.startswith("$$", i):
            rel = pe.int_at_rva(rva, 1)
            if rel is None:
                return False
            rva = rva + 1 + rel
            i += 2
        else:
            token = p[i : i + 2]
            byte = pe.byte_at_rva(rva)
            if byte is None or len(token) < 2:
                return False
            if token != ".." and int(token, 16) != byte:
                return False
            rva += 1
            i += 2
    return True
