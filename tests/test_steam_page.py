"""The Add to Steam page when Steam has no Proton yet (fresh install)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from retrodisc.core.library import Game, Library, Step
from retrodisc.core.steam import Steam
from retrodisc.ui.wizard import pages
from retrodisc.ui.wizard.game_wizard import GameWizard
from retrodisc.ui.wizard.pages import SteamPage

_alive: list[GameWizard] = []


@pytest.fixture(autouse=True)
def _release() -> Iterator[None]:
    yield
    _alive.clear()


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_page_offers_proton_install_and_notices_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    root = tmp_path / "Steam"
    (root / "userdata/1/config").mkdir(parents=True)
    (root / "steamapps").mkdir()
    steam = Steam(root, "1")
    cd = tmp_path / "cd"
    cd.mkdir()
    (cd / "setup.exe").write_bytes(b"MZ")
    game = Game(
        name="G", cue=str(tmp_path / "g.cue"), cd_dir=str(cd), installer=str(cd / "setup.exe")
    )
    lib = Library(tmp_path / "games.json")
    lib.add(game)
    opened: list[bool] = []
    monkeypatch.setattr(pages, "install_proton", lambda: opened.append(True))

    wizard = GameWizard(lib, game, steam)
    _alive.append(wizard)
    wizard.setStartId(Step.ADD_TO_STEAM)
    wizard.restart()
    wizard.show()
    page = wizard.currentPage()
    assert isinstance(page, SteamPage)
    page.proton_poll.setInterval(50)

    # Fresh Steam: explained on the page, install offered, adding disabled.
    assert page.no_proton.isVisible() and not page.button.isEnabled()
    assert page.proton_poll.isActive()
    page._install_proton()
    assert opened == [True]

    # Steam finishes installing Proton: the page picks it up by itself.
    tool = root / "steamapps/common/Proton 9.0 (Beta)"
    tool.mkdir(parents=True)
    (tool / "proton").write_text("")
    wait(300)
    assert not page.no_proton.isVisible() and page.button.isEnabled()
    assert page.proton.currentData() == "proton_9"
    assert not page.proton_poll.isActive()
