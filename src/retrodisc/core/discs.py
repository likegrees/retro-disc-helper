"""Find all discs of a multi-disc game from one of its .cue files (or an .m3u playlist)."""

from __future__ import annotations

import re
from pathlib import Path

# "(Disc 2)", "(Disc 2 of 3)", "[Disc 2]", "Disc2", "(CD 2)", "CD2"
DISC_RE = re.compile(r"\b(?:disc|disk|cd)\s*(?P<num>\d+)", re.I)


def disc_number(path: Path) -> int | None:
    match = DISC_RE.search(path.stem)
    return int(match.group("num")) if match else None


def _set_pattern(stem: str) -> re.Pattern[str] | None:
    """Regex matching every disc of the same set: the stem with the number wildcarded."""
    match = DISC_RE.search(stem)
    if match is None:
        return None
    before = re.escape(stem[: match.start("num")])
    after = re.escape(stem[match.end("num") :])
    return re.compile(rf"^{before}\d+{after}$", re.I)


def read_m3u(playlist: Path) -> list[Path]:
    discs: list[Path] = []
    for line in playlist.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path = (playlist.parent / line).resolve()
        if path.suffix.lower() == ".cue" and path.is_file():
            discs.append(path)
    return discs


def find_discs(chosen: Path) -> list[Path]:
    """All .cue files of the set `chosen` belongs to, disc 1 first.

    `chosen` may be any disc's .cue or an .m3u playlist. A single-disc game returns [chosen].
    """
    chosen = chosen.resolve()
    if chosen.suffix.lower() == ".m3u":
        return read_m3u(chosen)

    for playlist in sorted(chosen.parent.glob("*.m3u")):
        listed = read_m3u(playlist)
        if len(listed) > 1 and chosen in listed:
            return listed

    pattern = _set_pattern(chosen.stem)
    if pattern is None:
        return [chosen]
    siblings = [
        p for p in chosen.parent.iterdir() if p.suffix.lower() == ".cue" and pattern.match(p.stem)
    ]
    return sorted(siblings, key=lambda p: disc_number(p) or 0) or [chosen]
