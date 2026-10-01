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

from retrodisc.core import deep_scan
from retrodisc.core.diagnostics import CheckResult, Status, deep_scan_checks, run_checks
from retrodisc.core.library import Game, Library
from retrodisc.core.steam import Steam, SteamRunningError
from retrodisc.ui.common import ensure_steam_closed, show_error
from retrodisc.ui.workers import run_with_progress

ICONS = {
    Status.OK: QStyle.StandardPixmap.SP_DialogApplyButton,
    Status.WARN: QStyle.StandardPixmap.SP_MessageBoxWarning,
    Status.FAIL: QStyle.StandardPixmap.SP_MessageBoxCritical,
    Status.SKIP: QStyle.StandardPixmap.SP_MediaSkipForward,
    Status.INFO: QStyle.StandardPixmap.SP_MessageBoxInformation,
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
        self.deep_results: list[deep_scan.FileResult] | None = None
        deep = buttons.addButton(
            self.tr("Deep scan (Detect It Easy)"), QDialogButtonBox.ButtonRole.ActionRole
        )
        deep.clicked.connect(self._deep_scan)
        if not deep_scan.available():
            deep.setEnabled(False)
            deep.setToolTip(self.tr("Detect It Easy is not installed."))

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
        if self.deep_results is not None:
            # Detect It Easy supersedes the quick check: show protections only once.
            if any(not r.error for r in self.deep_results):
                results = [r for r in results if r.kind != "protection"]
            results = deep_scan_checks(self.deep_results, self.game) + results
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

    def _deep_scan(self) -> None:
        targets = deep_scan.targets_for(self.game)
        if not targets:
            show_error(self, self.tr("No program files to scan yet: extract the CD first."))
            return
        try:
            self.deep_results = run_with_progress(
                self,
                self.tr("Detect It Easy is scanning {n} file(s)…").format(n=len(targets)),
                lambda _r: deep_scan.scan(targets),
                cancellable=False,
            )
        except Exception as exc:
            show_error(self, exc)
            return
        self.refresh()

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
