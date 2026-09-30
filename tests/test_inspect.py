from __future__ import annotations

import struct
from pathlib import Path

from retrodisc.core.exe_inspect import ExeKind, exe_kind, find_exes, find_installers
from retrodisc.core.protection import scan


def mz(sig: bytes) -> bytes:
    header = bytearray(b"MZ" + b"\x00" * 62)
    struct.pack_into("<I", header, 0x3C, 64)
    return bytes(header) + sig + b"\x00" * 64


def test_exe_kind(tmp_path: Path) -> None:
    cases = {"pe.exe": mz(b"PE\x00\x00"), "ne.exe": mz(b"NE\x05\x0a"), "dos.exe": mz(b"\x00" * 4)}
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
    assert exe_kind(tmp_path / "pe.exe") is ExeKind.WIN32
    assert exe_kind(tmp_path / "ne.exe") is ExeKind.WIN16
    assert exe_kind(tmp_path / "dos.exe") is ExeKind.DOS
    (tmp_path / "txt.exe").write_text("nope")
    assert exe_kind(tmp_path / "txt.exe") is ExeKind.UNKNOWN


def test_find_installers(tmp_path: Path) -> None:
    (tmp_path / "AUTORUN.EXE").write_bytes(b"")
    (tmp_path / "Install").mkdir()
    (tmp_path / "Install" / "Setup.exe").write_bytes(b"")
    assert [p.name for p in find_installers(tmp_path)] == ["Setup.exe", "AUTORUN.EXE"]


def test_find_exes_ranking(tmp_path: Path) -> None:
    game = tmp_path / "Program Files" / "Game"
    game.mkdir(parents=True)
    (game / "game.exe").write_bytes(b"x" * 100)
    (game / "unins000.exe").write_bytes(b"x" * 1000)
    (tmp_path / "other.exe").write_bytes(b"x" * 500)
    assert [p.name for p in find_exes(tmp_path)] == ["game.exe", "other.exe", "unins000.exe"]


def test_protection_scan(tmp_path: Path) -> None:
    (tmp_path / "CLCD32.DLL").write_bytes(b"")
    (tmp_path / "00000001.TMP").write_bytes(b"")
    hits = scan(tmp_path)
    assert [h.name for h in hits] == ["SafeDisc"]
    assert hits[0].files == ("00000001.tmp", "clcd32.dll")
