"""Run long core operations off the UI thread, with a modal progress dialog."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

# fn(report) where report(done, total, text) -> keep_going
Reporter = Callable[[int, int, str], bool]


class _Signals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(object)


class Task(QRunnable):
    def __init__(self, fn: Callable[[Reporter], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.cancelled = threading.Event()

    def _report(self, done: int, total: int, text: str = "") -> bool:
        self.signals.progress.emit(done, total, text)
        return not self.cancelled.is_set()

    def run(self) -> None:
        try:
            result = self.fn(self._report)
        except BaseException as exc:
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)


def run_with_progress[T](
    parent: QWidget | None,
    title: str,
    fn: Callable[[Reporter], T],
    cancellable: bool = True,
) -> T:
    """Run `fn` in the thread pool while showing a modal progress dialog.

    Blocks (with a nested event loop, so the UI stays responsive) and returns the result,
    or re-raises the exception raised by `fn`.
    """
    dialog = QProgressDialog(title, parent.tr("Cancel") if parent else "Cancel", 0, 0, parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(300)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    if not cancellable:
        dialog.setCancelButton(None)

    task = Task(fn)
    loop = QEventLoop()
    outcome: dict[str, Any] = {}

    def on_progress(done: int, total: int, text: str) -> None:
        # QProgressDialog uses int ranges; scale byte counts down to permille.
        if total > 0:
            dialog.setMaximum(1000)
            dialog.setValue(min(1000, done * 1000 // total))
        if text:
            dialog.setLabelText(f"{title}\n{text}")

    def on_finished(result: object) -> None:
        outcome["result"] = result
        loop.quit()

    def on_failed(exc: object) -> None:
        outcome["error"] = exc
        loop.quit()

    task.signals.progress.connect(on_progress)
    task.signals.finished.connect(on_finished)
    task.signals.failed.connect(on_failed)
    dialog.canceled.connect(task.cancelled.set)
    QThreadPool.globalInstance().start(task)
    loop.exec()
    dialog.close()

    if "error" in outcome:
        raise outcome["error"]
    result: T = outcome["result"]
    return result
