from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from retrodisc.core.convert import ConversionCancelled, convert_to_iso, is_valid_iso
from retrodisc.core.cue import CueError, msf_to_frames, parse_cue
from retrodisc.core.iso import extract, list_files, volume_info

from .conftest import wrap_raw

AUDIO = b"\x11" * 2352 * 30


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("mode", [1, 2])
def test_multi_bin_roundtrip(tmp_path: Path, iso_file: Path, mode: int) -> None:
    (tmp_path / "Game (Track 01).bin").write_bytes(wrap_raw(iso_file.read_bytes(), mode))
    (tmp_path / "Game (Track 02).bin").write_bytes(AUDIO)
    cue = tmp_path / "Game.cue"
    cue.write_text(
        f'FILE "Game (Track 01).bin" BINARY\n  TRACK 01 MODE{mode}/2352\n    INDEX 01 00:00:00\n'
        'FILE "Game (Track 02).bin" BINARY\n  TRACK 02 AUDIO\n'
        "    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n"
    )
    sheet = parse_cue(cue)
    assert [t.mode for t in sheet.tracks] == [f"MODE{mode}/2352", "AUDIO"]
    assert len(sheet.audio_tracks) == 1
    out = convert_to_iso(sheet, tmp_path / "out.iso")
    assert sha(out) == sha(iso_file)


def test_single_bin_with_audio(tmp_path: Path, iso_file: Path) -> None:
    data = wrap_raw(iso_file.read_bytes(), 1)
    sectors = len(data) // 2352
    (tmp_path / "game.bin").write_bytes(data + b"\x00" * 2352 * 150 + AUDIO)
    mm, rest = divmod(sectors + 150, 75 * 60)
    ss, ff = divmod(rest, 75)
    cue = tmp_path / "game.cue"
    cue.write_text(
        'FILE "GAME.BIN" BINARY\n'  # different case on purpose
        "  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n"
        f"  TRACK 02 AUDIO\n    INDEX 01 {mm:02d}:{ss:02d}:{ff:02d}\n"
    )
    sheet = parse_cue(cue)
    out = convert_to_iso(sheet, tmp_path / "out.iso")
    # Data track includes the 150-sector pregap of track 2; the filesystem part must match.
    assert out.read_bytes()[: iso_file.stat().st_size] == iso_file.read_bytes()
    assert is_valid_iso(out)


def test_wrong_mode_in_cue_is_corrected(tmp_path: Path, iso_file: Path) -> None:
    (tmp_path / "g.bin").write_bytes(wrap_raw(iso_file.read_bytes(), 2))
    cue = tmp_path / "g.cue"
    cue.write_text('FILE "g.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n')
    assert sha(convert_to_iso(parse_cue(cue), tmp_path / "o.iso")) == sha(iso_file)


def test_mode1_2048_copy(tmp_path: Path, iso_file: Path) -> None:
    cue = tmp_path / "g.cue"
    cue.write_text(f'FILE "{iso_file.name}" BINARY\n TRACK 01 MODE1/2048\n  INDEX 01 00:00:00\n')
    assert sha(convert_to_iso(parse_cue(cue), tmp_path / "o.iso")) == sha(iso_file)


def test_cancel(tmp_path: Path, iso_file: Path) -> None:
    (tmp_path / "g.bin").write_bytes(wrap_raw(iso_file.read_bytes(), 1))
    cue = tmp_path / "g.cue"
    cue.write_text('FILE "g.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n')
    with pytest.raises(ConversionCancelled):
        convert_to_iso(parse_cue(cue), tmp_path / "o.iso", progress=lambda d, t: False)
    assert not (tmp_path / "o.iso").exists()
    assert not (tmp_path / "o.iso.part").exists()


def test_missing_bin(tmp_path: Path) -> None:
    cue = tmp_path / "g.cue"
    cue.write_text('FILE "nope.bin" BINARY\n TRACK 01 MODE1/2352\n  INDEX 01 00:00:00\n')
    with pytest.raises(CueError):
        parse_cue(cue)


def test_msf() -> None:
    assert msf_to_frames("00:02:00") == 150
    assert msf_to_frames("01:00:01") == 4501


def test_volume_info_matches_guide(iso_file: Path) -> None:
    # Algorithm copied from the guide.
    with open(iso_file, "rb") as f:
        f.seek(16 * 2048)
        pvd = f.read(2048)
    label = pvd[40:72].decode().strip()
    s = [0, 0, 0, 0]
    for i in range(0, 2048, 4):
        for j in range(4):
            s[j] = (s[j] + pvd[i + j]) & 0xFF
    info = volume_info(iso_file)
    assert info.label == label == "TESTGAME"
    assert info.serial == "%02x%02x%02x%02x" % tuple(s)  # noqa: UP031


def test_extract_uses_joliet_names(tmp_path: Path, iso_file: Path) -> None:
    assert sorted(list_files(iso_file)) == ["/Data/ReadMe.txt", "/Setup.exe"]
    dest = extract(iso_file, tmp_path / "cd")
    assert (dest / "Setup.exe").read_bytes().startswith(b"MZ")
    assert (dest / "Data" / "ReadMe.txt").read_text().startswith("hello world")
