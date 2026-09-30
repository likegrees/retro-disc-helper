"""Wizard pages, one per step of the guide's Proton path."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from retrodisc.core import convert, exe_inspect, iso, prefix, protection
from retrodisc.core.cue import CueError, CueSheet, parse_cue
from retrodisc.core.library import Game, safe_name
from retrodisc.core.steam import Shortcut, Steam
from retrodisc.ui.common import show_error, start_game, with_steam_closed
from retrodisc.ui.workers import Reporter, run_with_progress

if TYPE_CHECKING:
    from retrodisc.ui.wizard.game_wizard import GameWizard


def _label(text: str = "", kind: str | None = None) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if kind:
        label.setObjectName(kind)
    return label


def _row(*widgets: QWidget, stretch_first: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    for i, w in enumerate(widgets):
        row.addWidget(w, 1 if stretch_first and i == 0 else 0)
    return row


class BasePage(QWizardPage):
    @property
    def gw(self) -> GameWizard:
        return cast("GameWizard", self.wizard())

    @property
    def game(self) -> Game:
        return self.gw.game

    def save(self) -> None:
        self.gw.save()
        self.completeChanged.emit()


# ---- 1. Disc -------------------------------------------------------------------------------


class DiscPage(BasePage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Disc image"))
        self.setSubTitle(self.tr("Check the tracks found in the .cue file and name the game."))
        self.sheet: CueSheet | None = None

        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self.completeChanged)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr("Track"), self.tr("Type"), self.tr("File")])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.info = _label(kind="warn")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Game name")))
        layout.addWidget(self.name_edit)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.info)

    def initializePage(self) -> None:
        self.name_edit.setText(self.game.name)
        try:
            self.sheet = parse_cue(Path(self.game.cue))
        except (CueError, OSError) as exc:
            self.sheet = None
            self.info.setObjectName("error")
            self.info.setText(str(exc))
            return
        self.table.setRowCount(len(self.sheet.tracks))
        for row, track in enumerate(self.sheet.tracks):
            kind = (
                self.tr("Data ({mode})").format(mode=track.mode)
                if track.is_data
                else self.tr("Audio")
            )
            for col, text in enumerate((f"{track.number:02d}", kind, track.file.name)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        notes = []
        if self.sheet.data_track is None:
            notes.append(self.tr("No data track: this is an audio CD, not a game disc."))
        if self.sheet.audio_tracks:
            notes.append(
                self.tr(
                    "{n} audio track(s): CD music will not play under Proton. If the music "
                    "matters, 86Box is the better choice."
                ).format(n=len(self.sheet.audio_tracks))
            )
        self.info.setText("\n".join(notes))

    def isComplete(self) -> bool:
        return (
            self.sheet is not None
            and self.sheet.data_track is not None
            and bool(self.name_edit.text().strip())
        )

    def validatePage(self) -> bool:
        self.game.name = self.name_edit.text().strip()
        self.gw.save()
        return True


# ---- 2. Convert ----------------------------------------------------------------------------


class ConvertPage(BasePage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Create the ISO"))
        self.setSubTitle(
            self.tr("The data track is converted to a standard ISO. The original files are kept.")
        )
        self.path_edit = QLineEdit()
        browse = QPushButton(self.tr("Change…"))
        browse.clicked.connect(self._browse)
        self.button = QPushButton(self.tr("Create ISO"))
        self.button.clicked.connect(self._convert)
        self.status = _label()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("ISO file")))
        layout.addLayout(_row(self.path_edit, browse))
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status)
        layout.addStretch()

    def initializePage(self) -> None:
        default = self.game.folder / f"{safe_name(self.game.name)}.iso"
        self.path_edit.setText(self.game.iso or str(default))
        self._refresh()

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save ISO as"), self.path_edit.text(), "ISO (*.iso)"
        )
        if path:
            self.path_edit.setText(path)

    def _convert(self) -> None:
        output = Path(self.path_edit.text()).expanduser()
        try:
            sheet = parse_cue(Path(self.game.cue))
            output.parent.mkdir(parents=True, exist_ok=True)

            def job(report: Reporter) -> Path:
                return convert.convert_to_iso(
                    sheet, output, progress=lambda done, total: report(done, total, "")
                )

            run_with_progress(self, self.tr("Creating ISO…"), job)
        except Exception as exc:
            show_error(self, exc)
            return
        self.game.iso = str(output)
        self.save()
        self._refresh()

    def _refresh(self) -> None:
        iso_path = Path(self.game.iso) if self.game.iso else None
        if iso_path and convert.is_valid_iso(iso_path):
            info = iso.volume_info(iso_path)
            self.status.setObjectName("ok")
            self.status.setText(
                self.tr("ISO ready. Volume label: {label}, serial: {serial}").format(
                    label=info.label, serial=info.serial
                )
            )
            self.button.setText(self.tr("Create again"))
        else:
            self.status.setText("")
        self.status.style().polish(self.status)

    def isComplete(self) -> bool:
        return self.game.iso is not None and convert.is_valid_iso(Path(self.game.iso))


# ---- 3. Extract ----------------------------------------------------------------------------

NO_INSTALLER = ""


class ExtractPage(BasePage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Extract the CD"))
        self.setSubTitle(
            self.tr("Keep this folder: it is also used as the game's CD when it asks for it.")
        )
        self.path_edit = QLineEdit()
        browse = QPushButton(self.tr("Change…"))
        browse.clicked.connect(self._browse)
        self.button = QPushButton(self.tr("Extract"))
        self.button.clicked.connect(self._extract)

        self.installer = QComboBox()
        self.installer.currentIndexChanged.connect(self._installer_changed)
        self.warnings = _label(kind="warn")
        self.cd_check = QCheckBox(self.tr("The game checks for its CD (set up drive S: later)"))
        self.cd_check.toggled.connect(self._cd_toggled)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 12, 0, 0)
        details_layout.addWidget(QLabel(self.tr("Installer")))
        details_layout.addWidget(self.installer)
        details_layout.addWidget(self.warnings)
        details_layout.addWidget(self.cd_check)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Destination folder")))
        layout.addLayout(_row(self.path_edit, browse))
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.details)
        layout.addStretch()

    def initializePage(self) -> None:
        self.path_edit.setText(self.game.cd_dir or str(self.game.folder / "cd"))
        self.cd_check.setChecked(self.game.cd_drive)
        self._refresh()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr("Extract to"), self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def _extract(self) -> None:
        dest = Path(self.path_edit.text()).expanduser()
        assert self.game.iso is not None
        iso_path = Path(self.game.iso)
        try:

            def job(report: Reporter) -> Path:
                return iso.extract(iso_path, dest, progress=report)

            run_with_progress(self, self.tr("Extracting…"), job)
        except Exception as exc:
            show_error(self, exc)
            return
        self.game.cd_dir = str(dest)
        self.game.installer = None
        self.save()
        self._refresh()

    def _refresh(self) -> None:
        cd_dir = Path(self.game.cd_dir) if self.game.cd_dir else None
        extracted = cd_dir is not None and cd_dir.is_dir()
        self.details.setVisible(extracted)
        if not extracted or cd_dir is None:
            return
        self.button.setText(self.tr("Extract again"))

        self.installer.blockSignals(True)
        self.installer.clear()
        for candidate in exe_inspect.find_installers(cd_dir):
            self.installer.addItem(str(candidate.relative_to(cd_dir)), str(candidate))
        self.installer.addItem(self.tr("No installer: the game runs from the CD"), NO_INSTALLER)
        current = NO_INSTALLER if self.game.run_from_cd else self.game.installer
        index = self.installer.findData(current) if current is not None else 0
        self.installer.setCurrentIndex(max(index, 0))
        self.installer.blockSignals(False)
        self._installer_changed()

    def _installer_changed(self) -> None:
        data = self.installer.currentData()
        if data is None:
            return
        self.game.run_from_cd = data == NO_INSTALLER
        self.game.installer = None if self.game.run_from_cd else data
        self.gw.save()
        self._update_warnings()
        self.completeChanged.emit()

    def _cd_toggled(self, checked: bool) -> None:
        self.game.cd_drive = checked
        self.gw.save()

    def _update_warnings(self) -> None:
        assert self.game.cd_dir is not None
        cd_dir = Path(self.game.cd_dir)
        notes: list[str] = []
        if self.game.installer:
            installer = Path(self.game.installer)
            if exe_inspect.exe_kind(installer) is exe_inspect.ExeKind.WIN16:
                notes.append(
                    self.tr(
                        '{name} is a 16-bit program: Proton cannot run it. Choose "No '
                        'installer" to start the game directly from the CD, or use 86Box.'
                    ).format(name=installer.name)
                )
            elif installer.name.lower().startswith("auto"):
                notes.append(
                    self.tr(
                        "Autorun menus often close when you click Install. Prefer setup.exe "
                        "if it is in the list."
                    )
                )
        for hit in protection.scan(cd_dir):
            notes.append(
                self.tr(
                    "{name} copy protection detected ({files}). Wine usually cannot run it: "
                    "if the game refuses to start, look for the GOG release or use 86Box."
                ).format(name=hit.name, files=", ".join(hit.files))
            )
            self.cd_check.setChecked(True)
        self.warnings.setText("\n\n".join(notes))

    def isComplete(self) -> bool:
        return (
            self.game.cd_dir is not None
            and Path(self.game.cd_dir).is_dir()
            and (self.game.installer is not None or self.game.run_from_cd)
        )


# ---- 4. Steam ------------------------------------------------------------------------------


class SteamPage(BasePage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Add to Steam"))
        self.setSubTitle(
            self.tr("The game is added as a non-Steam game, forced to run with Proton.")
        )
        self.steam_info = _label()
        self.proton = QComboBox()
        self.button = QPushButton(self.tr("Add to Steam"))
        self.button.clicked.connect(self._add)
        self.status = _label(kind="ok")

        layout = QVBoxLayout(self)
        layout.addWidget(self.steam_info)
        layout.addWidget(QLabel(self.tr("Proton version")))
        layout.addWidget(self.proton)
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status)
        layout.addStretch()

    def initializePage(self) -> None:
        steam = self.gw.steam
        self.proton.clear()
        if steam is None:
            self.steam_info.setObjectName("error")
            self.steam_info.setText(self.tr("Steam was not found. Install and log in to Steam."))
            self.button.setEnabled(False)
            return
        self.steam_info.setText(
            self.tr("Steam: {root} (user {user})").format(root=steam.root, user=steam.user_id)
        )
        for tool in steam.proton_tools():
            self.proton.addItem(tool.display_name, tool.name)
        if self.game.proton:
            self.proton.setCurrentIndex(max(self.proton.findData(self.game.proton), 0))
        self._refresh()

    def _target(self) -> Path:
        if self.game.installer:
            return Path(self.game.installer)
        assert self.game.cd_dir is not None
        exes = exe_inspect.find_exes(Path(self.game.cd_dir))
        return exes[0] if exes else Path(self.game.cd_dir) / "game.exe"

    def _add(self) -> None:
        steam = self.gw.steam
        tool = self.proton.currentData()
        if steam is None or tool is None:
            show_error(self, self.tr("No Proton version installed. Install Proton from Steam."))
            return

        def action() -> Shortcut:
            if self.game.appid is not None:
                sc = steam.update_shortcut(self.game.appid, exe=self._target())
            else:
                sc = steam.add_shortcut(self.game.name, self._target())
            steam.set_compat_tool(sc.appid, tool)
            return sc

        try:
            shortcut = with_steam_closed(self, action)
        except Exception as exc:
            show_error(self, exc)
            return
        if shortcut is None:
            return
        self.game.appid = shortcut.appid
        self.game.proton = tool
        self.save()
        self._refresh()

    def _refresh(self) -> None:
        if self.game.appid is not None:
            self.status.setText(
                self.tr("Added to Steam (id {appid}) with {proton}.").format(
                    appid=self.game.appid, proton=self.game.proton
                )
            )

    def isComplete(self) -> bool:
        return self.game.appid is not None


# ---- 5. Install ----------------------------------------------------------------------------


class InstallPage(BasePage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Install"))
        self.help = _label()
        self.button = QPushButton(self.tr("Run the installer from Steam"))
        self.button.clicked.connect(self._run)
        self.status = _label(kind="ok")
        self.done = QCheckBox(self.tr("The installation finished"))
        self.done.toggled.connect(self._done_toggled)

        layout = QVBoxLayout(self)
        layout.addWidget(self.help)
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.status)
        layout.addWidget(self.done)
        layout.addStretch()

    def initializePage(self) -> None:
        if self.game.run_from_cd:
            self.help.setText(self.tr("Nothing to install: the game runs from the CD folder."))
            self.button.hide()
            self.done.hide()
            self.game.installed = True
            self.save()
            return
        self.button.show()
        self.done.show()
        self.help.setText(
            self.tr(
                "Steam starts the installer with Proton. Keep the default install path and "
                "accept DirectX if it is offered.\n\n"
                "• The first start can take a minute while Proton prepares its files.\n"
                "• If the installer closes immediately, run Troubleshoot from the main window.\n\n"
                "When the installer is done, come back here and tick the box below."
            )
        )
        self.done.setChecked(self.game.installed)

    def _run(self) -> None:
        assert self.game.appid is not None
        self.game.install_started = self.game.install_started or time.time()
        self.gw.save()
        if start_game(self, self.gw.steam, self.game.appid):
            self.status.setText(
                self.tr(
                    "Steam is starting the installer. If Steam was closed it can take a "
                    "minute to open; the installer window appears after that."
                )
            )

    def _done_toggled(self, checked: bool) -> None:
        self.game.installed = checked
        self.save()

    def isComplete(self) -> bool:
        return self.game.installed


# ---- 6. Finalize ---------------------------------------------------------------------------


def _installed_exes(steam: Steam, game: Game) -> list[Path]:
    assert game.appid is not None
    drive_c = steam.prefix(game.appid) / "drive_c"
    if not drive_c.is_dir():
        return []
    skip = {"windows", "programdata", "users"}
    return [
        p
        for p in exe_inspect.find_exes(drive_c, newer_than=game.install_started)
        if p.relative_to(drive_c).parts[0].lower() not in skip
    ]


class FinalizePage(BasePage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle(self.tr("Set up the game"))
        self.setSubTitle(self.tr("Choose the game's executable to replace the installer in Steam."))
        self.exes = QListWidget()
        self.exes.currentItemChanged.connect(self.completeChanged)
        browse = QPushButton(self.tr("Browse…"))
        browse.clicked.connect(self._browse)
        self.name_edit = QLineEdit()

        self.cd_box = QWidget()
        cd_layout = QVBoxLayout(self.cd_box)
        cd_layout.setContentsMargins(0, 8, 0, 0)
        self.cd_status = _label()
        self.cd_button = QPushButton(self.tr("Set up drive S:"))
        self.cd_button.clicked.connect(self._setup_cd)
        cd_layout.addWidget(QLabel(self.tr("CD drive")))
        cd_layout.addWidget(self.cd_status)
        cd_layout.addWidget(self.cd_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.apply = QPushButton(self.tr("Update Steam shortcut"))
        self.apply.clicked.connect(self._apply)
        self.play = QPushButton(self.tr("Play"))
        self.play.clicked.connect(self._play)
        self.status = _label(kind="ok")

        layout = QVBoxLayout(self)
        layout.addLayout(_row(QLabel(self.tr("Game executable")), browse))
        layout.addWidget(self.exes, 1)
        layout.addWidget(QLabel(self.tr("Name in Steam")))
        layout.addWidget(self.name_edit)
        layout.addWidget(self.cd_box)
        layout.addLayout(_row(self.status, self.apply, self.play))

    def initializePage(self) -> None:
        self.name_edit.setText(self.game.name)
        self.exes.clear()
        steam = self.gw.steam
        candidates: list[tuple[Path, str]] = []
        if steam is not None and not self.game.run_from_cd:
            candidates += [(p, self.tr("installed")) for p in _installed_exes(steam, self.game)]
        if self.game.cd_dir:
            cd_dir = Path(self.game.cd_dir)
            candidates += [
                (p, self.tr("from the CD, runs from S:"))
                for p in exe_inspect.find_exes(cd_dir)
                if not p.name.lower().startswith(("setup", "install", "autorun"))
            ]
        for path, origin in candidates:
            self._add_exe(path, origin)
        if self.game.exe:
            self._select(Path(self.game.exe))
        elif self.exes.count():
            self.exes.setCurrentRow(0)
        self._refresh_cd()
        self._refresh()

    def _add_exe(self, path: Path, origin: str) -> QListWidgetItem:
        item = QListWidgetItem(f"{path.name}  ({origin})\n{path.parent}")
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        self.exes.addItem(item)
        return item

    def _select(self, path: Path) -> None:
        for i in range(self.exes.count()):
            if self.exes.item(i).data(Qt.ItemDataRole.UserRole) == str(path):
                self.exes.setCurrentRow(i)
                return
        self.exes.setCurrentItem(self._add_exe(path, self.tr("chosen")))

    def _browse(self) -> None:
        start = self.game.cd_dir or str(Path.home())
        if self.gw.steam is not None and self.game.appid is not None:
            drive_c = self.gw.steam.prefix(self.game.appid) / "drive_c"
            if drive_c.is_dir():
                start = str(drive_c)
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Game executable"), start, self.tr("Programs (*.exe *.EXE)")
        )
        if path:
            self._select(Path(path))

    def _selected_exe(self) -> Path | None:
        item = self.exes.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _apply(self) -> None:
        steam, exe = self.gw.steam, self._selected_exe()
        if steam is None or exe is None or self.game.appid is None:
            return
        name = self.name_edit.text().strip() or self.game.name
        appid = self.game.appid
        try:
            result = with_steam_closed(
                self, lambda: steam.update_shortcut(appid, name=name, exe=exe)
            )
        except Exception as exc:
            show_error(self, exc)
            return
        if result is None:
            return
        self.game.exe = str(exe)
        self.game.name = name
        self.save()
        self._refresh()

    def _play(self) -> None:
        start_game(self, self.gw.steam, self.game.appid)

    def _setup_cd(self) -> None:
        steam = self.gw.steam
        if steam is None or self.game.appid is None or not self.game.cd_dir or not self.game.iso:
            return
        try:
            prefix.setup_cd_drive(
                steam.prefix(self.game.appid),
                Path(self.game.cd_dir),
                iso.volume_info(Path(self.game.iso)),
            )
        except Exception as exc:
            show_error(self, exc)
            return
        self.game.cd_drive = True
        self.save()
        self._refresh_cd()

    def _refresh_cd(self) -> None:
        steam = self.gw.steam
        show = (
            steam is not None
            and self.game.appid is not None
            and (self.game.cd_drive or self.game.run_from_cd)
        )
        self.cd_box.setVisible(show)
        if not show or steam is None or self.game.appid is None:
            return
        cd_dir = Path(self.game.cd_dir) if self.game.cd_dir else None
        status = prefix.cd_drive_status(steam.prefix(self.game.appid), cd_dir)
        if status.ok:
            self.cd_status.setText(
                self.tr("S: is the game CD ({label}, serial {serial}).").format(
                    label=status.label, serial=status.serial
                )
            )
            self.cd_button.setText(self.tr("Set up drive S: again"))
        elif not status.prefix_exists:
            self.cd_status.setText(
                self.tr("Press Play once and close the game: Proton needs to create its files.")
            )
        else:
            self.cd_status.setText(
                self.tr("Makes the extracted folder look like the original CD to the game.")
            )

    def _refresh(self) -> None:
        self.play.setEnabled(self.game.appid is not None)
        if self.game.exe:
            self.status.setText(
                self.tr("Steam now launches {exe}.").format(exe=Path(self.game.exe).name)
            )

    def isComplete(self) -> bool:
        return self.game.exe is not None
