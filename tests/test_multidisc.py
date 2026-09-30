from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from retrodisc.core import steam as steam_mod
from retrodisc.core.cleanup import Item, perform, plan
from retrodisc.core.diagnostics import Status, run_checks
from retrodisc.core.discs import disc_number, find_discs
from retrodisc.core.iso import volume_info
from retrodisc.core.library import Disc, Game, Library, Step
from retrodisc.core.prefix import LABEL_FILE, SERIAL_FILE, drive_link, insert_disc, setup_cd_drive
from retrodisc.core.steam import Steam, SteamError

from .conftest import make_iso, wrap_raw

CUE = 'FILE "{bin}" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n'


def write_disc(folder: Path, stem: str, label: str) -> Path:
    iso = make_iso(folder / f"{stem}.src.iso", label=label)
    (folder / f"{stem}.bin").write_bytes(wrap_raw(iso.read_bytes(), 1))
    iso.unlink()
    cue = folder / f"{stem}.cue"
    cue.write_text(CUE.format(bin=f"{stem}.bin"))
    return cue


# ---- detection ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "chosen", "expected"),
    [
        (
            [
                "Game (Europe) (Disc 1)",
                "Game (Europe) (Disc 2)",
                "Game (Europe) (Disc 3)",
                "Other (Europe) (Disc 1)",
            ],
            "Game (Europe) (Disc 2)",
            ["Game (Europe) (Disc 1)", "Game (Europe) (Disc 2)", "Game (Europe) (Disc 3)"],
        ),
        (["Myst CD1", "Myst CD2"], "Myst CD1", ["Myst CD1", "Myst CD2"]),
        (["Solo (Europe)"], "Solo (Europe)", ["Solo (Europe)"]),
        (
            ["Big (Disc 10)", "Big (Disc 2)", "Big (Disc 1)"],
            "Big (Disc 1)",
            ["Big (Disc 1)", "Big (Disc 2)", "Big (Disc 10)"],
        ),
    ],
)
def test_find_discs(tmp_path: Path, names: list[str], chosen: str, expected: list[str]) -> None:
    for name in names:
        (tmp_path / f"{name}.cue").write_text("")
    found = find_discs(tmp_path / f"{chosen}.cue")
    assert [p.stem for p in found] == expected


def test_find_discs_from_m3u(tmp_path: Path) -> None:
    for name in ("b", "a"):
        (tmp_path / f"{name}.cue").write_text("")
    (tmp_path / "game.m3u").write_text("# playlist\nb.cue\na.cue\nmissing.cue\n")
    assert [p.name for p in find_discs(tmp_path / "game.m3u")] == ["b.cue", "a.cue"]
    # Picking a disc listed in a playlist uses the playlist order.
    assert [p.name for p in find_discs(tmp_path / "a.cue")] == ["b.cue", "a.cue"]


def test_disc_number() -> None:
    assert disc_number(Path("X (Disc 2 of 3).cue")) == 2
    assert disc_number(Path("X.cue")) is None


# ---- library ------------------------------------------------------------------------------


def test_old_records_still_load(tmp_path: Path) -> None:
    path = tmp_path / "games.json"
    path.write_text(json.dumps({"games": [{"name": "Old", "cue": "/x.cue", "iso": "/x.iso"}]}))
    game = Library(path).games[0]
    assert game.discs == [Disc("/x.cue", "/x.iso", None)]
    assert not game.multi_disc and game.current_disc == 0


def test_multi_disc_roundtrip_and_steps(tmp_path: Path) -> None:
    lib = Library(tmp_path / "games.json")
    game = Game(name="Big", cue="/d1.cue")
    game.set_discs(["/d1.cue", "/d2.cue"])
    lib.add(game)
    loaded = Library(tmp_path / "games.json").games[0]
    assert [d.cue for d in loaded.discs] == ["/d1.cue", "/d2.cue"]
    assert loaded.default_cd_dir(1).name == "cd2"
    assert loaded.default_iso(1).name == "Big (Disc 2).iso"

    iso1 = tmp_path / "1.iso"
    iso1.write_bytes(b"")
    loaded.update_disc(0, Disc("/d1.cue", str(iso1), str(tmp_path)))
    assert loaded.step is Step.CONVERT  # disc 2 still missing

    # Re-ordering keeps what was already converted.
    loaded.set_discs(["/d2.cue", "/d1.cue"])
    assert loaded.discs[1].iso == str(iso1)


# ---- drive R: -----------------------------------------------------------------------------


