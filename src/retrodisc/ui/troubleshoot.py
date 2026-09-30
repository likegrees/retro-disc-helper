"""Troubleshooting dialog: runs the diagnostics and offers one-click fixes."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from retrodisc.core.diagnostics import CheckResult, Status, run_checks
from retrodisc.core.library import Game, Library
from retrodisc.core.steam import Steam, SteamRunningError
from retrodisc.ui.common import ensure_steam_closed, show_error
from retrodisc.ui.workers import run_with_progress

ICONS = {
    Status.OK: QStyle.StandardPixmap.SP_DialogApplyButton,
    Status.WARN: QStyle.StandardPixmap.SP_MessageBoxWarning,
    Status.FAIL: QStyle.StandardPixmap.SP_MessageBoxCritical,
    Status.SKIP: QStyle.StandardPixmap.SP_MediaSkipForward,
}


class TroubleshootDialog(QDialog):
    def __init__(
        self, library: Library, game: Game, steam: Steam | None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.library, self.game, self.steam = library, game, steam
        self.setWindowTitle(self.tr("Troubleshoot — {name}").format(name=game.name))
        self.resize(1100, 800)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        rerun = buttons.addButton(self.tr("Check again"), QDialogButtonBox.ButtonRole.ActionRole)
        rerun.clicked.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.addWidget(self.area, 1)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        try:
            results = run_checks(self.game, self.steam)
        except Exception as exc:
            show_error(self, exc)
            return
        content = QWidget()
        grid = QGridLayout(content)
        grid.setColumnStretch(1, 1)
        for row, result in enumerate(results):
            icon = QLabel()
            icon.setPixmap(self.style().standardIcon(ICONS[result.status]).pixmap(32, 32))
            text = QLabel(f"<b>{result.title}</b><br>{result.detail}")
            text.setWordWrap(True)
            grid.addWidget(icon, row, 0)
            grid.addWidget(text, row, 1)
            if result.fix is not None:
                button = QPushButton(result.fix_label or self.tr("Fix"))
                button.clicked.connect(lambda _=False, r=result: self._apply(r))
                grid.addWidget(button, row, 2)
        grid.setRowStretch(len(results), 1)
        self.area.setWidget(content)

    def _apply(self, result: CheckResult) -> None:
        fix = result.fix
        assert fix is not None
        try:
            try:
                run_with_progress(self, result.title, lambda _r: fix(), cancellable=False)
            except SteamRunningError:
                if not ensure_steam_closed(self):
                    return
                run_with_progress(self, result.title, lambda _r: fix(), cancellable=False)
        except Exception as exc:
            show_error(self, exc)
        self.library.update(self.game)
        self.refresh()
