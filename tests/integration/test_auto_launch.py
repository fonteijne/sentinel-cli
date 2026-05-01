"""Subprocess-level integration tests for Track 1 (auto-launch + discovery).

These tests spawn real ``sentinel serve`` processes via
``python -m src.cli serve --port 0`` and exercise the discovery-file and
TUI bootstrap contracts end-to-end. They are intentionally *not*
``TestClient``-based: the whole point of Track 1 is that the service
outlives the TUI, which only a real subprocess can validate.

Isolation model (see plan ``.agents/plans/interactive-tui/track-1-auto-launch.md``):

* ``XDG_STATE_HOME`` is redirected into ``tmp_path`` so the real
  ``~/.local/state/sentinel/`` is never touched. The child process
  inherits this env, so its ``discovery.write_discovery`` lands in the
  same sandbox.
* ``SENTINEL_SERVICE_TOKEN`` is pinned to a random per-test value so the
  child reuses it (via ``load_or_create_token``) and the test knows the
  bearer up-front.
* ``SENTINEL_DB_PATH`` + ``SENTINEL_LOGS_DIR`` keep SQLite + supervisor
  logs inside ``tmp_path``.

Every spawned child is reaped on teardown (SIGTERM → wait 5s → SIGKILL)
so a failing test never leaks a live ``sentinel serve`` process into the
host.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
import requests

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Track 1 is Linux-only (XDG paths + setsid semantics).",
)


# --- Helpers ---------------------------------------------------------------


_TEST_DEADLINE_S = 15.0
_POLL_INTERVAL_S = 0.05


def _wait_for(
    predicate,  # type: ignore[no-untyped-def]
    *,
    timeout_s: float = _TEST_DEADLINE_S,
    poll_s: float = _POLL_INTERVAL_S,
    what: str = "condition",
):
    """Poll ``predicate()`` until truthy or deadline expires.

    Returns the last truthy value; raises ``AssertionError`` on timeout.
    """

    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(poll_s)
    raise AssertionError(f"timed out after {timeout_s:.1f}s waiting for {what}")


def _reap(proc: subprocess.Popen) -> None:
    """SIGTERM → wait 5s → SIGKILL. Always safe to call on a dead child."""

    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        # Truly stuck; nothing else we can do from userspace.
        pass


@contextmanager
def _spawned_service(env: dict[str, str]) -> Iterator[subprocess.Popen]:
    """Spawn ``sentinel serve --port 0`` detached; reap on exit.

    ``start_new_session=True`` matches the bootstrap's real spawn
    discipline so tests exercise the same process-group semantics the TUI
    will hit in production.
    """

    cmd = [sys.executable, "-m", "src.cli", "serve", "--port", "0"]
    proc = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    try:
        yield proc
    finally:
        _reap(proc)


def _make_env(tmp_path: Path) -> tuple[dict[str, str], str]:
    """Build the isolated env dict for a child serve. Returns (env, token)."""

    token = "integration-test-token-" + secrets.token_hex(8)
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg")
    env["SENTINEL_SERVICE_TOKEN"] = token
    env["SENTINEL_DB_PATH"] = str(tmp_path / "sentinel.db")
    env["SENTINEL_LOGS_DIR"] = str(tmp_path / "logs")
    return env, token


def _discovery_file(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "sentinel" / "service.json"


def _wait_for_discovery(tmp_path: Path, token: str, timeout_s: float = 15.0) -> dict:
    """Wait for the child's discovery file to appear with matching token."""

    path = _discovery_file(tmp_path)

    def _ready() -> dict | None:
        try:
            raw = path.read_text()
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("token") != token:
            return None
        if not isinstance(data.get("port"), int) or data["port"] <= 0:
            return None
        if not isinstance(data.get("pid"), int) or data["pid"] <= 0:
            return None
        return data

    return _wait_for(_ready, timeout_s=timeout_s, what="discovery file")


def _probe_health(port: int, token: str, timeout_s: float = 2.0) -> requests.Response:
    return requests.get(
        f"http://127.0.0.1:{port}/health",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout_s,
    )


def _wait_for_health(port: int, token: str, timeout_s: float = 15.0) -> requests.Response:
    def _ok() -> requests.Response | None:
        try:
            resp = _probe_health(port, token)
        except requests.RequestException:
            return None
        if resp.status_code == 200:
            return resp
        return None

    return _wait_for(_ok, timeout_s=timeout_s, what=f"/health 200 on :{port}")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _apply_env_to_current_process(env: dict[str, str], monkeypatch) -> None:
    """Mirror the child env onto this process so in-process ``ensure_service``
    + ``read_discovery`` resolve the same XDG path and token."""

    for key in (
        "XDG_STATE_HOME",
        "SENTINEL_SERVICE_TOKEN",
        "SENTINEL_DB_PATH",
        "SENTINEL_LOGS_DIR",
    ):
        monkeypatch.setenv(key, env[key])


# --- Tests -----------------------------------------------------------------


