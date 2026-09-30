from __future__ import annotations

from pathlib import Path

import pytest
import vdf

from retrodisc.core import steam as steam_mod
from retrodisc.core.iso import volume_info
from retrodisc.core.prefix import (
    PrefixError,
    cd_drive_status,
    registry_drive_type,
    set_registry_drive_type,
    setup_cd_drive,
)
from retrodisc.core.steam import Steam, SteamRunningError, _official_tool_name, shortcut_appid

SYSTEM_REG = """WINE REGISTRY Version 2
;; All keys relative to \\\\Machine

#arch=win64

[Software\\\\Wine\\\\Drives] 1700000000
#time=1d9e1b8c1a2b3c4
"d:"="hd"

[Software\\\\Wine\\\\Other] 1700000000
"x"="y"
"""


@pytest.fixture
def fake_steam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Steam:
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: False)
    root = tmp_path / "Steam"
    (root / "userdata" / "1234" / "config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "config.vdf").write_text(
        '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n'
        '\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n\t\t\t\t\t"0"\n\t\t\t\t\t{\n'
        '\t\t\t\t\t\t"name"\t\t"proton_experimental"\n\t\t\t\t\t}\n\t\t\t\t}\n'
        "\t\t\t}\n\t\t}\n\t}\n}\n"
    )
    for d in ("Proton 9.0 (Beta)", "Proton 8.0", "Proton - Experimental"):
        p = root / "steamapps" / "common" / d
        p.mkdir(parents=True)
        (p / "proton").write_text("")
    return Steam(root, "1234")


def test_add_and_update_shortcut(fake_steam: Steam, tmp_path: Path) -> None:
    exe = tmp_path / "games" / "X" / "cd" / "setup.exe"
    sc = fake_steam.add_shortcut("My Game", exe)
    assert sc.appid == shortcut_appid(f'"{exe}"', "My Game")
    assert sc.appid & 0x80000000

    raw = vdf.binary_loads(fake_steam.shortcuts_path.read_bytes())
    entry = raw["shortcuts"]["0"]
    assert entry["Exe"] == f'"{exe}"' and entry["appid"] < 0  # stored signed

    game = tmp_path / "pfx" / "game.exe"
    updated = fake_steam.update_shortcut(sc.appid, exe=game, name="Renamed")
    assert updated.appid == sc.appid
    assert updated.exe == game and updated.start_dir == game.parent
    assert updated.name == "Renamed"
    assert len(fake_steam.shortcuts()) == 1
    assert list(fake_steam.shortcuts_path.parent.glob("shortcuts.vdf.bak-*"))


def test_compat_tool_mapping(fake_steam: Steam) -> None:
    fake_steam.set_compat_tool(3000000000, "proton_9")
    assert fake_steam.compat_tool(3000000000) == "proton_9"
    assert fake_steam.compat_tool(0) == "proton_experimental"  # untouched
    fake_steam.set_compat_tool(3000000000, "proton_8")
    assert fake_steam.compat_tool(3000000000) == "proton_8"


def test_refuses_when_steam_running(fake_steam: Steam, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_mod, "is_steam_running", lambda: True)
    with pytest.raises(SteamRunningError):
        fake_steam.add_shortcut("x", Path("/x.exe"))


def test_proton_tools(fake_steam: Steam) -> None:
    tools = fake_steam.proton_tools()
    # Valve names the regular Proton 9 folder "Proton 9.0 (Beta)": it still wins over 8.0,
    # and Experimental comes last.
    assert [t.name for t in tools] == ["proton_9", "proton_8", "proton_experimental"]
    assert {t.name for t in tools} == {"proton_8", "proton_9", "proton_experimental"}


@pytest.mark.parametrize(
    ("d", "name"),
    [
        ("Proton 9.0", "proton_9"),
        ("Proton 5.13", "proton_513"),
        ("Proton 5.0", "proton_5"),
        ("Proton - Experimental", "proton_experimental"),
        ("Proton Hotfix", "proton_hotfix"),
    ],
)
def test_official_tool_name(d: str, name: str) -> None:
    assert _official_tool_name(d) == name


def test_registry_edit(tmp_path: Path) -> None:
    (tmp_path / "system.reg").write_text(SYSTEM_REG)
    assert registry_drive_type(tmp_path) is None
    set_registry_drive_type(tmp_path, "cdrom")
    assert registry_drive_type(tmp_path) == "cdrom"
    set_registry_drive_type(tmp_path, "cdrom")  # idempotent
    text = (tmp_path / "system.reg").read_text()
    assert text.count('"r:"="cdrom"') == 1
    assert '"d:"="hd"' in text and '"x"="y"' in text


def test_registry_edit_new_section(tmp_path: Path) -> None:
    (tmp_path / "system.reg").write_text("WINE REGISTRY Version 2\n")
    set_registry_drive_type(tmp_path, "cdrom")
    assert registry_drive_type(tmp_path) == "cdrom"


def test_setup_cd_drive(tmp_path: Path, iso_file: Path) -> None:
    pfx = tmp_path / "pfx"
    cd = tmp_path / "cd"
    cd.mkdir()
    with pytest.raises(PrefixError):
        setup_cd_drive(pfx, cd, volume_info(iso_file))
    (pfx / "dosdevices").mkdir(parents=True)
    (pfx / "system.reg").write_text(SYSTEM_REG)
    assert not cd_drive_status(pfx, cd).ok

    info = volume_info(iso_file)
    setup_cd_drive(pfx, cd, info)
    status = cd_drive_status(pfx, cd)
    assert status.ok
    assert status.label == "TESTGAME" and status.serial == info.serial

    # Broken link is detected (troubleshooting case "File not found").
    other = tmp_path / "moved"
    cd.rename(other)
    assert not cd_drive_status(pfx, other).link_ok
