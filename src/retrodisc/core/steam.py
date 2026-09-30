"""Steam integration: non-Steam shortcuts, forced Proton version, launching."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vdf

STEAM_ROOT_CANDIDATES = (
    Path("~/.local/share/Steam"),
    Path("~/.steam/steam"),
    Path("~/.var/app/com.valvesoftware.Steam/data/Steam"),
)
STEAMID64_BASE = 76561197960265728
COMPAT_PRIORITY = "250"


class SteamError(Exception):
    pass


class SteamRunningError(SteamError):
    """Steam overwrites shortcuts.vdf/config.vdf on exit, so it must be closed first."""


@dataclass(frozen=True)
class ProtonTool:
    name: str  # internal name used in CompatToolMapping, e.g. "proton_9"
    display_name: str
    path: Path

    @property
    def is_stable(self) -> bool:
        lowered = f"{self.name} {self.display_name}".lower()
        return not any(w in lowered for w in ("experimental", "hotfix", "beta", "next"))


@dataclass(frozen=True)
class Shortcut:
    appid: int  # unsigned 32-bit
    name: str
    exe: Path
    start_dir: Path
    launch_options: str = ""

    @property
    def game_id(self) -> int:
        """64-bit id used by steam://rungameid/ for non-Steam shortcuts."""
        return (self.appid << 32) | 0x02000000