def test_fresh_boot_writes_discovery_and_serves_health(tmp_path, monkeypatch):
    """Spawning ``serve`` from a clean slate produces a valid discovery
    record and a live /health endpoint; SIGTERM cleans the record up."""

    env, token = _make_env(tmp_path)
    disc_path = _discovery_file(tmp_path)
    assert not disc_path.exists()

    with _spawned_service(env) as proc:
        data = _wait_for_discovery(tmp_path, token)
        assert data["pid"] == proc.pid, (
            f"discovery pid={data['pid']} doesn't match child pid={proc.pid}"
        )
        resp = _wait_for_health(data["port"], token)
        assert resp.json() == {"status": "ok", "db": "ok"}

        # Clean shutdown path.
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
            pytest.fail("sentinel serve did not exit on SIGTERM within 5s")

    # Lifespan teardown removes the discovery file on clean shutdown.
    _wait_for(
        lambda: not disc_path.exists(),
        timeout_s=5.0,
        what="discovery file to be cleaned up on SIGTERM",
    )


def test_second_ensure_service_attaches_without_spawning(tmp_path, monkeypatch):
    """A second caller finds the running service and does NOT fork a new one."""

    env, token = _make_env(tmp_path)

    with _spawned_service(env) as proc:
        data = _wait_for_discovery(tmp_path, token)
        _wait_for_health(data["port"], token)
        original_pid = proc.pid
        original_port = data["port"]

        # Mirror the env into THIS process so ensure_service resolves the
        # same XDG dir + bearer.
        _apply_env_to_current_process(env, monkeypatch)

        from src.tui.bootstrap import ensure_service

        handle = ensure_service(timeout_s=10.0)
        assert handle.spawned is False, (
            "ensure_service spawned a new process instead of attaching"
        )
        assert handle.discovery.pid == original_pid
        assert handle.discovery.port == original_port
        assert handle.token == token

        # Health still answers.
        resp = _probe_health(original_port, token)
        assert resp.status_code == 200

        # No sibling sentinel serve appeared. The discovery record still
        # points at our original child (strongest check available without
        # pgrep).
        from src.service.discovery import read_discovery

        fresh = read_discovery()
        assert fresh is not None
        assert fresh.pid == original_pid


def test_stale_pid_respawns(tmp_path, monkeypatch):
    """A discovery record pointing at a dead pid is cleaned up and a fresh
    serve is spawned by ``ensure_service``."""

    env, token = _make_env(tmp_path)
    _apply_env_to_current_process(env, monkeypatch)

    # Seed a stale record. pid=99999999 is well above the Linux pid_max
    # ceiling on every kernel we care about, so pid_alive returns False.
    stale_pid = 99_999_999
    if _pid_alive(stale_pid):
        pytest.skip(
            f"pid={stale_pid} appears live on this kernel; cannot fake staleness"
        )

    from src.service.discovery import pid_alive, read_discovery, write_discovery

    write_discovery(port=65535, token=token, pid=stale_pid)
    seeded = read_discovery()
    assert seeded is not None and seeded.pid == stale_pid
    assert not pid_alive(stale_pid)

    from src.tui.bootstrap import ensure_service

    handle = None
    try:
        handle = ensure_service(timeout_s=15.0)
        assert handle.spawned is True
        assert handle.discovery.pid != stale_pid
        assert pid_alive(handle.discovery.pid)

        resp = _probe_health(handle.discovery.port, token)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "db": "ok"}
    finally:
        # ensure_service hands us a detached child; terminate via pid since
        # we don't own the Popen object.
        if handle is not None and _pid_alive(handle.discovery.pid):
            try:
                os.kill(handle.discovery.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and _pid_alive(handle.discovery.pid):
                time.sleep(0.05)
            if _pid_alive(handle.discovery.pid):
                try:
                    os.kill(handle.discovery.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_sigkill_service_then_next_ensure_respawns(tmp_path, monkeypatch):
    """SIGKILL leaves the discovery file behind (lifespan never runs); the
    next ``ensure_service`` detects the dead pid and respawns."""

    env, token = _make_env(tmp_path)
    _apply_env_to_current_process(env, monkeypatch)

    handle = None
    with _spawned_service(env) as proc:
        data = _wait_for_discovery(tmp_path, token)
        _wait_for_health(data["port"], token)
        killed_pid = proc.pid

        os.kill(killed_pid, signal.SIGKILL)
        proc.wait(timeout=5.0)

        _wait_for(
            lambda: not _pid_alive(killed_pid),
            timeout_s=5.0,
            what="killed serve pid to become dead",
        )

        # File was NOT cleaned up (SIGKILL bypasses lifespan shutdown).
        assert _discovery_file(tmp_path).exists()

        from src.tui.bootstrap import ensure_service

        try:
            handle = ensure_service(timeout_s=15.0)
            assert handle.spawned is True
            assert handle.discovery.pid != killed_pid
            assert _pid_alive(handle.discovery.pid)

            resp = _probe_health(handle.discovery.port, token)
            assert resp.status_code == 200
        finally:
            if handle is not None and _pid_alive(handle.discovery.pid):
                try:
                    os.kill(handle.discovery.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 5.0
                while (
                    time.monotonic() < deadline
                    and _pid_alive(handle.discovery.pid)
                ):
                    time.sleep(0.05)
                if _pid_alive(handle.discovery.pid):
                    try:
                        os.kill(handle.discovery.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