def test_insert_disc_switches_label_and_serial(tmp_path: Path) -> None:
    pfx = tmp_path / "pfx"
    (pfx / "dosdevices").mkdir(parents=True)
    (pfx / "system.reg").write_text("WINE REGISTRY Version 2\n")
    isos = [make_iso(tmp_path / f"{n}.iso", label=f"DISC{n}") for n in (1, 2)]
    cds = [tmp_path / f"cd{n}" for n in (1, 2)]
    for cd in cds:
        cd.mkdir()

    setup_cd_drive(pfx, cds[0], volume_info(isos[0]))
    insert_disc(pfx, cds[1], volume_info(isos[1]))

    link = drive_link(pfx)
    assert link.resolve() == cds[1].resolve()
    assert (link / LABEL_FILE).read_text() == "DISC2"
    assert (link / SERIAL_FILE).read_text() == volume_info(isos[1]).serial
    insert_disc(pfx, cds[0], volume_info(isos[0]))
    assert (link / LABEL_FILE).read_text() == "DISC1"


# ---- Proton prefix ------------------------------------------------------------------------


def fake_steam(tmp_path: Path, proton_script: str) -> Steam:
    root = tmp_path / "Steam"
    tool = root / "steamapps/common/Proton 9.0"
    (tool / "files/bin").mkdir(parents=True)
    (tool / "proton").write_text(proton_script)
    (tool / "files/bin/wineserver").write_text(
        f'#!/bin/sh\necho "wineserver $* $WINEPREFIX" >> "{tmp_path}/calls.log"\n'
    )
    for script in (tool / "proton", tool / "files/bin/wineserver"):
        os.chmod(script, 0o755)
    (root / "userdata/1").mkdir(parents=True)
    return Steam(root, "1")


def test_prepare_prefix(tmp_path: Path) -> None:
    steam = fake_steam(
        tmp_path,
        "#!/bin/sh\n"
        f'echo "proton $* $STEAM_COMPAT_DATA_PATH" >> "{tmp_path}/calls.log"\n'
        'mkdir -p "$STEAM_COMPAT_DATA_PATH/pfx/dosdevices"\n'
        'echo "WINE REGISTRY Version 2" > "$STEAM_COMPAT_DATA_PATH/pfx/system.reg"\n',
    )
    pfx = steam.prepare_prefix(3000000001, "proton_9")
    assert (pfx / "system.reg").exists()
    calls = (tmp_path / "calls.log").read_text().splitlines()
    compat = steam.compatdata(3000000001)
    assert calls == [f"proton run wineboot --init {compat}", f"wineserver -w {pfx}"]
    # Already prepared: nothing runs again.
    steam.prepare_prefix(3000000001, "proton_9")
    assert len((tmp_path / "calls.log").read_text().splitlines()) == 2


def test_prepare_prefix_failure(tmp_path: Path) -> None:
    steam = fake_steam(tmp_path, "#!/bin/sh\nexit 1\n")
    with pytest.raises(SteamError):
        steam.prepare_prefix(3000000001, "proton_9")
    with pytest.raises(SteamError):
        steam.prepare_prefix(3000000001, "proton_missing")


# ---- clean-up and diagnostics -------------------------------------------------------------


@pytest.fixture
def two_disc_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Game:
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    monkeypatch.setattr("retrodisc.core.diagnostics.is_steam_running", lambda: False)
    cues = [write_disc(downloads, f"Big (Disc {n})", f"BIG{n}") for n in (1, 2)]
    game = Game(name="Big", cue=str(cues[0]))
    game.set_discs([str(c) for c in cues])
    from retrodisc.core.convert import convert_to_iso
    from retrodisc.core.cue import parse_cue
    from retrodisc.core.iso import extract

    for index, disc in enumerate(game.discs):
        iso = convert_to_iso(parse_cue(Path(disc.cue)), _mk(game.default_iso(index)))
        cd = extract(iso, game.default_cd_dir(index))
        game.update_disc(index, Disc(disc.cue, str(iso), str(cd)))
    return game


def _mk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_diagnostics_check_every_disc(two_disc_game: Game) -> None:
    titles = {r.title: r.status for r in run_checks(two_disc_game, None)}
    for n in (1, 2):
        assert titles[f"ISO image (Disc {n})"] is Status.OK
        assert titles[f"Extracted CD (Disc {n})"] is Status.OK


def test_cleanup_deletes_every_disc_keeps_sources(two_disc_game: Game) -> None:
    targets = plan(two_disc_game, None)
    assert sorted(t.path.name for t in targets if t.path) == [
        "Big (Disc 1).iso",
        "Big (Disc 2).iso",
        "cd1",
        "cd2",
    ]
    assert perform(two_disc_game, None, {Item.ISO, Item.CD}) == []
    assert not two_disc_game.folder.exists()
    for disc in two_disc_game.discs:
        assert Path(disc.cue).exists()
        assert Path(disc.cue).with_suffix(".bin").exists()
