from __future__ import annotations

import io
from pathlib import Path

import pycdlib
import pytest

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def make_iso(path: Path, label: str = "TESTGAME") -> Path:
    cd = pycdlib.PyCdlib()
    cd.new(interchange_level=3, joliet=3, vol_ident=label)
    setup = b"MZ" + b"\x00" * 62 + b"payload" * 100
    cd.add_fp(io.BytesIO(setup), len(setup), "/SETUP.EXE;1", joliet_path="/Setup.exe")
    cd.add_directory("/DATA", joliet_path="/Data")
    readme = b"hello world\n" * 500
    cd.add_fp(io.BytesIO(readme), len(readme), "/DATA/README.TXT;1", joliet_path="/Data/ReadMe.txt")
    cd.write(str(path))
    cd.close()
    return path


def wrap_raw(iso: bytes, mode: int) -> bytes:
    """Wrap 2048-byte sectors into 2352-byte raw sectors (fake EDC/ECC)."""
    out = bytearray()
    for i in range(0, len(iso), 2048):
        user = iso[i : i + 2048]
        if mode == 1:
            out += SYNC + b"\x00\x02\x00\x01" + user + b"\xaa" * 288
        else:
            out += SYNC + b"\x00\x02\x00\x02" + b"\x00\x00\x08\x00" * 2 + user + b"\xbb" * 280
    return bytes(out)


@pytest.fixture
def iso_file(tmp_path: Path) -> Path:
    return make_iso(tmp_path / "source.iso")
