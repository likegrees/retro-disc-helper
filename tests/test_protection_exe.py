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
from retrodisc.core.exe_inspect import pe_sections
from retrodisc.core.library import Game, Library, Step
from retrodisc.core.prefix import CD_LETTER, repoint_install_paths
from retrodisc.core.protection import scan, scan_executable
from retrodisc.core.steam import Steam
from retrodisc.ui.wizard.game_wizard import GameWizard
from retrodisc.ui.wizard.pages import FinalizePage


def make_pe(path: Path, sections: list[str]) -> Path:
    """A minimal PE file: DOS header, PE header, empty optional header, section table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    dos = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", dos, 0x3C, 64)
    coff = struct.pack("<HHIIIHH", 0x14C, len(sections), 0, 0, 0, 0, 0x102)
    table = b"".join(name.encode().ljust(8, b"\0") + b"\0" * 32 for name in sections)
    path.write_bytes(bytes(dos) + b"PE\0\0" + coff + table + b"\0" * 64)
    return path


def test_sections_and_detection(tmp_path: Path) -> None:
    securom = make_pe(
        tmp_path / "Lemans.exe", [".text", ".rdata", ".data", ".data1", ".cms_t", ".cms_d", ".rsrc"]
    )
    safedisc = make_pe(tmp_path / "game2.exe", [".text", "stxt774", "stxt371"])
    clean = make_pe(tmp_path / "clean.exe", [".text", ".data"])
    assert pe_sections(securom)[4:6] == [".cms_t", ".cms_d"]
    assert scan_executable(securom) == ["SecuROM"]
    assert scan_executable(safedisc) == ["SafeDisc"]
    assert scan_executable(clean) == []
    (tmp_path / "notes.exe").write_text("not a program")
    assert scan_executable(tmp_path / "notes.exe") == []


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
    exe = make_pe(tmp_path / "pfx/drive_c/Program Files/Le Mans/Lemans.exe", [".cms_t", ".cms_d"])
    game = Game(name="Le Mans", cue=str(tmp_path / "x.cue"), cd_dir=str(cd), exe=str(exe))
    check = next(r for r in run_checks(game, None) if r.title == "Copy protection: SecuROM")
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