def shortcut_appid(exe: str, name: str) -> int:
    """Same algorithm Steam uses for new shortcuts (unsigned)."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def to_signed(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


def to_unsigned(value: int) -> int:
    return value & 0xFFFFFFFF


def _quote(path: Path) -> str:
    return f'"{path}"'


def _unquote(value: str) -> str:
    return value.strip().strip('"')


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    return dest


def is_steam_running() -> bool:
    try:
        result = subprocess.run(["pgrep", "-x", "steam"], capture_output=True, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def shutdown_steam(timeout: float = 60.0) -> bool:
    """Ask Steam to quit and wait for it. Returns True once it is gone."""
    if not is_steam_running():
        return True
    subprocess.run(["steam", "-shutdown"], capture_output=True, check=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_steam_running():
            return True
        time.sleep(1)
    return False


def launch(shortcut: Shortcut) -> None:
    subprocess.Popen(
        ["xdg-open", f"steam://rungameid/{shortcut.game_id}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class Steam:
    def __init__(self, root: Path, user_id: str) -> None:
        self.root = root
        self.user_id = user_id

    # ---- discovery ---------------------------------------------------------------------

    @staticmethod
    def find_root() -> Path:
        for candidate in STEAM_ROOT_CANDIDATES:
            path = candidate.expanduser()
            if (path / "steamapps").is_dir():
                return path.resolve()
        raise SteamError("Steam installation not found")

    @staticmethod
    def user_ids(root: Path) -> list[str]:
        """Steam3 account ids with a userdata folder, most recent login first."""
        userdata = root / "userdata"
        ids = (
            [p.name for p in userdata.iterdir() if p.name.isdigit() and p.name != "0"]
            if (userdata.is_dir())
            else []
        )
        recent: str | None = None
        login_users = root / "config" / "loginusers.vdf"
        if login_users.exists():
            users = vdf.loads(login_users.read_text(errors="replace")).get("users", {})
            for steamid64, info in users.items():
                if str(info.get("MostRecent", "0")) == "1" and steamid64.isdigit():
                    recent = str(int(steamid64) - STEAMID64_BASE)
        ids.sort(key=lambda i: (i != recent, -(userdata / i).stat().st_mtime))
        return ids

    @classmethod
    def detect(cls) -> Steam:
        root = cls.find_root()
        ids = cls.user_ids(root)
        if not ids:
            raise SteamError("No Steam user found. Log in to Steam at least once.")
        return cls(root, ids[0])

    @property
    def shortcuts_path(self) -> Path:
        return self.root / "userdata" / self.user_id / "config" / "shortcuts.vdf"

    @property
    def config_path(self) -> Path:
        return self.root / "config" / "config.vdf"

    def library_folders(self) -> list[Path]:
        folders = [self.root]
        lib_file = self.root / "steamapps" / "libraryfolders.vdf"
        if lib_file.exists():
            data = vdf.loads(lib_file.read_text(errors="replace")).get("libraryfolders", {})
            for entry in data.values():
                if isinstance(entry, dict) and "path" in entry:
                    path = Path(entry["path"])
                    if path.resolve() != self.root and path.is_dir():
                        folders.append(path)
        return folders

    def proton_tools(self) -> list[ProtonTool]:
        """Installed Proton builds, newest stable first."""
        tools: list[ProtonTool] = []
        for lib in self.library_folders():
            common = lib / "steamapps" / "common"
            if not common.is_dir():
                continue
            for d in common.iterdir():
                if d.name.startswith("Proton") and (d / "proton").exists():
                    tools.append(ProtonTool(_official_tool_name(d.name), d.name, d))
        custom = self.root / "compatibilitytools.d"
        if custom.is_dir():
            for manifest in custom.glob("*/compatibilitytool.vdf"):
                data = vdf.loads(manifest.read_text(errors="replace"))
                compat = data.get("compatibilitytools", {}).get("compat_tools", {})
                for name, info in compat.items():
                    tools.append(ProtonTool(name, info.get("display_name", name), manifest.parent))

        def sort_key(t: ProtonTool) -> tuple[bool, list[int]]:
            version = [int(n) for n in re.findall(r"\d+", t.display_name)]
            return (not t.is_stable, [-n for n in version])

        return sorted(tools, key=sort_key)

    def compatdata(self, appid: int) -> Path:
        for lib in self.library_folders():
            candidate = lib / "steamapps" / "compatdata" / str(appid)
            if candidate.is_dir():
                return candidate
        return self.root / "steamapps" / "compatdata" / str(appid)

    def prefix(self, appid: int) -> Path:
        return self.compatdata(appid) / "pfx"

    # ---- shortcuts.vdf -----------------------------------------------------------------

    def _load_shortcuts(self) -> dict[str, Any]:
        if not self.shortcuts_path.exists():
            return {"shortcuts": {}}
        data: dict[str, Any] = vdf.binary_loads(self.shortcuts_path.read_bytes())
        data.setdefault("shortcuts", {})
        return data

    def _save_shortcuts(self, data: dict[str, Any]) -> None:
        self._ensure_closed()
        self.shortcuts_path.parent.mkdir(parents=True, exist_ok=True)
        backup(self.shortcuts_path)
        self.shortcuts_path.write_bytes(vdf.binary_dumps(data))

    def _ensure_closed(self) -> None:
        if is_steam_running():
            raise SteamRunningError("Close Steam before changing its configuration")

    def shortcuts(self) -> list[Shortcut]:
        return [
            Shortcut(
                appid=to_unsigned(int(e.get("appid", 0))),
                name=str(e.get("AppName", e.get("appname", ""))),
                exe=Path(_unquote(str(e.get("Exe", e.get("exe", ""))))),
                start_dir=Path(_unquote(str(e.get("StartDir", "")))),
                launch_options=str(e.get("LaunchOptions", "")),
            )
            for e in self._load_shortcuts()["shortcuts"].values()
        ]

    def add_shortcut(self, name: str, exe: Path, start_dir: Path | None = None) -> Shortcut:
        start_dir = start_dir or exe.parent
        appid = shortcut_appid(_quote(exe), name)
        data = self._load_shortcuts()
        entries = data["shortcuts"]
        entries[str(len(entries))] = {
            "appid": to_signed(appid),
            "AppName": name,
            "Exe": _quote(exe),
            "StartDir": _quote(start_dir),
            "icon": "",
            "ShortcutPath": "",
            "LaunchOptions": "",
            "IsHidden": 0,
            "AllowDesktopConfig": 1,
            "AllowOverlay": 1,
            "OpenVR": 0,
            "Devkit": 0,
            "DevkitGameID": "",
            "DevkitOverrideAppID": 0,
            "LastPlayTime": 0,
            "FlatpakAppID": "",
            "tags": {},
        }
        self._save_shortcuts(data)
        return Shortcut(appid=appid, name=name, exe=exe, start_dir=start_dir)

    def update_shortcut(
        self,
        appid: int,
        *,
        name: str | None = None,
        exe: Path | None = None,
        start_dir: Path | None = None,
    ) -> Shortcut:
        """Retarget an existing shortcut. The appid is kept so the Proton prefix survives."""
        data = self._load_shortcuts()
        for entry in data["shortcuts"].values():
            if to_unsigned(int(entry.get("appid", 0))) != appid:
                continue
            if name is not None:
                entry["AppName"] = name
            if exe is not None:
                entry["Exe"] = _quote(exe)
                if start_dir is None:
                    start_dir = exe.parent
            if start_dir is not None:
                entry["StartDir"] = _quote(start_dir)
            self._save_shortcuts(data)
            return next(s for s in self.shortcuts() if s.appid == appid)
        raise SteamError(f"No shortcut with appid {appid}")

    # ---- config.vdf --------------------------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise SteamError(f"{self.config_path} not found")
        data: dict[str, Any] = vdf.loads(
            self.config_path.read_text(errors="replace"), mapper=vdf.VDFDict
        )
        return data

    @staticmethod
    def _steam_section(data: dict[str, Any]) -> Any:
        section: Any = data
        for key in ("InstallConfigStore", "Software", "Valve", "Steam"):
            match = next((k for k in section if k.lower() == key.lower()), None)
            if match is None:
                section[key] = vdf.VDFDict()
                match = key
            section = section[match]
        return section

    def compat_tool(self, appid: int) -> str | None:
        steam = self._steam_section(self._load_config())
        mapping = steam.get("CompatToolMapping", {})
        entry = mapping.get(str(appid))
        return str(entry["name"]) if entry and "name" in entry else None

    def set_compat_tool(self, appid: int, tool_name: str) -> None:
        self._ensure_closed()
        data = self._load_config()
        steam = self._steam_section(data)
        if "CompatToolMapping" not in steam:
            steam["CompatToolMapping"] = vdf.VDFDict()
        mapping = steam["CompatToolMapping"]
        key = str(appid)
        if key in mapping:
            del mapping[key]
        mapping[key] = {"name": tool_name, "config": "", "priority": COMPAT_PRIORITY}
        backup(self.config_path)
        self.config_path.write_text(vdf.dumps(data, pretty=True))


def _official_tool_name(directory: str) -> str:
    """Map "Proton 9.0" -> "proton_9", "Proton - Experimental" -> "proton_experimental"."""
    lowered = directory.lower()
    if "experimental" in lowered:
        return "proton_experimental"
    if "hotfix" in lowered:
        return "proton_hotfix"
    match = re.search(r"(\d+)\.(\d+)", directory)
    if match is None:
        return re.sub(r"\W+", "_", lowered).strip("_")
    major, minor = int(match.group(1)), int(match.group(2))
    # Up to 5.x Valve used "proton_513"-style names; from 6 on just the major version.
    return f"proton_{major}{minor}" if major < 6 and minor else f"proton_{major}"
