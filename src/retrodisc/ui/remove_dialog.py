"""Confirm removing a game, optionally deleting everything the app created for it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from retrodisc.core.cleanup import Item, Target
from retrodisc.core.library import Game


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class RemoveDialog(QDialog):
    def __init__(self, game: Game, targets: list[Target], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Remove {name}").format(name=game.name))
        self.setMinimumWidth(900)
        self.boxes: dict[Item, QCheckBox] = {}

        layout = QVBoxLayout(self)
        intro = QLabel(
            self.tr("{name} will be removed from this list.").format(name=game.name)
            if not targets
            else self.tr(
                "{name} will be removed from this list. You can also delete what was "
                "created for it:"
            ).format(name=game.name)
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if targets:
            self.all_box = QCheckBox(self.tr("Full clean-up (delete everything below)"))
            self.all_box.toggled.connect(self._toggle_all)
            layout.addWidget(self.all_box)

        labels = {
            Item.SHORTCUT: self.tr("Steam shortcut and its Proton setting"),
            Item.PREFIX: self.tr("Proton prefix: the installed game and its save games"),
            Item.CD: self.tr("Extracted CD folder"),
            Item.ISO: self.tr("ISO image"),
        }
        for target in targets:
            text = labels[target.item]
            if target.size is not None:
                text += f"  ({human_size(target.size)})"
            box = QCheckBox(text)
            box.toggled.connect(self._update_all_box)
            layout.addWidget(box)
            details = [str(target.path)] if target.path else []
            if target.item is Item.PREFIX:
                details.append(self.tr("Save games stored here are lost for good."))
            if target.blocked:
                box.setEnabled(False)
                details.append(
                    self.tr("Will not be deleted: {reason}").format(reason=target.blocked)
                )
            if details:
                hint = QLabel("\n".join(details))
                hint.setObjectName(
                    "warn" if target.item is Item.PREFIX or target.blocked else "hint"
                )
                hint.setWordWrap(True)
                hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                hint.setContentsMargins(40, 0, 0, 8)
                layout.addWidget(hint)
            if box.isEnabled():
                self.boxes[target.item] = box

        note = QLabel(self.tr("Your original .cue/.bin files are never deleted."))
        note.setObjectName("hint")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.addButton(self.tr("Remove"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setFocus()

    def selected(self) -> set[Item]:
        return {item for item, box in self.boxes.items() if box.isChecked()}

    def _toggle_all(self, checked: bool) -> None:
        for box in self.boxes.values():
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._update_button()

    def _update_all_box(self) -> None:
        self.all_box.blockSignals(True)
        self.all_box.setChecked(bool(self.boxes) and len(self.selected()) == len(self.boxes))
        self.all_box.blockSignals(False)
        self._update_button()

    def _update_button(self) -> None:
        self.ok.setText(self.tr("Delete and remove") if self.selected() else self.tr("Remove"))
