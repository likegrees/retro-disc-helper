from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from retrodisc.ui.widgets import SafeComboBox


def make_combo() -> SafeComboBox:
    if QApplication.instance() is None:
        QApplication([])
    combo = SafeComboBox()
    combo.addItems(["game.exe", "unins000.exe", "setup.exe"])
    combo.show()
    return combo


def test_arrow_keys_open_the_list_without_changing_the_choice() -> None:
    combo = make_combo()
    for key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_End):
        combo.hidePopup()
        event = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        combo.keyPressEvent(event)
        assert combo.currentIndex() == 0
        assert combo.view().isVisible()  # the d-pad opens the list instead
    combo.hidePopup()


def test_scroll_wheel_does_not_change_the_choice() -> None:
    combo = make_combo()
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    combo.wheelEvent(event)
    assert combo.currentIndex() == 0
    assert not event.isAccepted()  # passed on, so the page scrolls instead
