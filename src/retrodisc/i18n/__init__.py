"""Translation hook usable from Qt-free code.

Core modules call `tr("text")`; once the Qt app is up it routes to QCoreApplication.translate.
Extract strings with (see packaging/update-translations.sh):
    pyside6-lupdate -tr-function-alias translate+=tr <sources> -ts retrodisc_<lang>.ts
"""

from __future__ import annotations

from collections.abc import Callable

_translate: Callable[[str, str], str] = lambda _context, text: text  # noqa: E731


def install(translate: Callable[[str, str], str]) -> None:
    global _translate
    _translate = translate


def tr(text: str, context: str = "retrodisc") -> str:
    return _translate(context, text)
