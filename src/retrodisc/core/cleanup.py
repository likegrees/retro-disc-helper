"""Full clean-up of a game: everything the app created, never the original disc image."""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from retrodisc.core.cue import CueError, parse_cue
from retrodisc.core.library import Game
from retrodisc.core.steam import Steam
from retrodisc.i18n import tr


class Item(StrEnum):
    SHORTCUT = "shortcut"  # Steam entry + forced Proton version
    PREFIX = "prefix"  # compatdata/<appid>: installed game and save games
    CD = "cd"  # extracted CD folder
    ISO = "iso"  # converted .iso


@dataclass(frozen=True)
class Target:
    item: Item
    path: Path | None  # None for the Steam shortcut
    size: int | None = None
    blocked: str | None = None  # reason it will not be deleted


def source_files(game: Game) -> list[Path]:
    """The user's original disc image files (every disc), which are never deleted."""
    files: list[Path] = []
    for disc in game.discs:
        cue = Path(disc.cue)
        files.append(cue)
        with contextlib.suppress(CueError, OSError):
            files += {t.file for t in parse_cue(cue).tracks}
    return files


def protected_paths(game: Game, others: Iterable[Game]) -> list[Path]:
    paths = source_files(game)
    for other in others:
        if other.id == game.id:
            continue
        for disc in other.discs:
            paths += [Path(p) for p in (disc.cue, disc.iso, disc.cd_dir) if p]
    return paths


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def why_not_deletable(path: Path, protected: Iterable[Path]) -> str | None:
    """Refuse anything that could take the user's own files with it."""
    resolved = path.resolve()
    home = Path.home().resolve()
    if len(resolved.parts) < 3 or _is_within(home, resolved):
        return tr("{path} is a system or home folder").format(path=path)
    for p in protected:
        p = p.resolve()
        if _is_within(p, resolved) or _is_within(resolved, p):
            return tr("{path} contains or overlaps {other}").format(path=path, other=p)
    return None


def disk_usage(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def plan(game: Game, steam: Steam | None, others: Iterable[Game] = ()) -> list[Target]:
    """What a full clean-up of `game` would delete; only things that still exist."""
    protected = protected_paths(game, others)
    targets: list[Target] = []

    if steam is not None and game.appid is not None:
        if any(s.appid == game.appid for s in steam.shortcuts()):
            targets.append(Target(Item.SHORTCUT, None))
        compat = steam.compatdata(game.appid)
        if compat.is_dir():
            blocked = None
            if compat.name != str(game.appid) or compat.parent.name != "compatdata":
                blocked = tr("unexpected prefix location {path}").format(path=compat)
            size = None if blocked else disk_usage(compat)
            targets.append(Target(Item.PREFIX, compat, size, blocked))

    for disc in game.discs:
        if disc.cd_dir and Path(disc.cd_dir).is_dir():
            cd_dir = Path(disc.cd_dir)
            blocked = why_not_deletable(cd_dir, protected)
            # Never walk a folder that will not be deleted: it may be $HOME or "/".
            size = None if blocked else disk_usage(cd_dir)
            targets.append(Target(Item.CD, cd_dir, size, blocked))

    for disc in game.discs:
        if disc.iso and Path(disc.iso).is_file():
            iso = Path(disc.iso)
            blocked = None
            if any(iso.resolve() == p.resolve() for p in protected):
                blocked = tr("{path} is an original disc file").format(path=iso)
            targets.append(Target(Item.ISO, iso, iso.stat().st_size, blocked))

    return targets


def perform(
    game: Game, steam: Steam | None, items: set[Item], others: Iterable[Game] = ()
) -> list[str]:
    """Delete the selected items. Returns a message for each item that could not be removed.

    Steam must be closed when Item.SHORTCUT is selected (SteamRunningError otherwise).
    """
    errors: list[str] = []
    targets = plan(game, steam, others)

    # Steam first: if Steam is running this raises before any file is touched.
    if Item.SHORTCUT in items and steam is not None and game.appid is not None:
        steam.remove_compat_tool(game.appid)
        steam.remove_shortcut(game.appid)

    for target in targets:
        if target.item not in items or target.item is Item.SHORTCUT or target.path is None:
            continue
        if target.blocked:
            errors.append(target.blocked)
            continue
        try:
            if target.path.is_dir():
                shutil.rmtree(target.path)
            else:
                target.path.unlink()
        except OSError as exc:
            errors.append(f"{target.path}: {exc.strerror or exc}")

    # Remove the per-game folder ~/Documents/games/<Name> if nothing else is left in it.
    parents = {Path(p).parent for d in game.discs for p in (d.iso, d.cd_dir) if p}
    for folder in {game.folder, *parents}:
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass
    return errors
