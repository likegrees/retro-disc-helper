"""Troubleshooting checks, based on the "Problemi comuni" table of the guide (Proton part)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from retrodisc.core import convert, exe_inspect, iso, prefix, protection
from retrodisc.core.cue import CueError, CueSheet, parse_cue
from retrodisc.core.library import Game
from retrodisc.core.steam import Steam, SteamError, is_steam_running
from retrodisc.i18n import tr


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    title: str
    status: Status
    detail: str = ""
    fix: Callable[[], None] | None = None
    fix_label: str = ""


def run_checks(game: Game, steam: Steam | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    add = results.append

    def titled(title: str, index: int) -> str:
        return f"{title} ({game.disc_label(index)})" if game.multi_disc else title

    audio_tracks = 0
    hits: list[protection.ProtectionHit] = []
    for index, disc in enumerate(game.discs):
        # --- the disc image --------------------------------------------------------------
        sheet: CueSheet | None = None
        try:
            sheet = parse_cue(Path(disc.cue))
        except (CueError, OSError) as exc:
            add(CheckResult(titled(tr("Cue sheet readable"), index), Status.FAIL, str(exc)))
        else:
            audio_tracks += len(sheet.audio_tracks)
            add(
                CheckResult(
                    titled(tr("Cue sheet readable"), index),
                    Status.OK,
                    tr("{n} track(s)").format(n=len(sheet.tracks)),
                )
            )
        results.append(_iso_check(titled(tr("ISO image"), index), disc.iso, sheet))

        # --- extracted CD ----------------------------------------------------------------
        title = titled(tr("Extracted CD"), index)
        if not disc.extracted:
            missing = disc.cd_dir is None
            add(
                CheckResult(
                    title,
                    Status.SKIP if missing else Status.FAIL,
                    tr("Not extracted yet") if missing else str(disc.cd_dir),
                )
            )
            continue
        cd_dir = Path(str(disc.cd_dir))
        add(CheckResult(title, Status.OK, str(cd_dir)))
        if index == 0:
            results += _installer_checks(game, cd_dir, steam)
        hits += protection.scan(cd_dir)

    # Newer protections live inside the installed .exe (unpacked from the installer), not on
    # the disc: check the game that was actually installed as well.
    if game.exe and Path(game.exe).is_file():
        exe = Path(game.exe)
        hits += protection.scan(exe.parent, max_depth=1, extra_exes=[exe])

    if audio_tracks:
        add(
            CheckResult(
                tr("CD audio tracks"),
                Status.WARN,
                tr(
                    "{n} audio track(s): the CD music will not play under Proton. "
                    "Use 86Box if the music matters."
                ).format(n=audio_tracks),
            )
        )
    for hit in protection.merge(hits):
        add(
            CheckResult(
                tr("Copy protection: {name}").format(name=hit.label),
                Status.WARN,
                protection_advice(", ".join(hit.files)),
            )
        )

    # --- Steam ---------------------------------------------------------------------------
    if steam is None:
        add(CheckResult(tr("Steam"), Status.FAIL, tr("Steam installation not found")))
        return results
    if game.appid is None:
        add(CheckResult(tr("Steam shortcut"), Status.SKIP, tr("Not added to Steam yet")))
        return results

    shortcut = next((s for s in steam.shortcuts() if s.appid == game.appid), None)
    if shortcut is None:
        add(
            CheckResult(
                tr("Steam shortcut"),
                Status.FAIL,
                tr("The shortcut was removed from Steam. Add the game again."),
            )
        )
        return results
    exe_ok = shortcut.exe.exists()
    add(
        CheckResult(
            tr("Steam shortcut"),
            Status.OK if exe_ok else Status.FAIL,
            tr("{name} → {exe}").format(name=shortcut.name, exe=shortcut.exe)
            if exe_ok
            else tr("Target does not exist: {exe}").format(exe=shortcut.exe),
        )
    )

    try:
        tool = steam.compat_tool(game.appid)
    except SteamError as exc:
        tool = None
        add(CheckResult(tr("Proton forced"), Status.FAIL, str(exc)))
    else:
        tools = steam.proton_tools()
        proton_fix: Callable[[], None] | None = None
        if tool is None and tools:
            best, appid = tools[0].name, game.appid

            def _fix_proton() -> None:
                steam.set_compat_tool(appid, best)

            proton_fix = _fix_proton
        add(
            CheckResult(
                tr("Proton forced"),
                Status.OK if tool else Status.FAIL,
                tool
                or tr("No compatibility tool set: Steam would try to run a Windows .exe natively."),
                proton_fix,
                tr("Use {tool}").format(tool=tools[0].display_name) if proton_fix else "",
            )
        )

    if game.cd_drive or game.run_from_cd or game.multi_disc:
        results.append(_cd_drive_check(game, steam))

    if game.windows_version is not None:
        results.append(_windows_version_check(game, steam))

    if is_steam_running():
        add(
            CheckResult(
                tr("Steam running"),
                Status.WARN,
                tr("Fixes that change Steam's configuration need Steam to be closed."),
            )
        )
    return results


def protection_advice(files: str | None = None) -> str:
    advice = tr(
        "Proton cannot pass this protection's disc check, so the game reports that no disc "
        "is inserted even with drive R: set up. Look for an official patch or re-release "
        "without the disc check, or use 86Box."
    )
    return tr("Found in {files}.").format(files=files) + " " + advice if files else advice


def _iso_check(title: str, iso_value: str | None, sheet: CueSheet | None) -> CheckResult:
    if iso_value is None:
        return CheckResult(title, Status.SKIP, tr("Not converted yet"))
    iso_path = Path(iso_value)
    if convert.is_valid_iso(iso_path):
        return CheckResult(title, Status.OK, str(iso_path))
    fix: Callable[[], None] | None = None
    if sheet is not None:
        sheet_ = sheet

        def _fix() -> None:
            convert.convert_to_iso(sheet_, iso_path)

        fix = _fix
    return CheckResult(
        title,
        Status.FAIL,
        tr(
            "Missing or not a valid ISO9660 image. Archive tools cannot open the .cue "
            'directly ("No suitable plugin found"); they need the converted .iso.'
        ),
        fix,
        tr("Convert again"),
    )


def _installer_checks(game: Game, cd_dir: Path, steam: Steam | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    candidates = exe_inspect.find_installers(cd_dir)
    installer = Path(game.installer) if game.installer else (candidates[0] if candidates else None)
    if installer is None:
        results.append(
            CheckResult(
                tr("Installer"),
                Status.WARN,
                tr("No setup.exe found. The game may run straight from the CD folder."),
            )
        )
        return results

    setup = next((c for c in candidates if not c.name.lower().startswith("auto")), None)
    if installer.name.lower().startswith("auto") and setup is not None:
        fix: Callable[[], None] | None = None
        if steam is not None and game.appid is not None and not game.installed:
            steam_, appid, setup_ = steam, game.appid, setup

            def _fix() -> None:
                steam_.update_shortcut(appid, exe=setup_)

            fix = _fix
        results.append(
            CheckResult(
                tr("Installer"),
                Status.WARN,
                tr(
                    "{autorun} often closes when you click Install. Launch {setup} directly."
                ).format(autorun=installer.name, setup=setup.relative_to(cd_dir)),
                fix,
                tr("Use {setup}").format(setup=setup.name),
            )
        )
    else:
        results.append(CheckResult(tr("Installer"), Status.OK, str(installer.relative_to(cd_dir))))

    if exe_inspect.exe_kind(installer) is exe_inspect.ExeKind.WIN16:
        results.append(
            CheckResult(
                tr("16-bit installer"),
                Status.FAIL,
                tr(
                    "{name} is a 16-bit program and Proton cannot run it. Try launching the game's "
                    ".exe directly from the CD folder, or use 86Box."
                ).format(name=installer.name),
            )
        )
    return results


def _cd_drive_check(game: Game, steam: Steam) -> CheckResult:
    assert game.appid is not None
    pfx = steam.prefix(game.appid)
    disc = game.discs[game.current_disc]
    cd_dir = Path(disc.cd_dir) if disc.extracted and disc.cd_dir else None
    status = prefix.cd_drive_status(pfx, cd_dir)
    title = tr("Drive R: (CD)")
    if game.multi_disc:
        title = tr("Drive R: ({disc} inserted)").format(disc=game.disc_label(game.current_disc))
    if not status.prefix_exists:
        return CheckResult(
            title, Status.FAIL, tr("Launch the game once from Steam so Proton creates its prefix.")
        )
    legacy = any(
        prefix.registry_drive_type(pfx, old) == "cdrom" for old in prefix.LEGACY_CD_LETTERS
    )
    if (
        status.ok
        and not legacy
        and not prefix.repoint_install_paths(pfx, game.cd_dirs(), dry_run=True)
    ):
        return CheckResult(
            title,
            Status.OK,
            tr("{label} ({serial}) → {target}").format(
                label=status.label, serial=status.serial, target=status.link_target
            ),
        )
    problems = []
    if not status.link_ok:
        problems.append(tr('R: does not point to the extracted CD ("File not found")'))
    if status.label is None or status.serial is None:
        problems.append(tr("label/serial files missing"))
    if not status.registry_ok:
        problems.append(tr("R: is not marked as a CD-ROM in the registry"))
    if any(prefix.registry_drive_type(pfx, old) == "cdrom" for old in prefix.LEGACY_CD_LETTERS):
        problems.append(
            tr("set up as drive S: by an older version, which Proton removes at every launch")
        )
    stale = prefix.repoint_install_paths(pfx, game.cd_dirs(), dry_run=True)
    if stale:
        problems.append(
            tr(
                "the installer recorded the CD as a folder on Z: ({n} registry entries), "
                "so the game may not find its disc"
            ).format(n=stale)
        )
    fix: Callable[[], None] | None = None
    if cd_dir is not None and disc.converted and disc.iso:
        info, cd = iso.volume_info(Path(disc.iso)), cd_dir

        def _fix() -> None:
            prefix.setup_cd_drive(pfx, cd, info, game.cd_dirs())

        fix = _fix
    return CheckResult(title, Status.FAIL, "; ".join(problems), fix, tr("Repair drive R:"))


def _windows_version_check(game: Game, steam: Steam) -> CheckResult:
    assert game.appid is not None and game.windows_version is not None
    wanted = game.windows_version
    label = prefix.WINDOWS_VERSIONS.get(wanted, wanted)
    title = tr("Windows version")
    pfx = steam.prefix(game.appid)
    if not (pfx / "user.reg").exists():
        return CheckResult(
            title, Status.FAIL, tr("Launch the game once from Steam so Proton creates its prefix.")
        )
    current = prefix.windows_version(pfx)
    if current == wanted:
        return CheckResult(title, Status.OK, tr("The game sees {version}.").format(version=label))

    def _fix() -> None:
        prefix.set_windows_version(pfx, wanted)

    return CheckResult(
        title,
        Status.FAIL,
        tr("Set to {version}, but the prefix reports {current}.").format(
            version=label,
            current=prefix.WINDOWS_VERSIONS.get(current or "", current or "Windows 10"),
        ),
        _fix,
        tr("Set {version} again").format(version=label),
    )
