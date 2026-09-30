# Retro Disc Helper

A desktop app for SteamOS / Arch Linux that sets up Redump CUE/BIN PC games to run with Proton.
It automates the manual Proton workflow for running disc-image games on SteamOS:

1. **Disc**: reads the `.cue`, shows the data and audio tracks, and warns when CD music will be lost.
2. **Create ISO**: converts the data track (MODE1/2352, MODE2/2352, MODE2/2336, MODE1/2048) to
   `.iso`. It checks the sync header and the ISO9660 signature, and never modifies the original files.
3. **Extract**: extracts to `~/Documents/games/<Game>/cd`, finds `setup.exe`, and flags 16-bit installers
   and SafeDisc/SecuROM/LaserLock/StarForce files.
4. **Add to Steam**: writes the non-Steam shortcut and the forced Proton version straight into Steam's
   config (Steam must be closed, and backups are made as `*.bak-<timestamp>`).
5. **Install**: starts the installer through Steam.
6. **Set up**: points the shortcut at the installed game exe. Optionally creates **drive S:** with the
   original disc's label and serial, and writes `s:=cdrom` into the prefix's `system.reg`
   (Protontricks is not needed).

The **Troubleshoot** window checks for the usual Proton problems and offers one-click fixes.

A game's state is saved in `~/.local/share/retrodisc/games.json`, so setup can be resumed after the
installer runs.

## Usage

```bash
./packaging/build-appimage.sh          # -> dist/RetroDiscHelper-x86_64.AppImage
```

Copy the AppImage to the Legion Go, make it executable, and run it in Desktop Mode.

Headless conversion only:

```bash
retrodisc convert "Game (Europe).cue" [out.iso]
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests && uv run mypy src
uv run retrodisc                          # start the GUI
```

`src/retrodisc/core` has no Qt imports and is fully unit-tested; `src/retrodisc/ui` is PySide6.
UI strings go through `tr()`. `packaging/update-translations.sh` generates and compiles the `.ts`
files (Italian is set up but not yet translated).
