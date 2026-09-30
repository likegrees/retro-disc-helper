"""Regression tests for where the wizard converts and extracts discs (runs Qt offscreen)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from retrodisc.core.convert import convert_to_iso
from retrodisc.core.cue import parse_cue
from retrodisc.core.library import Disc, Game, Library, Step
from retrodisc.ui.wizard.game_wizard import GameWizard
from retrodisc.ui.wizard.pages import ConvertPage, ExtractPage, disc_targets

from .test_multidisc import write_disc

_alive: list[GameWizard] = []


@pytest.fixture(autouse=True)
def _release_wizards() -> Iterator[None]:
    yield
    _alive.clear()


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home/Downloads").mkdir(parents=True)
    return tmp_path / "home"


def page_for(game: Game, step: Step, tmp: Path) -> ConvertPage | ExtractPage:
    lib = Library(tmp / "games.json")
    lib.add(game)
    wizard = GameWizard(lib, game, None)
    wizard.setStartId(step)
    wizard.restart()
    page = wizard.currentPage()
    assert isinstance(page, ConvertPage | ExtractPage)
    _alive.append(wizard)  # Qt deletes the pages with their wizard
    return page


def converted(home: Path, stem: str = "Solo (Europe)") -> Game:
    cue = write_disc(home / "Downloads", stem, "SOLO")
    game = Game(name="Solo", cue=str(cue))
    game.default_iso(0).parent.mkdir(parents=True)
    game.iso = str(convert_to_iso(parse_cue(cue), game.default_iso(0)))
    return game


def test_disc_targets() -> None:
    field = Path("/g")
    assert disc_targets(field, [None], [Path("/x/cd")], multi=False) == [field]
    assert disc_targets(
        field, ["/g/Disc-A", "/elsewhere/cd2"], [Path("/x/cd1"), Path("/x/cd2")], multi=True
    ) == [Path("/g/Disc-A"), Path("/g/cd2")]


def test_single_disc_extracts_exactly_where_shown(app: QApplication, home: Path) -> None:
    game = converted(home)
    page = page_for(game, Step.EXTRACT, home)
    assert isinstance(page, ExtractPage)
    assert page.path_edit.text() == str(game.default_cd_dir(0))  # .../games/Solo/cd
    assert page._targets() == [game.default_cd_dir(0)]

    chosen = home / "Games/SoloCD"
    page.path_edit.setText(str(chosen))  # as if picked with Browse
    page._extract()
    assert game.cd_dir == str(chosen)  # not .../SoloCD/cd
    assert (chosen / "Setup.exe").exists()


def test_existing_custom_folder_is_kept(app: QApplication, home: Path) -> None:
    game = converted(home)
    custom = home / "Games/SoloCD"
    custom.mkdir(parents=True)
    game.cd_dir = str(custom)
    page = page_for(game, Step.EXTRACT, home)
    assert isinstance(page, ExtractPage)
    assert page.path_edit.text() == str(custom)
    assert page._targets() == [custom]


def test_single_disc_iso_path(app: QApplication, home: Path) -> None:
    game = converted(home)
    custom_iso = home / "isos/My Solo.iso"
    custom_iso.parent.mkdir()
    Path(str(game.iso)).rename(custom_iso)
    game.iso = str(custom_iso)
    page = page_for(game, Step.CONVERT, home)
    assert isinstance(page, ConvertPage)
    assert page.path_edit.text() == str(custom_iso)
    assert page._targets() == [custom_iso]


def test_multi_disc_uses_parent_folder(app: QApplication, home: Path) -> None:
    cues = [write_disc(home / "Downloads", f"Big (Disc {n})", f"BIG{n}") for n in (1, 2)]
    game = Game(name="Big", cue=str(cues[0]))
    game.set_discs([str(c) for c in cues])
    page = page_for(game, Step.EXTRACT, home)
    assert isinstance(page, ExtractPage)
    assert page.path_edit.text() == str(game.folder)
    assert page._targets() == [game.folder / "cd1", game.folder / "cd2"]

    # A disc already extracted under another name in that folder keeps it.
    game.update_disc(0, Disc(str(cues[0]), None, str(game.folder / "first")))
    assert page._targets() == [game.folder / "first", game.folder / "cd2"]
