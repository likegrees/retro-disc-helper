#!/usr/bin/env bash
# Refresh .ts files from the sources and compile them to .qm.
# Add a language by appending its code to LANGS (e.g. "it"), then translate with Qt Linguist.
set -euo pipefail
cd "$(dirname "$0")/.."
LANGS=(it)
SOURCES=$(find src/retrodisc -name '*.py')
for lang in "${LANGS[@]}"; do
    ts="src/retrodisc/i18n/retrodisc_${lang}.ts"
    # shellcheck disable=SC2086
    uv run pyside6-lupdate -tr-function-alias translate+=tr $SOURCES -ts "$ts"
    uv run pyside6-lrelease "$ts" -qm "${ts%.ts}.qm"
done
