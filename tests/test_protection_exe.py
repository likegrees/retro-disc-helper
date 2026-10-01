"""Protections embedded in the game's .exe (e.g. SecuROM 4 in Le Mans 24 Hours)."""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from retrodisc.core.diagnostics import Status, run_checks
from retrodisc.core.library import Game, Library, Step
from retrodisc.core.pe import parse
from retrodisc.core.prefix import CD_LETTER, repoint_install_paths
from retrodisc.core.protection import detect_executable, scan
from retrodisc.core.steam import Steam
from retrodisc.ui.wizard.game_wizard import GameWizard
from retrodisc.ui.wizard.pages import FinalizePage


def make_pe(
    path: Path,
    sections: list[str] | None = None,
    code: bytes = b"\xc3",
    overlay: bytes = b"",
    imports: list[str] = (),  # type: ignore[assignment]
) -> Path:
    """A small but valid PE32: code at the entry point, optional imports and overlay."""
    path.parent.mkdir(parents=True, exist_ok=True)
    names = [".text", *(sections or [".data"])]
    if imports:
        names.append(".idata")
    count, opt_size = len(names), 224
    headers = 0x400
    raw: list[bytes] = [code.ljust(0x200, b"\0")]
    idata = b""
    if imports:
        base = 0x1000 * (count)  # rva of .idata
        descr = b""
        strings = b""
        str_at = 20 * (len(imports) + 1)
        for dll in imports:
            descr += struct.pack("<IIIII", 0, 0, 0, base + str_at + len(strings), 0)
            strings += dll.encode() + b"\0"
        idata = descr + b"\0" * 20 + strings
    for name in names[1:]:
        raw.append((idata if name == ".idata" else b"\0").ljust(0x200, b"\0"))
    dos = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", dos, 0x3C, 64)
    coff = struct.pack("<HHIIIHH", 0x14C, count, 0, 0, 0, opt_size, 0x102)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 16, 0x1000)  # entry point = start of .text
    struct.pack_into("<I", opt, 92, 16)  # number of data directories
    if imports:
        struct.pack_into("<II", opt, 96 + 8, 0x1000 * count, len(idata))
    table = b""
    for n, name in enumerate(names):
        table += (
            name.encode().ljust(8, b"\0")
            + struct.pack("<IIII", 0x200, 0x1000 * (n + 1), 0x200, headers + 0x200 * n)
            + b"\0" * 16
        )
    head = (bytes(dos) + b"PE\0\0" + coff + bytes(opt) + table).ljust(headers, b"\0")
    path.write_bytes(head + b"".join(raw) + overlay)
    return path


def test_pe_reader(tmp_path: Path) -> None:
    exe = make_pe(
        tmp_path / "a.exe",
        [".cms_t", ".cms_d"],
        code=b"\x55\x8b\xec",
        overlay=b"XYZ",
        imports=["KERNEL32.dll", "protect.dll"],
    )
    pe = parse(exe)
    assert pe is not None
    assert pe.section_names == [".text", ".cms_t", ".cms_d", ".idata"]
    assert pe.overlay == b"XYZ"
    assert pe.imports == ["kernel32.dll", "protect.dll"]
    assert pe.matches_at_entry("558b..")
    assert not pe.matches_at_entry("558bed")
    (tmp_path / "notes.exe").write_text("not a program")
    assert parse(tmp_path / "notes.exe") is None


def test_securom_version_from_overlay(tmp_path: Path) -> None:
    # As in Le Mans 24 Hours: 'AddD' 03 00 00 00, then the version string.
    exe = make_pe(
        tmp_path / "Lemans.exe",
        [".data1", ".cms_t", ".cms_d"],
        overlay=b"AddD\x03\0\0\x004.68.00\0rest",
    )
    assert [d.label for d in detect_executable(exe)] == ["SecuROM 4.68.00"]
    assert [
        d.label for d in detect_executable(make_pe(tmp_path / "b.exe", [".cms_t", ".cms_d"]))
    ] == ["SecuROM 4.x"]
    assert [
        d.label for d in detect_executable(make_pe(tmp_path / "c.exe", [".data", ".securom"]))
    ] == ["SecuROM pre-8.03.03"]


def test_entry_point_rules_follow_jumps(tmp_path: Path) -> None:
    safedisc = bytes.fromhex("558bec60bb1122334433c98a0d5566778885c97401b899aabbcc2bc383e801eb")
    assert [d.name for d in detect_executable(make_pe(tmp_path / "s.exe", code=safedisc))] == [
        "SafeDisc"
    ]
    # LaserLok starts with two short jumps and a call: the matcher must follow them.
    laserlok = bytearray(0x100)
    laserlok[0:2] = b"\xeb\x02"  # jmp +2 -> 4
    laserlok[4:6] = b"\xeb\x02"  # jmp +2 -> 8
    laserlok[8:11] = b"\x50\x55\xe8"
    laserlok[11:15] = struct.pack("<i", 0x10)  # call -> 15 + 0x10 = 0x1f
    tail = bytes.fromhex(
        "5d508bc581ed112233442d112233443e2b85112233443e898511223344"
        "608d8511223344508d9d112233442bd853"
    )
    laserlok[0x1F : 0x1F + len(tail)] = tail
    assert [
        d.name for d in detect_executable(make_pe(tmp_path / "l.exe", code=bytes(laserlok)))
    ] == ["LaserLock"]


