"""Deep scan with Detect It Easy, which always runs in a separate process."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from retrodisc.core import deep_scan
from retrodisc.core.deep_scan import FileResult, Finding
from retrodisc.core.diagnostics import Status, deep_scan_checks
from retrodisc.core.library import Game, Library
from retrodisc.ui import troubleshoot

from .test_protection_exe import make_pe

needs_die = pytest.mark.skipif(not deep_scan.available(), reason="die-python not installed")
SECUROM_OVERLAY = b"AddD\x03\0\0\x004.68.00\0"


@needs_die
def test_real_detect_it_easy_in_child_process(tmp_path: Path) -> None:
    protected = make_pe(tmp_path / "Lemans.exe", [".cms_t", ".cms_d"], overlay=SECUROM_OVERLAY)
    clean = make_pe(tmp_path / "Config.exe", [".data"])
    results = deep_scan.scan([protected, clean, tmp_path / "missing.exe"])
    by_name = {r.path.name: r for r in results}
    assert [f.label for f in by_name["Lemans.exe"].protections] == ["SecuROM 4.68.00"]
    assert by_name["Config.exe"].protections == []
    assert by_name["missing.exe"].error == "file not found"
    # DIE (and its Qt) never entered this process, which runs PySide6.
    assert "die" not in sys.modules


def test_targets(tmp_path: Path) -> None:
    cd = tmp_path / "cd"
    for rel in ("setup.exe", "Install/x.exe", "a/b/deep.exe", "a/b/c/too_deep.exe", "readme.txt"):
        (cd / rel).parent.mkdir(parents=True, exist_ok=True)
        (cd / rel).write_bytes(b"MZ")
    exe = tmp_path / "pfx/game.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    game = Game(
        name="G", cue="/x.cue", cd_dir=str(cd), installer=str(cd / "setup.exe"), exe=str(exe)
    )
    names = [p.name for p in deep_scan.targets_for(game)]
    assert names[:2] == ["game.exe", "setup.exe"]  # installed game first, no duplicates
    assert sorted(names) == ["deep.exe", "game.exe", "setup.exe", "x.exe"]


def test_rows() -> None:
    game = Game(name="G", cue="/x.cue", exe="/pfx/Lemans.exe")
    results = [
        FileResult(
            Path("/pfx/Lemans.exe"),
            [Finding("Protector", "SecuROM", "4.68.00"), Finding("Library", "Direct3D", "8")],
        ),
        FileResult(Path("/cd/AutoRun.exe"), [Finding("Compiler", "MSVC", "12.00")]),
        FileResult(Path("/cd/bad.exe"), error="file not found"),
    ]
    rows = {r.title: r for r in deep_scan_checks(results, game)}
    assert rows["Detect It Easy: SecuROM 4.68.00"].status is Status.WARN
    assert "Lemans.exe" in rows["Detect It Easy: SecuROM 4.68.00"].detail
    assert rows["Detect It Easy: Lemans.exe"].status is Status.INFO
    assert rows["Detect It Easy: Lemans.exe"].detail == (
        "Protector: SecuROM 4.68.00; Library: Direct3D 8"
    )
    assert "bad.exe" in rows["Detect It Easy: not scanned"].detail
    clean = deep_scan_checks([FileResult(Path("/a.exe"), [])], game)
    assert clean[0].status is Status.OK and "no copy protection" in clean[0].detail


_alive: list[troubleshoot.TroubleshootDialog] = []


@pytest.fixture(autouse=True)
def _release() -> Iterator[None]:
    yield
    _alive.clear()


def test_troubleshoot_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    exe = make_pe(tmp_path / "Lemans.exe", [".text"])
    game = Game(name="G", cue=str(tmp_path / "x.cue"), exe=str(exe))
    lib = Library(tmp_path / "games.json")
    lib.add(game)
    monkeypatch.setattr(deep_scan, "available", lambda: True)
    monkeypatch.setattr(
        deep_scan,
        "scan",
        lambda paths: [FileResult(p, [Finding("Protector", "SecuROM", "4.68.00")]) for p in paths],
    )
    dialog = troubleshoot.TroubleshootDialog(lib, game, None)
    _alive.append(dialog)
    dialog._deep_scan()
    texts = [w.text() for w in dialog.area.widget().findChildren(QLabel)]
    assert any("Detect It Easy: SecuROM 4.68.00" in t for t in texts)
