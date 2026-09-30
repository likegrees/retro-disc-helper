"""CUE sheet parsing: find the data track and its byte range inside the .bin files."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

FRAMES_PER_SECOND = 75
SECONDS_PER_MINUTE = 60

# Sector size in the .bin and offset of the 2048 bytes of user data inside each sector.
MODE_LAYOUT: dict[str, tuple[int, int]] = {
    "MODE1/2048": (2048, 0),
    "MODE1/2352": (2352, 16),
    "MODE2/2336": (2336, 8),
    "MODE2/2352": (2352, 24),
    "AUDIO": (2352, 0),
}

USER_DATA_SIZE = 2048


class CueError(Exception):
    """Raised when a CUE sheet cannot be parsed or references missing files."""


@dataclass(frozen=True)
class Track:
    number: int
    mode: str
    file: Path
    start_frame: int  # INDEX 01, in sectors from the beginning of `file`
    end_frame: int | None = None  # exclusive; None = until end of file

    @property
    def is_data(self) -> bool:
        return self.mode != "AUDIO"

    @property
    def sector_size(self) -> int:
        return MODE_LAYOUT[self.mode][0]

    @property
    def data_offset(self) -> int:
        return MODE_LAYOUT[self.mode][1]

    @property
    def byte_start(self) -> int:
        return self.start_frame * self.sector_size

    def byte_end(self) -> int:
        if self.end_frame is not None:
            return self.end_frame * self.sector_size
        return self.file.stat().st_size

    @property
    def sector_count(self) -> int:
        return (self.byte_end() - self.byte_start) // self.sector_size


@dataclass(frozen=True)
class CueSheet:
    path: Path
    tracks: list[Track] = field(default_factory=list)

    @property
    def data_track(self) -> Track | None:
        return next((t for t in self.tracks if t.is_data), None)

    @property
    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if not t.is_data]

    @property
    def title(self) -> str:
        return self.path.stem


_MSF_RE = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})$")


def msf_to_frames(msf: str) -> int:
    match = _MSF_RE.match(msf)
    if match is None:
        raise CueError(f"Invalid timestamp: {msf!r}")
    minutes, seconds, frames = (int(g) for g in match.groups())
    return (minutes * SECONDS_PER_MINUTE + seconds) * FRAMES_PER_SECOND + frames


def resolve_case_insensitive(base: Path, name: str) -> Path:
    """Resolve `name` relative to `base`, tolerating case differences in the file name."""
    candidate = (base / name).resolve()
    if candidate.exists():
        return candidate
    # Only the basename is matched loosely; cue sheets from Redump are flat.
    target = Path(name).name.lower()
    parent = candidate.parent if candidate.parent.exists() else base
    for entry in parent.iterdir():
        if entry.name.lower() == target:
            return entry
    raise CueError(f"File referenced by the cue sheet not found: {name}")


def parse_cue(path: Path) -> CueSheet:
    path = path.resolve()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")

    # (number, mode, file, start_frame) collected in order; end frames are filled afterwards.
    raw: list[tuple[int, str, Path, int | None]] = []
    current_file: Path | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            raise CueError(f"Line {lineno}: {exc}") from exc
        if not tokens:
            continue
        keyword = tokens[0].upper()
        if keyword == "FILE":
            if len(tokens) < 2:
                raise CueError(f"Line {lineno}: FILE without a name")
            current_file = resolve_case_insensitive(path.parent, tokens[1])
        elif keyword == "TRACK":
            if current_file is None:
                raise CueError(f"Line {lineno}: TRACK before FILE")
            if len(tokens) < 3:
                raise CueError(f"Line {lineno}: malformed TRACK")
            mode = tokens[2].upper()
            if mode not in MODE_LAYOUT:
                raise CueError(f"Line {lineno}: unsupported track mode {mode}")
            raw.append((int(tokens[1]), mode, current_file, None))
        elif keyword == "INDEX" and len(tokens) >= 3 and int(tokens[1]) == 1:
            if not raw:
                raise CueError(f"Line {lineno}: INDEX before TRACK")
            number, mode, file, _ = raw[-1]
            raw[-1] = (number, mode, file, msf_to_frames(tokens[2]))

    if not raw:
        raise CueError("The cue sheet contains no tracks")

    tracks: list[Track] = []
    for i, (number, mode, file, start) in enumerate(raw):
        if start is None:
            raise CueError(f"Track {number} has no INDEX 01")
        end: int | None = None
        if i + 1 < len(raw) and raw[i + 1][2] == file:
            # Next track lives in the same .bin: this one ends where the next one's
            # INDEX 00 (pregap) would start; INDEX 01 is a safe upper bound for data tracks.
            end = raw[i + 1][3]
        tracks.append(Track(number=number, mode=mode, file=file, start_frame=start, end_frame=end))

    return CueSheet(path=path, tracks=tracks)
