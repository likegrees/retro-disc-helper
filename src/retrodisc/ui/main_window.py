"""Main window: the game library."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrodisc.core import cleanup
from retrodisc.core.cleanup import Item
from retrodisc.core.library import Game, Library, Step, pretty_name
from retrodisc.core.steam import Steam, SteamError, launch
from retrodisc.ui.common import show_error, with_steam_closed
from retrodisc.ui.remove_dialog import RemoveDialog
from retrodisc.ui.troubleshoot import TroubleshootDialog
from retrodisc.ui.wizard.game_wizard import GameWizard
from retrodisc.ui.workers import run_with_progress


class MainWindow(QMainWindow):
    def __init__(self, library: Library) -> None:
        super().__init__()
        self.library = library
        self.steam: Steam | None
        try:
            self.steam = Steam.detect()
        except SteamError:
            self.steam = None

        self.setWindowTitle(self.tr("Retro Disc Helper"))
        self.resize(1200, 800)

        self.banner = QLabel(self.tr("Steam was not found: converting and extracting still work."))
        self.banner.setObjectName("warn")
        self.banner.setVisible(self.steam is None)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self.open_wizard())
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.empty = QLabel(
            self.tr('No games yet. Press "Add game" and choose the .cue file of a disc image.')
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)

        add = QPushButton(self.tr("Add game…"))
        add.clicked.connect(self.add_game)
        self.continue_button = QPushButton(self.tr("Continue setup"))
        self.continue_button.clicked.connect(self.open_wizard)
        self.play_button = QPushButton(self.tr("Play"))
        self.play_button.clicked.connect(self.play)
        self.fix_button = QPushButton(self.tr("Troubleshoot"))
        self.fix_button.clicked.connect(self.troubleshoot)
        self.remove_button = QPushButton(self.tr("Remove"))
        self.remove_button.clicked.connect(self.remove)

        buttons = QHBoxLayout()
        for b in (add, self.continue_button, self.play_button, self.fix_button):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.remove_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.banner)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty, 1)
        layout.addLayout(buttons)
        self.setCentralWidget(central)
        self.reload()

    # ---- list --------------------------------------------------------------------------

    def _step_text(self, game: Game) -> str:
        return {
            Step.CONVERT: self.tr("Next: create the ISO"),
            Step.EXTRACT: self.tr("Next: extract the CD"),
            Step.ADD_TO_STEAM: self.tr("Next: add to Steam"),
            Step.INSTALL: self.tr("Next: install"),
            Step.FINALIZE: self.tr("Next: choose the game executable"),
            Step.DONE: self.tr("Ready to play"),
        }.get(game.step, "")

    def reload(self, select: str | None = None) -> None:
        self.library.load()
        self.list.clear()
        for game in self.library.games:
            item = QListWidgetItem(f"{game.name}\n{self._step_text(game)}")
            item.setData(Qt.ItemDataRole.UserRole, game.id)
            self.list.addItem(item)
            if game.id == select:
                self.list.setCurrentItem(item)
        has_games = bool(self.library.games)
        self.list.setVisible(has_games)
        self.empty.setVisible(not has_games)
        if has_games and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        self._update_buttons()

    def current(self) -> Game | None:
        item = self.list.currentItem()
        return self.library.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _update_buttons(self) -> None:
        game = self.current()
        for b in (self.continue_button, self.fix_button, self.remove_button):
            b.setEnabled(game is not None)
        self.play_button.setEnabled(
            game is not None and game.appid is not None and self.steam is not None
        )

    # ---- actions -----------------------------------------------------------------------

    def add_game(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose the .cue file"),
            str(Path.home()),
            self.tr("Cue sheets (*.cue *.CUE)"),
        )
        if not path:
            return
        game = self.library.add(Game(name=pretty_name(Path(path).stem), cue=path))
        self.reload(select=game.id)
        self.open_wizard()

    def open_wizard(self) -> None:
        game = self.current()
        if game is None:
            return
        GameWizard(self.library, game, self.steam, self).exec()
        self.reload(select=game.id)

    def troubleshoot(self) -> None:
        game = self.current()
        if game is None:
            return
        TroubleshootDialog(self.library, game, self.steam, self).exec()
        self.reload(select=game.id)

    def play(self) -> None:
        game = self.current()
        if game is None or game.appid is None or self.steam is None:
            return
        shortcut = next((s for s in self.steam.shortcuts() if s.appid == game.appid), None)
        if shortcut is not None:
            launch(shortcut)

    def remove(self) -> None:
        game = self.current()
        if game is None:
            return
        others = [g for g in self.library.games if g.id != game.id]
        try:
            targets = run_with_progress(
                self,
                self.tr("Checking files…"),
                lambda _r: cleanup.plan(game, self.steam, others),
                cancellable=False,
            )
        except Exception as exc:
            show_error(self, exc)
            return
        dialog = RemoveDialog(game, targets, self)
        if dialog.exec() != RemoveDialog.DialogCode.Accepted:
            return
        items = dialog.selected()

        def delete() -> list[str]:
            return run_with_progress(
                self,
                self.tr("Deleting…"),
                lambda _r: cleanup.perform(game, self.steam, items, others),
                cancellable=False,
            )

        try:
            errors = with_steam_closed(self, delete) if Item.SHORTCUT in items else delete()
        except Exception as exc:
            show_error(self, exc)
            return
        if errors is None:  # the user chose not to close Steam
            return
        self.library.remove(game.id)
        self.reload()
        if errors:
            QMessageBox.warning(
                self,
                self.tr("Some items were not deleted"),
                "\n\n".join(errors),
            )
