from __future__ import annotations

from pathlib import Path

import pytest
import vdf

from retrodisc.core import steam as steam_mod
from retrodisc.core.cleanup import Item, perform, plan
from retrodisc.core.library import Game
from retrodisc.core.steam import Steam

from .conftest import wrap_raw


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def make_steam(home: Path) -> Steam:
    root = home / ".local/share/Steam"
    (root / "userdata/1/config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/config.vdf").write_text('"InstallConfigStore"\n{\n}\n')
    return Steam(root, "1")


def installed_game(home: Path, iso_file: Path, steam: Steam) -> Game:
    """A game in the state the wizard leaves it: ISO, CD folder, shortcut, prefix."""
    downloads = home / "Downloads"
    downloads.mkdir()
    (downloads / "g.bin").write_bytes(wrap_raw(iso_file.read_bytes(), 1))
    cue = downloads / "g.cue"
    cue.write_text('FILE "g.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n')

    game = Game(name="Sub Culture", cue=str(cue))
    game.folder.mkdir(parents=True)
    iso = game.folder / "Sub Culture.iso"
    iso.write_bytes(iso_file.read_bytes())
    cd = game.folder / "cd"
    (cd / "Setup").mkdir(parents=True)
    (cd / "Setup/SETUP.EXE").write_bytes(b"MZ" * 100)

    shortcut = steam.add_shortcut(game.name, cd / "Setup/SETUP.EXE")
    steam.set_compat_tool(shortcut.appid, "proton_9")
    pfx = steam.prefix(shortcut.appid) / "drive_c/Program Files/Game"
    pfx.mkdir(parents=True)
    (pfx / "game.exe").write_bytes(b"MZ" * 1000)

    game.iso, game.cd_dir, game.appid = str(iso), str(cd), shortcut.appid
    return game


def test_full_cleanup(home: Path, iso_file: Path) -> None:
    steam = make_steam(home)
    other = steam.add_shortcut("Other game", home / "other.exe")
    game = installed_game(home, iso_file, steam)
    assert game.appid is not None

    targets = {t.item: t for t in plan(game, steam)}
    assert set(targets) == set(Item)
    assert all(t.blocked is None for t in targets.values())
    assert targets[Item.PREFIX].size == 2000

    assert perform(game, steam, set(Item)) == []

    assert not steam.compatdata(game.appid).exists()
    assert not game.folder.exists()  # ISO + CD gone, empty game folder removed too
    assert [s.appid for s in steam.shortcuts()] == [other.appid]
    raw = vdf.binary_loads(steam.shortcuts_path.read_bytes())
    assert list(raw["shortcuts"]) == ["0"]  # re-indexed
    assert steam.compat_tool(game.appid) is None
    # The original disc image is untouched.
    assert (home / "Downloads/g.cue").exists() and (home / "Downloads/g.bin").exists()


def test_partial_cleanup_keeps_unselected(home: Path, iso_file: Path) -> None:
    steam = make_steam(home)
    game = installed_game(home, iso_file, steam)
    assert game.appid is not None
    assert perform(game, steam, {Item.ISO, Item.CD}) == []
    assert not Path(game.iso or "").exists() and not Path(game.cd_dir or "").exists()
    assert steam.compatdata(game.appid).exists()
    assert len(steam.shortcuts()) == 1


def test_refuses_folder_with_original_files(home: Path, iso_file: Path) -> None:
    steam = make_steam(home)
    game = installed_game(home, iso_file, steam)
    game.cd_dir = str(home / "Downloads")  # CD "extracted" next to the .cue/.bin
    target = next(t for t in plan(game, steam) if t.item is Item.CD)
    assert target.blocked is not None

    errors = perform(game, steam, {Item.CD})
    assert errors == [target.blocked]
    assert (home / "Downloads/g.bin").exists()


@pytest.mark.parametrize("folder", ["home", "root"])
def test_refuses_home_and_system_folders(home: Path, iso_file: Path, folder: str) -> None:
    steam = make_steam(home)
    game = installed_game(home, iso_file, steam)
    game.cd_dir = str(home if folder == "home" else Path("/"))
    target = next(t for t in plan(game, steam) if t.item is Item.CD)
    assert target.blocked is not None


def test_refuses_folder_used_by_another_game(home: Path, iso_file: Path) -> None:
    steam = make_steam(home)
    game = installed_game(home, iso_file, steam)
    other = Game(name="Disc 2", cue=str(home / "d2.cue"), cd_dir=game.cd_dir)
    target = next(t for t in plan(game, steam, [other]) if t.item is Item.CD)
    assert target.blocked is not None
    assert perform(game, steam, {Item.CD}, [other])
    assert Path(game.cd_dir or "").exists()


def test_plan_without_steam_or_files(home: Path) -> None:
    assert plan(Game(name="New", cue=str(home / "missing.cue")), None) == []
