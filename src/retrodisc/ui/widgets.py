"""Widgets tuned for the Legion Go (touch screen, d-pad mapped to arrow keys)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QWidget

NAVIGATION_KEYS = {
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
}


class SafeComboBox(QComboBox):
    """A drop-down whose value only changes by picking an entry from the open list.

    A plain QComboBox changes its value when it has focus and gets arrow keys (the d-pad)
    or a scroll-wheel/touchpad event, which silently picked another .exe, disc or Windows
    version while the user was just moving around the page.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # no focus from the scroll wheel
        # Lets the stylesheet size the rows of the open list (finger-sized on the Legion Go).
        self.setItemDelegate(QStyledItemDelegate(self))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if Qt.Key(event.key()) in NAVIGATION_KEYS:
            self.showPopup()  # arrows open the list; choosing happens inside it
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()  # let the page scroll instead
