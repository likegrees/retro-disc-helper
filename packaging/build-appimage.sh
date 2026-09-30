#!/usr/bin/env bash
# Build dist/RetroDiscHelper-x86_64.AppImage (run from anywhere).
set -euo pipefail
cd "$(dirname "$0")/.."

uv run --group build pyinstaller --noconfirm --clean \
    --distpath build/pyinstaller --workpath build/work packaging/retrodisc.spec

APPDIR=build/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -r build/pyinstaller/retrodisc "$APPDIR/usr/lib/retrodisc"
cp packaging/retrodisc.desktop "$APPDIR/retrodisc.desktop"
cp packaging/retrodisc.desktop "$APPDIR/usr/share/applications/"
cp packaging/retrodisc.svg "$APPDIR/retrodisc.svg"
cp packaging/retrodisc.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/retrodisc/retrodisc" "$@"
RUN
chmod +x "$APPDIR/AppRun"

TOOL=build/appimagetool-x86_64.AppImage
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
mkdir -p dist
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" dist/RetroDiscHelper-x86_64.AppImage
echo "Built dist/RetroDiscHelper-x86_64.AppImage"
