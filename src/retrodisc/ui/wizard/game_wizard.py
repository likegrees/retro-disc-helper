"""Per-game wizard; reopens at the first unfinished step."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QWizard

from retrodisc.core.library import Game, Library, Step
from retrodisc.core.steam import Steam
from retrodisc.ui.wizard.pages import (
    ConvertPage,
    DiscPage,
    ExtractPage,
    FinalizePage,
    InstallPage,
    SteamPage,
)


class GameWizard(QWizard):
    def __init__(
        self, library: Library, game: Game, steam: Steam | None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.game = game
        self.steam = steam
        self.setWindowTitle(self.tr("{name} — setup").format(name=game.name))
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.setOption(QWizard.WizardOption.NoCancelButton, False)
        self.setButtonText(QWizard.WizardButton.CancelButton, self.tr("Close"))
        self.resize(1100, 800)

        for step, page in (
            (Step.IMPORT, DiscPage()),
            (Step.CONVERT, ConvertPage()),
            (Step.EXTRACT, ExtractPage()),
            (Step.ADD_TO_STEAM, SteamPage()),
            (Step.INSTALL, InstallPage()),
            (Step.FINALIZE, FinalizePage()),
        ):
            self.setPage(step, page)

        current = game.step
        self.setStartId(Step.IMPORT if current is Step.CONVERT else min(current, Step.FINALIZE))

    def save(self) -> None:
        self.library.update(self.game)
