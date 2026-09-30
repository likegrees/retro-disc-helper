"""Convert the data track of a CUE/BIN image into a plain 2048-byte-sector ISO."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from retrodisc.core.cue import USER_DATA_SIZE, CueSheet, Track

SYNC_PATTERN = b"\x00" + b"\xff" * 10 + b"\x00"
CHUNK_SECTORS = 512  # ~1.2 MiB per read with 2352-byte sectors

# Called with (bytes_done, bytes_total); return False to cancel.
ProgressCallback = Callable[[int, int], bool]


class ConversionError(Exception):
    pass


class ConversionCancelled(ConversionError):
    pass


def detect_mode(track: Track) -> str | None:
    """Inspect the first sector of a raw track and return the mode it really uses.

    Returns None when the track has no sync header (i.e. it is not a raw 2352 track).
    """
    with track.file.open("rb") as f:
        f.seek(track.byte_start)
        header = f.read(16)
    if len(header) < 16 or header[:12] != SYNC_PATTERN:
        return None
    return {1: "MODE1/2352", 2: "MODE2/2352"}.get(header[15])


def is_valid_iso(path: Path) -> bool:
    """An ISO9660 image has the primary volume descriptor at sector 16 ("\\x01CD001")."""
    try:
        with path.open("rb") as f:
            f.seek(16 * USER_DATA_SIZE)
            return f.read(6) == b"\x01CD001"
    except OSError:
        return False


def convert_to_iso(
    sheet: CueSheet,
    output: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Write the data track of `sheet` to `output` as an ISO. Source files are untouched."""
    track = sheet.data_track
    if track is None:
        raise ConversionError("The cue sheet has no data track")

    if track.sector_size == 2352:
        detected = detect_mode(track)
        if detected is not None and detected != track.mode:
            # Trust the sectors over the cue sheet; recreate the track with the real mode.
            track = Track(
                number=track.number,
                mode=detected,
                file=track.file,
                start_frame=track.start_frame,
                end_frame=track.end_frame,
            )

    total = track.byte_end() - track.byte_start
    tmp = output.with_suffix(output.suffix + ".part")
    try:
        whole_file = track.byte_start == 0 and track.end_frame is None
        if track.sector_size == USER_DATA_SIZE and whole_file:
            shutil.copyfile(track.file, tmp)
            if progress is not None:
                progress(total, total)
        else:
            _copy_user_data(track, tmp, total, progress)
        if not is_valid_iso(tmp):
            raise ConversionError(
                "The resulting image is not a valid ISO9660 file system "
                f"(track mode {track.mode}). The disc may use a different format."
            )
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)
    return output


def _copy_user_data(
    track: Track, dest: Path, total: int, progress: ProgressCallback | None
) -> None:
    size, offset = track.sector_size, track.data_offset
    chunk = size * CHUNK_SECTORS
    done = 0
    with track.file.open("rb") as src, dest.open("wb") as out:
        src.seek(track.byte_start)
        while done < total:
            block = src.read(min(chunk, total - done))
            if not block:
                break
            view = memoryview(block)
            out.write(
                b"".join(
                    view[i + offset : i + offset + USER_DATA_SIZE]
                    for i in range(0, len(block) - size + 1, size)
                )
            )
            done += len(block)
            if progress is not None and not progress(done, total):
                raise ConversionCancelled("Conversion cancelled")
