"""Choosing the installed exe and retargeting the Steam shortcut (the last wizard step)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import vdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from retrodisc.core import steam as steam_mod
from retrodisc.core.exe_inspect import find_exes
from retrodisc.core.library import Game, Library, Step
from retrodisc.core.steam import Steam, to_signed
from retrodisc.ui.wizard.game_wizard import GameWizard
from retrodisc.ui.wizard.pages import FinalizePage

OLD = time.mktime((1997, 11, 1, 0, 0, 0, 0, 0, -1))


def old_exe(path: Path, size: int = 10_000) -> Path:
    """An .exe as installers write it: the original 1990s date from the CD."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ" + b"\0" * size)
    os.utime(path, (OLD, OLD))
    return path


def test_installed_exe_with_original_date_is_found(tmp_path: Path) -> None:
    started = time.time() - 5
    exe = old_exe(tmp_path / "Program Files/Game/game.exe")
    assert find_exes(tmp_path, newer_than=started) == [exe]
    assert find_exes(tmp_path, newer_than=time.time() + 60) == []


@pytest.fixture
def steam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Steam:
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    root = tmp_path / "Steam"
    (root / "userdata/1/config").mkdir(parents=True)
    return Steam(root, "1")


def write_shortcuts(steam: Steam, entry: dict[str, object]) -> None:
    steam.shortcuts_path.write_bytes(vdf.binary_dumps({"shortcuts": {"0": entry}}))


def raw_entry(steam: Steam) -> dict[str, object]:
    entry: dict[str, object] = vdf.binary_loads(steam.shortcuts_path.read_bytes())["shortcuts"]["0"]
    return entry


def test_update_lowercase_keys_written_by_steam(steam: Steam) -> None:
    appid = 3_000_000_123
    write_shortcuts(
        steam,
        {
            "appid": to_signed(appid),
            "appname": "Setup",
            "exe": '"/cd/setup.exe"',
            "StartDir": '"/cd"',
        },
    )
    steam.update_shortcut(appid, name="Sub Culture", exe=Path("/pfx/game/SubCulture.exe"))
    entry = raw_entry(steam)
    # The existing keys are updated in place, no second "Exe"/"AppName" is added.
    assert entry["exe"] == '"/pfx/game/SubCulture.exe"'
    assert entry["appname"] == "Sub Culture"
    assert entry["StartDir"] == '"/pfx/game"'
    assert {k.lower() for k in entry} == {"appid", "appname", "exe", "startdir"}
    assert len(entry) == 4


def test_update_collapses_duplicate_keys(steam: Steam) -> None:
    appid = 3_000_000_124
    write_shortcuts(
        steam,
        {
            "appid": to_signed(appid),
            "AppName": "G",
            "exe": '"/old/setup.exe"',
            "Exe": '"/old/other.exe"',
        },
    )
    steam.update_shortcut(appid, exe=Path("/new/game.exe"))
    entry = raw_entry(steam)
    exe_keys = [k for k in entry if k.lower() == "exe"]
    assert len(exe_keys) == 1 and entry[exe_keys[0]] == '"/new/game.exe"'
    assert steam.shortcuts()[0].exe == Path("/new/game.exe")


_alive: list[GameWizard] = []


@pytest.fixture(autouse=True)
def _release() -> Iterator[None]:
    yield
    _alive.clear()


def test_finalize_page_lists_installed_exe_and_updates_steam(
    tmp_path: Path, steam: Steam, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("retrodisc.ui.common.is_steam_running", lambda: False)
    app = QApplication.instance() or QApplication([])
    assert app is not None
    cd = tmp_path / "cd"
    old_exe(cd / "SETUP.EXE", 100)
    shortcut = steam.add_shortcut("Sub Culture", cd / "SETUP.EXE")
    started = time.time() - 5
    drive_c = steam.prefix(shortcut.appid) / "drive_c"
    game_exe = old_exe(drive_c / "Program Files/Sub Culture/SubCulture.exe", 50_000)
    old_exe(drive_c / "Program Files/Sub Culture/unins000.exe", 90_000)
    old_exe(drive_c / "windows/system32/notepad.exe")  # not from the installer

    game = Game(
        name="Sub Culture",
        cue=str(tmp_path / "x.cue"),
        cd_dir=str(cd),
        installer=str(cd / "SETUP.EXE"),
        appid=shortcut.appid,
        proton="proton_9",
        install_started=started,
        installed=True,
    )
    lib = Library(tmp_path / "games.json")
    lib.add(game)
    wizard = GameWizard(lib, game, steam)
    _alive.append(wizard)
    wizard.setStartId(Step.FINALIZE)
    wizard.restart()
    page = wizard.currentPage()
    assert isinstance(page, FinalizePage)

    listed = [page.exes.item(i).data(Qt.ItemDataRole.UserRole) for i in range(page.exes.count())]
    assert listed[0] == str(game_exe)  # the game first, the uninstaller after it
    assert str(drive_c / "windows/system32/notepad.exe") not in listed
    assert page.found_hint.isHidden()

    page._apply()
    assert steam.shortcuts()[0].exe == game_exe
    assert steam.shortcuts()[0].start_dir == game_exe.parent
    assert Library(tmp_path / "games.json").games[0].exe == str(game_exe)
    assert page.isComplete()
