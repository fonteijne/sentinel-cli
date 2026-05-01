"""Stream-capture shim that forwards stdout lines into a Textual RichLog.

Usage from a worker thread:

    from src.tui.widgets.run_output import capture_stdout_to_log

    with capture_stdout_to_log(app, log_widget):
        run_validate(None, None)

The worker thread writes to ``sys.stdout`` as usual; each line is dispatched
to the RichLog via ``app.call_from_thread`` so Textual's render loop sees a
safe update on its own thread.
"""

from __future__ import annotations

import contextlib
import io
import sys
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from textual.app import App
    from textual.widgets import RichLog


class _LineForwarder(io.TextIOBase):
    """File-like that forwards complete lines to a RichLog on the app thread."""

    def __init__(self, app: "App", log: "RichLog") -> None:
        self._app = app
        self._log = log
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self._emit(self._buf)
            self._buf = ""

    def _emit(self, line: str) -> None:
        # call_from_thread marshals the write onto Textual's event loop.
        try:
            self._app.call_from_thread(self._log.write, line)
        except Exception:
            # App may have exited between write and emit. Swallow — the
            # stream contract is best-effort.
            pass


@contextlib.contextmanager
def capture_stdout_to_log(app: "App", log: "RichLog") -> Iterator[None]:
    """Redirect stdout (and stderr) into ``log`` for the duration of the block."""
    forwarder = _LineForwarder(app, log)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = forwarder
    sys.stderr = forwarder
    try:
        yield
    finally:
        forwarder.flush()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
