"""Deep scan of executables with Detect It Easy (https://github.com/horsicq/Detect-It-Easy).

DIE's Python bindings ship their own Qt 6, which cannot share a process with PySide6
(different Qt builds: crashes on import or on exit). So the scan always runs in a separate
process that imports DIE and never Qt for Python: `retrodisc die-scan FILE...` from source,
or the bundled `retrodisc-die` helper in the AppImage (built without PySide6, whose PyInstaller
runtime hook would otherwise load Qt 6.11 into it).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from retrodisc.core.hostenv import host_env
from retrodisc.core.library import Game

SCAN_TIMEOUT = 180.0
# DIE result types that matter here; the rest (compiler, linker, library...) is extra info.
PROTECTION_TYPES = {"protector", "protection", "drm", "packer", "cryptor"}


class DeepScanError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    type: str  # "Protector", "Compiler", ...
    name: str
    version: str = ""
    info: str = ""

    @property
    def label(self) -> str:
        extra = f" ({self.info})" if self.info else ""
        return f"{self.name} {self.version}".strip() + extra

    @property
    def is_protection(self) -> bool:
        return self.type.lower() in PROTECTION_TYPES


@dataclass(frozen=True)
class FileResult:
    path: Path
    findings: list[Finding] = field(default_factory=list)
    error: str = ""

    @property
    def protections(self) -> list[Finding]:
        return [f for f in self.findings if f.is_protection]


MAX_FILES = 40


def targets_for(game: Game) -> list[Path]:
    """What a deep scan looks at: installed game, installer, and the .exe files on each disc."""
    candidates: list[Path] = [Path(p) for p in (game.exe, game.installer) if p]
    for disc in game.discs:
        if disc.extracted and disc.cd_dir:
            root = Path(disc.cd_dir)
            candidates += sorted(
                p
                for p in root.rglob("*")
                if p.suffix.lower() == ".exe" and len(p.relative_to(root).parts) <= 3
            )
    unique: list[Path] = []
    for path in candidates:
        if path.is_file() and path not in unique:
            unique.append(path)
    return unique[:MAX_FILES]


def available() -> bool:
    """Whether DIE is installed (checked without importing it: that would load its Qt)."""
    return importlib.util.find_spec("die") is not None


def _command(paths: list[Path]) -> list[str]:
    files = [str(p) for p in paths]
    if getattr(sys, "frozen", False):  # the AppImage: the separate helper program
        return [str(Path(sys.executable).with_name("retrodisc-die")), *files]
    return [sys.executable, "-m", "retrodisc", "die-scan", *files]


def scan(paths: list[Path]) -> list[FileResult]:
    """Scan `paths` with DIE in a child process. Raises DeepScanError."""
    if not paths:
        return []
    try:
        proc = subprocess.run(
            _command(paths),
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT,
            check=False,
            env=host_env(),  # no PySide6 Qt paths: the helper must use DIE's own Qt
        )
    except subprocess.TimeoutExpired as exc:
        raise DeepScanError("Detect It Easy took too long") from exc
    except OSError as exc:
        raise DeepScanError(f"Could not start Detect It Easy: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        raise DeepScanError(f"Detect It Easy failed: {detail[0]}")
    try:
        raw: list[dict[str, Any]] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DeepScanError("Detect It Easy returned unexpected output") from exc
    return [
        FileResult(
            path=Path(item["path"]),
            findings=[Finding(**f) for f in item.get("findings", [])],
            error=item.get("error", ""),
        )
        for item in raw
    ]


# ---- child process side --------------------------------------------------------------


def run_cli(args: list[str]) -> int:
    """`retrodisc die-scan FILE...`: print a JSON list with DIE's findings for each file."""
    import die  # only here, in the child process

    flags = die.ScanFlags.DEEP_SCAN | die.ScanFlags.HEURISTIC_SCAN | die.ScanFlags.RESULT_AS_JSON
    database = str(die.database_path)
    results: list[dict[str, Any]] = []
    for arg in args:
        entry: dict[str, Any] = {"path": arg, "findings": []}
        if not Path(arg).is_file():
            entry["error"] = "file not found"
            results.append(entry)
            continue
        try:
            report = json.loads(die.scan_file(arg, flags, database) or "{}")
            for detect in report.get("detects", []):
                for value in detect.get("values", []):
                    if value.get("type", "Unknown") == "Unknown":
                        continue
                    entry["findings"].append(
                        {
                            "type": value.get("type", ""),
                            "name": value.get("name", ""),
                            "version": value.get("version", ""),
                            "info": value.get("info", ""),
                        }
                    )
        except Exception as exc:  # one bad file must not hide the others
            entry["error"] = str(exc)
        results.append(entry)
    json.dump(results, sys.stdout)
    return 0