def test_starforce_rules(tmp_path: Path) -> None:
    by_import = make_pe(tmp_path / "sf.exe", imports=["KERNEL32.dll", "protect.dll"])
    assert [d.label for d in detect_executable(by_import)] == ["StarForce 3.X"]
    by_section = make_pe(tmp_path / "sf4.exe", [".ps4"])
    assert [d.label for d in detect_executable(by_section)] == ["StarForce 4.X-5.X"]


def test_clean_program_is_not_flagged(tmp_path: Path) -> None:
    clean = make_pe(
        tmp_path / "game.exe",
        [".rdata", ".data", ".rsrc"],
        code=bytes.fromhex("558bec83ec10e8000000005dc3"),
        imports=["KERNEL32.dll"],
    )
    assert detect_executable(clean) == []


def test_scan_finds_protected_exe_on_disc_and_installed(tmp_path: Path) -> None:
    cd = tmp_path / "cd"
    make_pe(cd / "program files/Game/Lemans.exe", [".text", ".cms_t", ".cms_d"])
    (cd / "setup.exe").write_bytes(b"MZ")
    assert scan(cd) == [scan(cd)[0]]
    assert scan(cd)[0].name == "SecuROM" and scan(cd)[0].files == ("Lemans.exe",)

    # Only visible once installed (packed in an .msi on the disc): passed explicitly.
    installed = make_pe(tmp_path / "pfx/Lemans.exe", [".cms_t"])
    hits = scan(tmp_path / "empty", extra_exes=[installed])
    assert [(h.name, h.files) for h in hits] == [("SecuROM", ("Lemans.exe",))]


def test_troubleshoot_reports_protection_of_installed_game(tmp_path: Path) -> None:
    cd = tmp_path / "cd"
    cd.mkdir()
    exe = make_pe(
        tmp_path / "pfx/drive_c/Program Files/Le Mans/Lemans.exe",
        [".cms_t", ".cms_d"],
        overlay=b"AddD\x03\0\0\x004.68.00\0",
    )
    game = Game(name="Le Mans", cue=str(tmp_path / "x.cue"), cd_dir=str(cd), exe=str(exe))
    check = next(r for r in run_checks(game, None) if r.title == "Copy protection: SecuROM 4.68.00")
    assert check.status is Status.WARN
    assert "Lemans.exe" in check.detail and "no disc is inserted" in check.detail


def test_last_used_source_is_repointed(tmp_path: Path) -> None:
    cd = "/home/deck/Documents/games/Le Mans 24 Hours/cd"
    win = "Z:" + cd.replace("/", "\\\\")
    (tmp_path / "system.reg").write_text(
        f'"LastUsedSource"="n;1;{win}\\\\"\n'
        f'"Other"="{win} extra\\\\file"\n'  # a different folder that starts the same
    )
    assert repoint_install_paths(tmp_path, [Path(cd)]) == 1
    text = (tmp_path / "system.reg").read_text()
    assert f'"LastUsedSource"="n;1;{CD_LETTER.upper()}:\\\\"' in text
    assert f'"Other"="{win} extra\\\\file"' in text


_alive: list[GameWizard] = []


@pytest.fixture(autouse=True)
def _release() -> Iterator[None]:
    yield
    _alive.clear()


def test_finalize_page_warns_about_protected_exe(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    root = tmp_path / "Steam"
    (root / "userdata/1/config").mkdir(parents=True)
    steam = Steam(root, "1")
    exe = make_pe(tmp_path / "game/Lemans.exe", [".text", ".cms_t", ".cms_d"])
    clean = make_pe(tmp_path / "game/Config.exe", [".text"])
    game = Game(name="Le Mans", cue=str(tmp_path / "x.cue"), installed=True, exe=str(exe))
    lib = Library(tmp_path / "games.json")
    lib.add(game)
    wizard = GameWizard(lib, game, steam)
    _alive.append(wizard)
    wizard.setStartId(Step.FINALIZE)
    wizard.restart()
    wizard.show()
    page = wizard.currentPage()
    assert isinstance(page, FinalizePage)
    assert page.exe_protection.isVisible()
    assert "Lemans.exe is protected by SecuROM" in page.exe_protection.text()
    page._select(clean)
    assert not page.exe_protection.isVisible()
