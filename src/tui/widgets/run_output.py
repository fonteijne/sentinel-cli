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

import asyncio
import contextlib
import json
import os
import re
import sys
import threading
from typing import TYPE_CHECKING, Iterator, List, Optional, Tuple

if TYPE_CHECKING:
    from textual.app import App
    from textual.widgets import Log

    from src.tui.service_client import ServiceClient


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


# ---------------------------------------------------------------------------
# Remote-stream rendering (Track 3 §§3)
# ---------------------------------------------------------------------------


# Cap the rendered ``payload`` blob per event so the Log widget keeps nice,
# one-line rows. Textual's Log doesn't wrap well; long rows hard-truncate at
# the widget edge and the operator can't scroll them. Keep it compact.
_PAYLOAD_MAX_LEN = 200


def _short_payload(payload: object) -> str:
    """One-line JSON of ``payload``, truncated to ~200 chars.

    ``default=str`` keeps us alive on datetimes, Paths, and other
    non-JSON-native values the service might ship inside an event.
    """
    try:
        s = json.dumps(payload, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — never let formatting kill the stream
        s = repr(payload)
    if len(s) > _PAYLOAD_MAX_LEN:
        return s[: _PAYLOAD_MAX_LEN - 1] + "…"
    return s


def _render_event(log: "Log", frame: dict) -> None:
    """Render one ``event``-kind frame into the Log widget."""
    seq = frame.get("seq")
    ev_type = frame.get("type", "?")
    payload = frame.get("payload", {})
    seq_str = f"{seq:>5}" if isinstance(seq, int) else "    ?"
    log.write_line(f"[{seq_str}] {ev_type} {_short_payload(payload)}")


async def tail_execution(
    app: "App",
    log_widget: "Log",
    client: "ServiceClient",
    execution_id: str,
) -> Optional[str]:
    """Stream WS frames from the service into ``log_widget``.

    Returns the final execution status string (``"succeeded"``,
    ``"failed"``, ``"cancelled"``, …) when the stream ends cleanly, or
    ``None`` when it was abandoned (disconnect, auth failure, not found).

    Contract points (see Track 3 plan):

    * ``heartbeat`` frames are dropped (noise).
    * On :class:`ServiceStreamDropped`, try **one** reconnect from
      ``last_seq``. A second failure gives up with a visible error line.
    * :class:`ServiceUnauthorized` is terminal — the server won't accept
      this token again without a fresh bootstrap. No retry.
    * :class:`asyncio.CancelledError` propagates (the TUI uses it on quit).
    """
    # Imported lazily so this module stays importable without the service
    # client deps (tests that only exercise capture_stdout_to_log).
    from src.tui.service_client import (
        ExecutionNotFound,
        ServiceStreamDropped,
        ServiceUnauthorized,
        ServiceUnreachable,
    )

    async def _consume(since_seq: int) -> tuple[Optional[str], int]:
        """Pull frames until ``end`` or the stream raises.

        Returns ``(final_status, last_seq)``. ``final_status`` is None if
        the stream ended without an ``end`` frame (caller decides).
        """
        last_seq = since_seq
        async for frame in client.tail(execution_id, since_seq=since_seq):
            kind = frame.get("kind")
            if kind == "heartbeat":
                continue
            if kind == "event":
                seq = frame.get("seq")
                if isinstance(seq, int):
                    last_seq = seq
                _render_event(log_widget, frame)
                continue
            if kind == "end":
                status = str(frame.get("execution_status", "unknown"))
                log_widget.write_line(f"<<< {status}")
                return status, last_seq
            # Unknown frame kind — surface but don't crash.
            log_widget.write_line(f"[tui] unknown frame: {_short_payload(frame)}")
        return None, last_seq

    try:
        status, last_seq = await _consume(since_seq=0)
        if status is not None:
            return status
        # Stream ended without ``end`` (generator returned without raising
        # and without yielding ``end``). Treat like a drop — one resume
        # attempt from last_seq.
        log_widget.write_line(
            f"[tui] stream ended without terminal frame; resuming from seq {last_seq}"
        )
        status, _ = await _consume(since_seq=last_seq)
        if status is None:
            log_widget.write_line("[tui] stream disconnected: no terminal frame")
        return status
    except ServiceUnauthorized:
        log_widget.write_line(
            "[tui] token rejected — reopen the dashboard to refresh"
        )
        return None
    except ServiceStreamDropped as exc:
        reason = exc.detail or str(exc)
        log_widget.write_line(
            f"[tui] stream dropped (seq={exc.last_seq}): {reason}; retrying once"
        )
        try:
            status, _ = await _consume(since_seq=exc.last_seq)
            return status
        except asyncio.CancelledError:
            raise
        except Exception as exc2:  # noqa: BLE001
            log_widget.write_line(f"[tui] stream disconnected: {exc2}")
            return None
    except ExecutionNotFound as exc:
        log_widget.write_line(f"[tui] execution not found: {exc}")
        return None
    except ServiceUnreachable as exc:
        log_widget.write_line(f"[tui] service unreachable: {exc}")
        return None
    except asyncio.CancelledError:
        # Quit path: let Textual cancel the worker. The WS context manager
        # exits via KeyboardInterrupt handling in ``websockets``, which
        # sends a clean close frame.
        raise
    except Exception as exc:  # noqa: BLE001
        log_widget.write_line(
            f"[tui] stream error: {type(exc).__name__}: {exc}"
        )
        return None
