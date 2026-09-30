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
class Game:
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

    @property
    def step(self) -> Step:
        """First unfinished step."""
        if self.iso is None or not Path(self.iso).exists():
            return Step.CONVERT
        if self.cd_dir is None or not Path(self.cd_dir).is_dir():
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
        return cls(**{k: v for k, v in raw.items() if k in known})


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
