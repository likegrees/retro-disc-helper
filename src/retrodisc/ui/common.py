"""Shared UI helpers."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox, QWidget

from retrodisc.core.steam import (
    Steam,
    SteamError,
    SteamRunningError,
    is_steam_running,
    launch,
    shutdown_steam,
)
from retrodisc.ui.workers import run_with_progress

# Big touch targets for the Legion Go screen.
STYLESHEET = """
* { font-size: 15pt; }
QPushButton { min-height: 48px; padding: 6px 18px; }
QComboBox, QLineEdit { min-height: 44px; }
QCheckBox::indicator { width: 28px; height: 28px; }
QListWidget::item { min-height: 64px; padding: 6px; }
QLabel#hint { color: palette(placeholder-text); }
QLabel#warn { color: #b26a00; }
QLabel#error { color: #c62828; }
QLabel#ok { color: #2e7d32; }
"""


def _tr(text: str) -> str:
    return QCoreApplication.translate("common", text)


def show_error(parent: QWidget | None, exc: BaseException | str) -> None:
    QMessageBox.critical(parent, _tr("Something went wrong"), str(exc))


def ensure_steam_closed(parent: QWidget | None) -> bool:
    """Steam rewrites its config on exit, so offer to close it before editing. True = closed."""
    if not is_steam_running():
        return True
    answer = QMessageBox.question(
        parent,
        _tr("Steam is running"),
        _tr(
            "Steam must be closed to change its game list and compatibility settings.\n\n"
            "Close Steam now?"
        ),
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    closed = run_with_progress(
        parent, _tr("Closing Steam…"), lambda _report: shutdown_steam(), cancellable=False
    )
    if not closed:
        show_error(parent, _tr("Steam did not close. Quit it from its menu and try again."))
    return closed


def with_steam_closed[T](parent: QWidget | None, action: Callable[[], T]) -> T | None:
    """Run a Steam-config edit, asking to close Steam first. None if the user declined."""
    if not ensure_steam_closed(parent):
        return None
    try:
        return action()
    except SteamRunningError:
        # Steam was restarted in the meantime (e.g. by a steam:// link).
        if not ensure_steam_closed(parent):
            return None
        return action()


def start_game(parent: QWidget | None, steam: Steam | None, appid: int | None) -> bool:
    """Ask Steam to run the game's shortcut; show an error instead of failing silently."""
    if steam is None or appid is None:
        show_error(parent, _tr("Steam was not found."))
        return False
    shortcut = next((s for s in steam.shortcuts() if s.appid == appid), None)
    if shortcut is None:
        show_error(
            parent,
            _tr(
                "The game's shortcut is not in Steam anymore. Add it again in the "
                '"Add to Steam" step.'
            ),
        )
        return False
    try:
        run_with_progress(
            parent,
            _tr("Asking Steam to start the game…"),
            lambda _r: launch(shortcut),
            cancellable=False,
        )
    except SteamError as exc:
        show_error(parent, exc)
        return False
    return True
