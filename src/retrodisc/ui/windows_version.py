"""Choose which Windows version Proton reports to the game (and its installer)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from retrodisc.core import prefix
from retrodisc.core.library import Game
from retrodisc.core.steam import Steam
from retrodisc.ui.common import show_error
from retrodisc.ui.widgets import SafeComboBox
from retrodisc.ui.workers import run_with_progress


def _tr(text: str) -> str:
    return QCoreApplication.translate("WindowsVersionBox", text)


def apply_windows_version(
    parent: QWidget | None, steam: Steam, game: Game, version: str | None
) -> bool:
    """Create the prefix if needed, then set the version. Shows errors; True on success."""
    appid, proton = game.appid, game.proton
    if appid is None or proton is None:
        show_error(parent, _tr("Add the game to Steam first."))
        return False

    def job(_report: object) -> None:
        pfx = steam.prepare_prefix(appid, proton)
        prefix.set_windows_version(pfx, version)

    try:
        run_with_progress(parent, _tr("Setting the Windows version…"), job, cancellable=False)
    except Exception as exc:
        show_error(parent, exc)
        return False
    game.windows_version = version
    return True


class WindowsVersionBox(QWidget):
    """Calls `on_change` after the version was applied to the game's prefix."""

    def __init__(self, on_change: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_change = on_change
        self.steam: Steam | None = None
        self.game: Game | None = None
        self.combo = SafeComboBox()
        self.combo.addItem(_tr("Default (Windows 10)"), None)
        for name, label in prefix.WINDOWS_VERSIONS.items():
            if name != "win10":
                self.combo.addItem(label, name)
        self.combo.activated.connect(self._activated)
        hint = QLabel(
            _tr(
                "Older games and installers may refuse to run, or misbehave, on a Windows "
                "they don't know. Change it while the game is closed."
            )
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(QLabel(_tr("Windows version the game sees")))
        layout.addWidget(self.combo)
        layout.addWidget(hint)

    def bind(self, steam: Steam | None, game: Game) -> None:
        self.steam, self.game = steam, game
        self.combo.setCurrentIndex(max(self.combo.findData(game.windows_version), 0))
        self.setEnabled(steam is not None and game.appid is not None and game.proton is not None)

    def _activated(self, index: int) -> None:
        if self.steam is None or self.game is None:
            return
        version = self.combo.itemData(index)
        if version == self.game.windows_version:
            return
        if apply_windows_version(self, self.steam, self.game, version):
            self.on_change()
        self.combo.setCurrentIndex(max(self.combo.findData(self.game.windows_version), 0))
