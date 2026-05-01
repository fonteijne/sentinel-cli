"""File-descriptor-level capture of stdout/stderr into a Textual Log.

Why fd-level and not a Python-attribute swap:

* Python's ``sys.stdout``/``sys.stderr`` attribute replacement doesn't
  reach child processes. ``subprocess.run`` forks with the parent's
  fd 1 and fd 2, so ``git`` / ``ssh`` / ``docker compose`` write straight
  past a Python-level pipe (the user saw raw git progress and ssh
  warnings splash across the TUI frame for exactly this reason).
* ``click.echo`` caches its stream via ``click.utils._default_text_stdout``
  and ``logging.StreamHandler.stream`` caches ``sys.stderr`` at handler
  construction — but both of those end up writing to the underlying fd
  1 / fd 2 via TextIOWrapper, so redirecting the fds captures them
  transparently. No monkey-patching needed once we own the fds.

Gotcha: Textual's linux driver writes terminal output via
``sys.__stderr__`` (immutable reference to the interpreter's original
fd 2). If we naively redirect fd 2 to a pipe, Textual's frames start
landing in the log and the UI stops rendering. Before the swap we
``dup`` the original fd 2 to a new fd, wrap it in a file object, and
re-point Textual's driver ``_file`` (and its ``_writer_thread._file``)
at the wrap. Textual keeps painting the real tty while fd 2 goes to
our pipe for everything else.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
from typing import TYPE_CHECKING, Iterator, List, Tuple

if TYPE_CHECKING:
    from textual.app import App
    from textual.widgets import Log


# Many CLI commands (validate, status, execute) print emoji like 🔐 📊 ✅ 1️⃣.
# Textual measures those as width-2 via wcwidth, but if the operator's
# terminal font lacks the glyph it renders as a 1-cell replacement char.
# Strip them so rows never desync.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # pictographs, transport, supplementals
    "\U0001F1E0-\U0001F1FF"   # regional indicators (flags)
    "☀-➿"           # misc symbols + dingbats
    "⌀-⏿"           # misc technical (⏱, ⏳)
    "️"                  # variation selector 16 (emoji presentation)
    "‍"                  # zero-width joiner (emoji sequences)
    "⃣"                  # combining enclosing keycap (for 1️⃣ 2️⃣ etc.)
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(line: str) -> str:
    return _EMOJI_PATTERN.sub("", line).rstrip()


def _forward_reader(
    app: "App", log: "Log", fd: int, stop_event: threading.Event
) -> None:
    """Read bytes from ``fd`` until EOF, forwarding each complete line to ``log``.

    Runs in a background thread. Uses ``app.call_from_thread`` so Textual's
    event loop is the one that actually mutates the widget.
    """
    buf = b""
    while not stop_event.is_set():
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace")
            clean = _strip_emoji(line)
            try:
                app.call_from_thread(log.write_line, clean)
            except Exception:
                # App may have exited between write and emit. Swallow.
                pass
    # Flush any trailing partial line.
    if buf:
        line = buf.decode("utf-8", errors="replace")
        clean = _strip_emoji(line)
        try:
            app.call_from_thread(log.write_line, clean)
        except Exception:
            pass
    try:
        os.close(fd)
    except OSError:
        pass


def _rescue_textual_writes(
    app: "App", tty_fd: int
) -> Tuple[List[Tuple[object, str, object]], object]:
    """Point Textual's driver output at a file object wrapping ``tty_fd``.

    Textual's linux driver holds ``sys.__stderr__`` (fd 2) in ``_file`` and
    in ``_writer_thread._file``. Once we redirect fd 2 to a pipe, those
    references write to the pipe — the TUI goes dark. Wrap a dup'd copy of
    the original fd 2 in a file object and swap it into both slots.

    Returns ``(patches, tty_file)``. ``patches`` is a list of
    ``(holder, attr, original)`` tuples for the caller to restore on exit;
    ``tty_file`` is the file object that must be closed last.
    """
    patches: List[Tuple[object, str, object]] = []
    tty_file = os.fdopen(os.dup(tty_fd), "w", buffering=1)

    driver = getattr(app, "_driver", None)
    if driver is None:
        return patches, tty_file

    if hasattr(driver, "_file"):
        patches.append((driver, "_file", driver._file))
        driver._file = tty_file

    writer = getattr(driver, "_writer_thread", None)
    if writer is not None and hasattr(writer, "_file"):
        patches.append((writer, "_file", writer._file))
        writer._file = tty_file

    return patches, tty_file


@contextlib.contextmanager
def capture_stdout_to_log(app: "App", log: "Log") -> Iterator[None]:
    """Capture every kind of output produced during the block into ``log``.

    Covers Python print / click.echo / logging handlers AND child-process
    output (git, ssh, docker compose) via fd-level dup2. Textual's own
    rendering is preserved by pointing its driver at a dup'd copy of the
    original fd 2.
    """
    # Flush anything currently buffered so it lands on the real tty.
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.flush()
    except Exception:
        pass

    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()

    saved_out = os.dup(1)
    saved_err = os.dup(2)

    driver_patches, tty_file = _rescue_textual_writes(app, saved_err)

    os.dup2(out_w, 1)
    os.dup2(err_w, 2)
    os.close(out_w)
    os.close(err_w)

    stop_event = threading.Event()
    t_out = threading.Thread(
        target=_forward_reader, args=(app, log, out_r, stop_event), daemon=True
    )
    t_err = threading.Thread(
        target=_forward_reader, args=(app, log, err_r, stop_event), daemon=True
    )
    t_out.start()
    t_err.start()

    try:
        yield
    finally:
        # Flush Python stdio through the pipe before restoring.
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass

        # Restoring the fds drops the only remaining references to the pipe
        # write ends, so the readers see EOF and exit cleanly.
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)

        # Restore Textual driver's file references.
        for holder, attr, original in driver_patches:
            try:
                setattr(holder, attr, original)
            except Exception:
                pass
        try:
            tty_file.close()
        except Exception:
            pass

        stop_event.set()
        t_out.join(timeout=2)
        t_err.join(timeout=2)
