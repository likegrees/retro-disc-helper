"""The CD drive letter and what the drive set-up fixes in the prefix."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from retrodisc.core import steam as steam_mod
from retrodisc.core.diagnostics import Status, run_checks
from retrodisc.core.iso import volume_info
from retrodisc.core.library import Game
from retrodisc.core.prefix import (
    CD_LETTER,
    drive_link,
    registry_drive_type,
    repoint_install_paths,
    set_registry_drive_type,
    setup_cd_drive,
)
from retrodisc.core.steam import Steam

from .conftest import make_iso

CD = "/home/deck/Documents/games/Le Mans 24 Hours/cd"
WIN_CD = "Z:" + CD.replace("/", "\\\\")  # as written in .reg files: Z:\\home\\deck\\...


def proton_start(pfx: Path) -> None:
    """What Proton's setup_game_dir_drive()/setup_steam_dir_drive() do at every launch."""
    for letter in ("s:", "t:"):
        link = pfx / "dosdevices" / letter
        if os.path.lexists(link):
            os.remove(link)


def test_cd_letter_is_not_reserved_by_proton_or_wine() -> None:
    # Proton owns C:, S:, T:, Z:; Wine auto-assigns removable media from C:/D: upward.
    assert CD_LETTER not in {"c", "s", "t", "z"}
    assert CD_LETTER > "m"


@pytest.fixture
def pfx(tmp_path: Path) -> Path:
    p = tmp_path / "pfx"
    (p / "dosdevices").mkdir(parents=True)
    (p / "system.reg").write_text(
        "WINE REGISTRY Version 2\n\n"
        "[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\{8236}] 1\n"
        f'"InstallSource"="{WIN_CD}\\\\"\n'
        '"DisplayName"="Le Mans 24 Ore"\n\n'
        "[Software\\\\Microsoft\\\\Installer\\\\SourceList\\\\Net] 1\n"
        f'"1"=str(2):"{WIN_CD}\\\\"\n'
        f'"2"="{WIN_CD}2\\\\"\n'  # a different folder (cd2): untouched
    )
    (p / "user.reg").write_text("WINE REGISTRY Version 2\n")
    return p


def test_drive_survives_proton_start(tmp_path: Path, pfx: Path) -> None:
    cd = tmp_path / "cd"
    cd.mkdir()
    setup_cd_drive(pfx, cd, volume_info(make_iso(tmp_path / "x.iso")))
    proton_start(pfx)
    assert drive_link(pfx).resolve() == cd.resolve()
    assert registry_drive_type(pfx) == "cdrom"


def test_setup_removes_old_s_drive(tmp_path: Path, pfx: Path) -> None:
    set_registry_drive_type(pfx, "cdrom", letter="s")
    cd = tmp_path / "cd"
    cd.mkdir()
    setup_cd_drive(pfx, cd, volume_info(make_iso(tmp_path / "x.iso")))
    assert registry_drive_type(pfx, "s") is None
    assert registry_drive_type(pfx, CD_LETTER) == "cdrom"


def test_recorded_install_source_points_to_cd_drive(pfx: Path) -> None:
    cd = Path(CD)
    assert repoint_install_paths(pfx, [cd], dry_run=True) == 2
    text_before = (pfx / "system.reg").read_text()
    assert repoint_install_paths(pfx, [cd], dry_run=True) == 2  # dry run changes nothing
    assert (pfx / "system.reg").read_text() == text_before

    assert repoint_install_paths(pfx, [cd]) == 2
    text = (pfx / "system.reg").read_text()
    drive = CD_LETTER.upper()
    assert f'"InstallSource"="{drive}:\\\\"' in text
    assert f'"1"=str(2):"{drive}:\\\\"' in text
    assert f'"2"="{WIN_CD}2\\\\"' in text
    assert '"DisplayName"="Le Mans 24 Ore"' in text
    assert repoint_install_paths(pfx, [cd]) == 0


def test_non_utf8_bytes_survive_registry_edits(pfx: Path) -> None:
    raw = (pfx / "system.reg").read_bytes() + b'"Odd"="caf\xe9"\n'
    (pfx / "system.reg").write_bytes(raw)
    set_registry_drive_type(pfx, "cdrom")
    assert b'"Odd"="caf\xe9"' in (pfx / "system.reg").read_bytes()


def test_troubleshoot_detects_old_setup_and_recorded_paths(
    tmp_path: Path, pfx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    monkeypatch.setattr("retrodisc.core.diagnostics.is_steam_running", lambda: False)
    root = tmp_path / "Steam"
    (root / "userdata/1/config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/config.vdf").write_text('"InstallConfigStore"\n{\n}\n')
    steam = Steam(root, "1")
    cd = tmp_path / "home/games/Le Mans/cd"
    cd.mkdir(parents=True)
    (cd / "setup.exe").write_bytes(b"MZ")
    iso = make_iso(tmp_path / "lemans.iso")
    appid = steam.add_shortcut("Le Mans", cd / "setup.exe").appid
    shutil.copytree(pfx, steam.prefix(appid))
    real = steam.prefix(appid)
    # State after an older version: S: set up, installer ran from the folder via Z:.
    set_registry_drive_type(real, "cdrom", letter="s")
    win = "Z:" + str(cd).replace("/", "\\\\")
    with (real / "system.reg").open("a") as f:
        f.write(f'"InstallSource"="{win}\\\\"\n')

    game = Game(
        name="Le Mans",
        cue=str(tmp_path / "x.cue"),
        iso=str(iso),
        cd_dir=str(cd),
        appid=appid,
        cd_drive=True,
    )
    check = next(r for r in run_checks(game, steam) if r.title.startswith("Drive R:"))
    assert check.status is Status.FAIL
    assert "older version" in check.detail and "Z:" in check.detail
    assert check.fix is not None
    check.fix()
    proton_start(real)
    check = next(r for r in run_checks(game, steam) if r.title.startswith("Drive R:"))
    assert check.status is Status.OK
