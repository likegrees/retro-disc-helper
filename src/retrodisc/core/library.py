"""Per-game wizard state, persisted as JSON so a game can be resumed after installing."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import IntEnum
from pathlib import Path
from typing import Any

from retrodisc.i18n import tr


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "retrodisc"


def default_games_dir() -> Path:
    return Path.home() / "Documents" / "games"


class Step(IntEnum):
    IMPORT = 0
    CONVERT = 1
    EXTRACT = 2
    ADD_TO_STEAM = 3
    INSTALL = 4
    FINALIZE = 5
    DONE = 6


@dataclass
class Disc:
    cue: str
    iso: str | None = None
    cd_dir: str | None = None

    @property
    def converted(self) -> bool:
        return self.iso is not None and Path(self.iso).is_file()

    @property
    def extracted(self) -> bool:
        return self.cd_dir is not None and Path(self.cd_dir).is_dir()


@dataclass
class Game:
    """A game. Disc 1 lives in `cue`/`iso`/`cd_dir` (the original single-disc format)."""

    name: str
    cue: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    iso: str | None = None
    cd_dir: str | None = None
    installer: str | None = None
    appid: int | None = None
    proton: str | None = None
    install_started: float | None = None
    installed: bool = False
    exe: str | None = None
    run_from_cd: bool = False
    cd_drive: bool = False
    notes: list[str] = field(default_factory=list)
    more_discs: list[Disc] = field(default_factory=list)  # disc 2, 3, ...
    current_disc: int = 0  # index of the disc drive S: shows

    @property
    def discs(self) -> list[Disc]:
        """All discs in order. Disc 1 is a snapshot: change it with update_disc()."""
        return [Disc(self.cue, self.iso, self.cd_dir), *self.more_discs]

    @property
    def multi_disc(self) -> bool:
        return bool(self.more_discs)

    def update_disc(self, index: int, disc: Disc) -> None:
        if index == 0:
            self.cue, self.iso, self.cd_dir = disc.cue, disc.iso, disc.cd_dir
        else:
            self.more_discs[index - 1] = disc

    def set_discs(self, cues: list[str]) -> None:
        """Replace the disc list, keeping conversion/extraction of discs that stay."""
        known = {d.cue: d for d in self.discs}
        discs = [known.get(c, Disc(c)) for c in cues]
        self.update_disc(0, discs[0])
        self.more_discs = discs[1:]
        self.current_disc = min(self.current_disc, len(discs) - 1)

    def disc_label(self, index: int) -> str:
        return tr("Disc {n}").format(n=index + 1)

    def default_iso(self, index: int) -> Path:
        suffix = f" (Disc {index + 1})" if self.multi_disc else ""
        return self.folder / f"{safe_name(self.name)}{suffix}.iso"

    def default_cd_dir(self, index: int) -> Path:
        return self.folder / (f"cd{index + 1}" if self.multi_disc else "cd")

    @property
    def step(self) -> Step:
        """First unfinished step."""
        if not all(d.converted for d in self.discs):
            return Step.CONVERT
        if not all(d.extracted for d in self.discs):
            return Step.EXTRACT
        if self.appid is None:
            return Step.ADD_TO_STEAM
        if not self.installed:
            return Step.INSTALL
        if self.exe is None:
            return Step.FINALIZE
        return Step.DONE

    @property
    def folder(self) -> Path:
        """Where this game's ISO and extracted CD live."""
        return default_games_dir() / safe_name(self.name)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Game:
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        values["more_discs"] = [Disc(**d) for d in values.get("more_discs", [])]
        return cls(**values)


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return cleaned or "game"


def pretty_name(cue_stem: str) -> str:
    """'Sub Culture (Europe) (En,Fr,De)' -> 'Sub Culture'."""
    return re.sub(r"\s*[(\[].*$", "", cue_stem).strip() or cue_stem


class Library:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "games.json"
        self.games: list[Game] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.games = []
            return
        raw = json.loads(self.path.read_text())
        self.games = [Game.from_dict(g) for g in raw.get("games", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"games": [asdict(g) for g in self.games]}, indent=2))
        tmp.replace(self.path)

    def add(self, game: Game) -> Game:
        self.games.append(game)
        self.save()
        return game

    def remove(self, game_id: str) -> None:
        self.games = [g for g in self.games if g.id != game_id]
        self.save()

    def get(self, game_id: str) -> Game | None:
        return next((g for g in self.games if g.id == game_id), None)

    def update(self, game: Game) -> None:
        self.games = [game if g.id == game.id else g for g in self.games]
        self.save()
