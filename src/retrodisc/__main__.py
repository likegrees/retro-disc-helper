"""Entry point: `retrodisc` (GUI) or `retrodisc convert <file.cue> [out.iso]`."""

from __future__ import annotations

import sys
from pathlib import Path


def _cli_convert(args: list[str]) -> int:
    from retrodisc.core.convert import convert_to_iso
    from retrodisc.core.cue import parse_cue

    if not args:
        print("usage: retrodisc convert <file.cue> [output.iso]", file=sys.stderr)
        return 2
    cue = Path(args[0])
    output = Path(args[1]) if len(args) > 1 else cue.with_suffix(".iso")

    def progress(done: int, total: int) -> bool:
        print(f"\r{done * 100 // max(total, 1):3d}%", end="", flush=True)
        return True

    convert_to_iso(parse_cue(cue), output, progress)
    print(f"\n{output}")
    return 0


def _run_gui() -> int:
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator
    from PySide6.QtWidgets import QApplication

    from retrodisc import i18n
    from retrodisc.core.library import Library
    from retrodisc.ui.common import STYLESHEET
    from retrodisc.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("retrodisc")
    app.setApplicationDisplayName("Retro Disc Helper")
    app.setDesktopFileName("retrodisc")

    translators: list[QTranslator] = []
    for name, folder in (
        ("qtbase", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)),
        ("retrodisc", str(Path(i18n.__file__).parent)),
    ):
        translator = QTranslator(app)
        if translator.load(QLocale(), name, "_", folder):
            app.installTranslator(translator)
            translators.append(translator)
    i18n.install(QCoreApplication.translate)

    app.setStyleSheet(STYLESHEET)
    window = MainWindow(Library())
    window.show()
    return app.exec()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "convert":
        return _cli_convert(sys.argv[2:])
    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
