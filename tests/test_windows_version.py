from __future__ import annotations

from pathlib import Path

import pytest

from retrodisc.core import steam as steam_mod
from retrodisc.core.diagnostics import Status, run_checks
from retrodisc.core.library import Game, Library
from retrodisc.core.prefix import (
    PrefixError,
    read_reg_value,
    set_windows_version,
    windows_version,
)
from retrodisc.core.steam import Steam

USER_REG = """WINE REGISTRY Version 2
;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000

#arch=win64

[Software\\\\Wine\\\\Drives] 1700000000
"s:"="cdrom"

[Software\\\\Wine\\\\DllOverrides] 1700000000
"d3d9"="native"

[Software\\\\Wine\\\\AppDefaults\\\\game.exe] 1700000000
"Version"="win7"
"""


@pytest.fixture
def pfx(tmp_path: Path) -> Path:
    (tmp_path / "user.reg").write_text(USER_REG)
    return tmp_path


def test_set_and_reset_windows_version(pfx: Path) -> None:
    assert windows_version(pfx) is None
    set_windows_version(pfx, "winxp")
    assert windows_version(pfx) == "winxp"
    set_windows_version(pfx, "win98")  # replaces, does not duplicate
    text = (pfx / "user.reg").read_text()
    assert text.count('"Version"="win98"') == 1 and '"winxp"' not in text

    # Neighbouring keys with the same prefix are untouched.
    reg = pfx / "user.reg"
    assert read_reg_value(reg, r"Software\\Wine\\Drives", "s:") == "cdrom"
    assert read_reg_value(reg, r"Software\\Wine\\DllOverrides", "d3d9") == "native"
    assert read_reg_value(reg, r"Software\\Wine\\AppDefaults\\game.exe", "Version") == "win7"

    set_windows_version(pfx, None)  # back to Proton's default
    assert windows_version(pfx) is None
    assert read_reg_value(reg, r"Software\\Wine\\AppDefaults\\game.exe", "Version") == "win7"


def test_existing_version_value_is_updated(pfx: Path) -> None:
    reg = pfx / "user.reg"
    reg.write_text(USER_REG + '\n[Software\\\\Wine] 1700000000\n"Version"="win10"\n"Other"="x"\n')
    set_windows_version(pfx, "winxp")
    assert windows_version(pfx) == "winxp"
    assert read_reg_value(reg, r"Software\\Wine", "Other") == "x"


def test_rejects_unknown_version_and_missing_prefix(tmp_path: Path, pfx: Path) -> None:
    with pytest.raises(PrefixError):
        set_windows_version(pfx, "winme")
    with pytest.raises(PrefixError):
        set_windows_version(tmp_path / "nope", "winxp")


def test_setting_survives_reload(tmp_path: Path) -> None:
    lib = Library(tmp_path / "games.json")
    lib.add(Game(name="Old", cue="/x.cue", windows_version="winxp"))
    assert Library(tmp_path / "games.json").games[0].windows_version == "winxp"


def test_diagnostics_detect_and_fix_reset_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    monkeypatch.setattr("retrodisc.core.diagnostics.is_steam_running", lambda: False)
    root = tmp_path / "Steam"
    (root / "userdata/1/config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/config.vdf").write_text('"InstallConfigStore"\n{\n}\n')
    steam = Steam(root, "1")
    appid = steam.add_shortcut("Old", tmp_path / "game.exe").appid
    pfx = steam.prefix(appid)
    pfx.mkdir(parents=True)
    (pfx / "user.reg").write_text(USER_REG)

    game = Game(name="Old", cue=str(tmp_path / "x.cue"), appid=appid, windows_version="winxp")
    check = next(r for r in run_checks(game, steam) if r.title == "Windows version")
    assert check.status is Status.FAIL and check.fix is not None
    check.fix()
    check = next(r for r in run_checks(game, steam) if r.title == "Windows version")
    assert check.status is Status.OK
    assert "Windows XP" in check.detail
