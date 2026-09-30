"""Choose which disc drive R: shows: the equivalent of swapping CDs in the drive."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from retrodisc.core import iso, prefix
from retrodisc.core.library import Game
from retrodisc.core.steam import Steam
from retrodisc.ui.common import show_error
from retrodisc.ui.widgets import SafeComboBox


def _tr(text: str) -> str:
    return QCoreApplication.translate("DiscSwitcher", text)


def insert_disc(parent: QWidget | None, steam: Steam, game: Game, index: int) -> bool:
    """Point R: at disc `index` of `game`. Shows errors; returns True on success."""
    disc = game.discs[index]
    if game.appid is None or not disc.extracted or not disc.converted:
        show_error(parent, _tr("{disc} is not extracted yet.").format(disc=game.disc_label(index)))
        return False
    try:
        prefix.insert_disc(
            steam.prefix(game.appid),
            Path(str(disc.cd_dir)),
            iso.volume_info(Path(str(disc.iso))),
        )
    except Exception as exc:
        show_error(parent, exc)
        return False
    game.current_disc = index
    return True


class DiscSwitcher(QWidget):
    """Combo box choosing the disc in drive R:. Calls `on_change` after a successful swap."""

    def __init__(self, on_change: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_change = on_change
        self.steam: Steam | None = None
        self.game: Game | None = None
        self.combo = SafeComboBox()
        self.combo.activated.connect(self._activated)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(_tr("Disc in drive R:")))
        layout.addWidget(self.combo, 1)

    def bind(self, steam: Steam | None, game: Game) -> None:
        self.steam, self.game = steam, game
        self.combo.clear()
        for index, disc in enumerate(game.discs):
            self.combo.addItem(f"{game.disc_label(index)} — {Path(disc.cue).name}")
        self.combo.setCurrentIndex(game.current_disc)
        ready = (
            steam is not None
            and game.appid is not None
            and prefix.drive_link(steam.prefix(game.appid)).is_symlink()
        )
        self.setEnabled(ready)
        self.setToolTip("" if ready else _tr("Set up drive R: first."))

    def _activated(self, index: int) -> None:
        if self.steam is None or self.game is None or index == self.game.current_disc:
            return
        if insert_disc(self, self.steam, self.game, index):
            self.on_change()
        self.combo.setCurrentIndex(self.game.current_disc)
