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
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from retrodisc.core import cleanup, prefix
from retrodisc.core.cleanup import Item
from retrodisc.core.discs import find_discs
from retrodisc.core.library import Game, Library, Step, pretty_name
from retrodisc.core.steam import Steam, SteamError
from retrodisc.ui.common import show_error, start_game, with_steam_closed
from retrodisc.ui.disc_switcher import insert_disc
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
        self.disc_button = QPushButton(self.tr("Change disc"))
        self.disc_button.clicked.connect(self.change_disc)
        self.fix_button = QPushButton(self.tr("Troubleshoot"))
        self.fix_button.clicked.connect(self.troubleshoot)
        self.remove_button = QPushButton(self.tr("Remove"))
        self.remove_button.clicked.connect(self.remove)

        buttons = QHBoxLayout()
        for b in (add, self.continue_button, self.play_button, self.disc_button, self.fix_button):
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
            detail = self._step_text(game)
            if game.multi_disc:
                detail = self.tr("{n} discs · {disc} in drive R: · {step}").format(
                    n=len(game.discs), disc=game.disc_label(game.current_disc), step=detail
                )
            item = QListWidgetItem(f"{game.name}\n{detail}")
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
        self.disc_button.setVisible(game is not None and game.multi_disc)
        self.disc_button.setEnabled(
            game is not None
            and game.appid is not None
            and self.steam is not None
            and prefix.drive_link(self.steam.prefix(game.appid)).is_symlink()
        )

    # ---- actions -----------------------------------------------------------------------

    def add_game(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose the .cue file (any disc) or an .m3u playlist"),
            str(Path.home()),
            self.tr("Disc images (*.cue *.CUE *.m3u *.M3U)"),
        )
        if not path:
            return
        cues = [str(p) for p in find_discs(Path(path))] or [path]
        if len(cues) > 1:
            answer = QMessageBox.question(
                self,
                self.tr("Multi-disc game"),
                self.tr("Found {n} discs:\n\n{names}\n\nAdd them all to this game?").format(
                    n=len(cues), names="\n".join(Path(c).name for c in cues)
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                cues = [path] if not path.lower().endswith(".m3u") else cues[:1]
        game = Game(name=pretty_name(Path(cues[0]).stem), cue=cues[0])
        game.set_discs(cues)
        self.library.add(game)
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
        if game is not None:
            start_game(self, self.steam, game.appid)

    def change_disc(self) -> None:
        game = self.current()
        if game is None or self.steam is None:
            return
        menu = QMenu(self)
        for index, disc in enumerate(game.discs):
            action = menu.addAction(f"{game.disc_label(index)} — {Path(disc.cue).name}")
            action.setCheckable(True)
            action.setChecked(index == game.current_disc)
            action.setData(index)
        chosen = menu.exec(self.disc_button.mapToGlobal(self.disc_button.rect().bottomLeft()))
        if chosen is None or chosen.data() == game.current_disc:
            return
        if insert_disc(self, self.steam, game, int(chosen.data())):
            self.library.update(game)
            self.reload(select=game.id)

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
