from __future__ import annotations

from pathlib import Path

import pytest

from retrodisc.core import steam as steam_mod
from retrodisc.core.diagnostics import Status, run_checks
from retrodisc.core.library import Game, Library, Step, pretty_name
from retrodisc.core.steam import Steam

from .conftest import wrap_raw


def test_pretty_name() -> None:
    assert pretty_name("Sub Culture (Europe) (En,Fr,De)") == "Sub Culture"
    assert pretty_name("(weird)") == "(weird)"


def test_library_roundtrip_and_steps(tmp_path: Path) -> None:
    lib = Library(tmp_path / "games.json")
    game = lib.add(Game(name="X", cue=str(tmp_path / "x.cue")))
    assert game.step is Step.CONVERT
    iso = tmp_path / "x.iso"
    iso.write_bytes(b"")
    game.iso = str(iso)
    game.cd_dir = str(tmp_path)
    game.appid = 3000000001
    lib.update(game)
    reloaded = Library(tmp_path / "games.json").get(game.id)
    assert reloaded is not None and reloaded.step is Step.INSTALL


def test_diagnostics_flags_autorun_and_audio(
    tmp_path: Path, iso_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    monkeypatch.setattr("retrodisc.core.diagnostics.is_steam_running", lambda: False)
    (tmp_path / "g.bin").write_bytes(wrap_raw(iso_file.read_bytes(), 1))
    (tmp_path / "a.bin").write_bytes(b"\0" * 2352)
    cue = tmp_path / "g.cue"
    cue.write_text(
        'FILE "g.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'
        'FILE "a.bin" BINARY\n TRACK 02 AUDIO\n  INDEX 01 00:00:00\n'
    )
    cd = tmp_path / "cd"
    (cd / "Setup").mkdir(parents=True)
    (cd / "AUTORUN.EXE").write_bytes(b"MZ")
    (cd / "Setup" / "SETUP.EXE").write_bytes(b"MZ")

    root = tmp_path / "Steam"
    (root / "userdata" / "1" / "config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "config.vdf").write_text('"InstallConfigStore"\n{\n}\n')
    steam = Steam(root, "1")
    sc = steam.add_shortcut("G", cd / "AUTORUN.EXE")

    game = Game(
        name="G",
        cue=str(cue),
        iso=str(tmp_path / "missing.iso"),
        cd_dir=str(cd),
        installer=str(cd / "AUTORUN.EXE"),
        appid=sc.appid,
    )
    results = {r.title: r for r in run_checks(game, steam)}
    assert results["CD audio tracks"].status is Status.WARN
    assert results["ISO image"].status is Status.FAIL
    assert results["Proton forced"].status is Status.FAIL
    installer = results["Installer"]
    assert installer.status is Status.WARN and installer.fix is not None

    installer.fix()
    assert steam.shortcuts()[0].exe == cd / "Setup" / "SETUP.EXE"
    iso_fix = results["ISO image"].fix
    assert iso_fix is not None
    iso_fix()
    after = {r.title: r for r in run_checks(game, steam)}
    assert after["ISO image"].status is Status.OK
