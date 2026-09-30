from __future__ import annotations

import sys
from pathlib import Path

import pytest

from retrodisc.core import steam as steam_mod
from retrodisc.core.hostenv import host_env
from retrodisc.core.steam import Shortcut, SteamError, launch

SHORTCUT = Shortcut(appid=3576942165, name="G", exe=Path("/g.exe"), start_dir=Path("/"))
URL = f"steam://rungameid/{SHORTCUT.game_id}"


def test_host_env_restores_original_library_path() -> None:
    env = {"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib", "HOME": "/h"}
    assert host_env(env, frozen=True) == {"LD_LIBRARY_PATH": "/usr/local/lib", "HOME": "/h"}


def test_host_env_drops_bundle_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEI123", raising=False)
    env = {
        "LD_LIBRARY_PATH": "/tmp/_MEI123",
        "QT_PLUGIN_PATH": "/tmp/_MEI123/PySide6/Qt/plugins",
        "QML2_IMPORT_PATH": "/home/me/qml",  # the user's own: kept
        "PATH": "/usr/bin",
    }
    assert host_env(env, frozen=True) == {"QML2_IMPORT_PATH": "/home/me/qml", "PATH": "/usr/bin"}


def test_host_env_untouched_when_not_frozen() -> None:
    env = {"LD_LIBRARY_PATH": "/opt/lib"}
    assert host_env(env, frozen=False) == env


def fake_command(bin_dir: Path, name: str, exit_code: int, log: Path) -> None:
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "{name} $1 LD=${{LD_LIBRARY_PATH-unset}}" >> "{log}"\n'
        f'echo "{name} failed on purpose" >&2\n'
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(steam_mod, "LAUNCH_CHECK_SECONDS", 2.0)
    return bin_dir


def test_launch_uses_xdg_open_with_host_env(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the AppImage: frozen, with the bundle on LD_LIBRARY_PATH.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIbundle")
    log = tmp_path / "calls.log"
    fake_command(fake_bin, "xdg-open", 0, log)
    fake_command(fake_bin, "steam", 0, log)
    launch(SHORTCUT)
    assert log.read_text().splitlines() == [f"xdg-open {URL} LD=unset"]


def test_launch_falls_back_to_steam(fake_bin: Path, tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    fake_command(fake_bin, "xdg-open", 3, log)
    fake_command(fake_bin, "steam", 0, log)
    launch(SHORTCUT)
    assert [line.split()[0] for line in log.read_text().splitlines()] == ["xdg-open", "steam"]


def test_launch_reports_failure(fake_bin: Path, tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    fake_command(fake_bin, "xdg-open", 3, log)
    fake_command(fake_bin, "steam", 1, log)
    with pytest.raises(SteamError) as exc:
        launch(SHORTCUT)
    message = str(exc.value)
    assert "xdg-open exited with 3: xdg-open failed on purpose" in message
    assert "steam exited with 1: steam failed on purpose" in message


def test_install_proton_opens_steam_install_dialog(fake_bin: Path, tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    fake_command(fake_bin, "xdg-open", 0, log)
    steam_mod.install_proton()
    assert log.read_text().split()[:2] == ["xdg-open", "steam://install/2805730"]
