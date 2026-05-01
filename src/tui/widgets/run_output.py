"""Stream-capture shim that forwards stdout lines into a Textual RichLog.

Usage from a worker thread:

    from src.tui.widgets.run_output import capture_stdout_to_log

    with capture_stdout_to_log(app, log_widget):
        run_validate(None, None)

Two capture paths matter:

1. **Plain ``print`` / ``sys.stdout.write``**: we replace ``sys.stdout`` with
   a forwarder; standard redirection catches it.
2. **``click.echo``**: Click caches the text stream in
   ``click.utils._default_text_stdout`` on first use, so replacing
   ``sys.stdout`` after that cache is warm is a no-op. We monkey-patch
   ``click.echo`` itself for the duration of the capture to always route
   through the current ``sys.stdout`` — which is our forwarder.

Each complete line is dispatched to the RichLog via ``app.call_from_thread``
so Textual's render loop sees a safe update on its own thread.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from textual.app import App
    from textual.widgets import RichLog


# Many CLI commands (validate, status, execute) print emoji like 🔐 📊 ✅ 1️⃣.
# Textual's layout engine measures these as 2 cells wide via `wcwidth`, but
# if the operator's terminal font lacks the glyph it renders as a 1-cell
# replacement character (or 0-cell with a combiner). The mismatch
# accumulates across lines and skews every border to the right, eventually
# pushing the top-bar / project-select off-screen.
#
# Rather than require a specific terminal font, strip emoji from captured
# output before it hits the Log. The surrounding text still identifies each
# step (1️⃣ becomes "1", ✅ disappears, etc. — all preceded by the step's
# own text label like "Testing Jira API...").
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # pictographs, transport, supplementals
    "\U0001F1E0-\U0001F1FF"   # regional indicators (flags)
    "☀-➿"           # misc symbols + dingbats (☀-➿)
    "⌀-⏿"           # misc technical (⏱, ⏳)
    "️"                  # variation selector 16 (emoji presentation)
    "‍"                  # zero-width joiner (emoji sequences)
    "⃣"                  # combining enclosing keycap (for 1️⃣ 2️⃣ etc.)
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(line: str) -> str:
    return _EMOJI_PATTERN.sub("", line).rstrip()


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
        # Log.write_line appends the trailing newline; we stripped it above.
        clean = _strip_emoji(line)
        try:
            self._app.call_from_thread(self._log.write_line, clean)
        except Exception:
            # App may have exited between write and emit. Swallow — the
            # stream contract is best-effort.
            pass


@contextlib.contextmanager
def capture_stdout_to_log(app: "App", log: "RichLog") -> Iterator[None]:
    """Redirect stdout, stderr, and ``click.echo`` into ``log``."""
    import click

    forwarder = _LineForwarder(app, log)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = forwarder
    sys.stderr = forwarder

    original_echo = click.echo

    def patched_echo(
        message: Any = None,
        file: Any = None,
        nl: bool = True,
        err: bool = False,
        color: Any = None,
    ) -> None:
        # When Click picks its cached default stream it writes past our
        # replaced sys.stdout. Force the current streams every call.
        if file is None:
            file = sys.stderr if err else sys.stdout
        return original_echo(message, file=file, nl=nl, err=err, color=color)

    click.echo = patched_echo  # type: ignore[assignment]
    try:
        yield
    finally:
        click.echo = original_echo  # type: ignore[assignment]
        forwarder.flush()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
