"""Tests for ``src.service.discovery`` — the Track 1 rendezvous file.

Every test pins ``XDG_STATE_HOME`` to a per-test ``tmp_path`` so the real
``~/.local/state/sentinel/`` is never touched. One test additionally
exercises the ``HOME`` fallback branch by unsetting ``XDG_STATE_HOME``.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time

import pytest

from src.service import discovery


@pytest.fixture
def xdg_state(tmp_path, monkeypatch):
    """Pin XDG_STATE_HOME → tmp_path. Returns the resolved state dir."""

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "sentinel"


def test_write_then_read_round_trips(xdg_state):
    rec = discovery.write_discovery(port=8765, token="tkn-abc-123", pid=4242)
    assert rec.pid == 4242
    assert rec.port == 8765
    assert rec.token == "tkn-abc-123"
    # started_at is ISO-8601 UTC ending in Z
    assert rec.started_at.endswith("Z")
    assert "T" in rec.started_at
    # version is a non-empty string ("0" in a source checkout is valid)
    assert isinstance(rec.version, str) and rec.version

    read_back = discovery.read_discovery()
    assert read_back is not None
    assert read_back == rec


def test_write_default_pid_uses_getpid(xdg_state):
    rec = discovery.write_discovery(port=1, token="x" * 40)
    assert rec.pid == os.getpid()


def test_file_mode_is_0o600(xdg_state):
    discovery.write_discovery(port=1, token="a-token")
    mode = stat.S_IMODE(os.stat(discovery.discovery_path()).st_mode)
    assert mode == 0o600


def test_remove_discovery_missing_file_is_noop(xdg_state):
    # File does not exist yet; must not raise.
    discovery.remove_discovery()
    # Idempotent: calling twice is fine.
    discovery.remove_discovery()


def test_remove_discovery_after_write(xdg_state):
    discovery.write_discovery(port=1, token="tkn-xyz-long-enough")
    assert discovery.discovery_path().exists()
    discovery.remove_discovery()
    assert not discovery.discovery_path().exists()


def test_read_discovery_missing_file_returns_none(xdg_state):
    assert discovery.read_discovery() is None


def test_read_discovery_corrupt_json_returns_none(xdg_state):
    path = discovery.discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{this is not valid json")
    assert discovery.read_discovery() is None


def test_read_discovery_missing_keys_returns_none(xdg_state):
    path = discovery.discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # valid JSON, required keys missing
    path.write_text(json.dumps({"pid": 1, "port": 2}))
    assert discovery.read_discovery() is None


def test_read_discovery_wrong_types_returns_none(xdg_state):
    path = discovery.discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": "not-an-int",
                "port": 2,
                "token": "t",
                "started_at": "2026-05-01T00:00:00Z",
                "version": "0",
            }
        )
    )
    assert discovery.read_discovery() is None


def test_read_discovery_top_level_not_object_returns_none(xdg_state):
    path = discovery.discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([1, 2, 3]))
    assert discovery.read_discovery() is None


def test_pid_alive_self_is_true(xdg_state):
    assert discovery.pid_alive(os.getpid()) is True


def test_pid_alive_nonexistent_is_false(xdg_state):
    # Very high PID unlikely to ever be allocated.
    assert discovery.pid_alive(99999999) is False


def test_pid_alive_nonpositive_is_false(xdg_state):
    assert discovery.pid_alive(0) is False
    assert discovery.pid_alive(-1) is False


def test_discovery_lock_acquires_and_releases(xdg_state):
    with discovery.discovery_lock(timeout_s=1.0) as fd:
        assert isinstance(fd, int) and fd >= 0
    # After release, a fresh acquisition must succeed immediately.
    with discovery.discovery_lock(timeout_s=1.0):
        pass


def test_discovery_lock_second_acquire_times_out(xdg_state):
    """Hold the lock on a background thread; main thread must time out."""

    held = threading.Event()
    release = threading.Event()

    def holder():
        with discovery.discovery_lock(timeout_s=1.0):
            held.set()
            # Keep the lock until the main test signals release.
            release.wait(timeout=5.0)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(timeout=2.0), "holder thread failed to acquire lock"
        start = time.monotonic()
        with pytest.raises(TimeoutError) as excinfo:
            with discovery.discovery_lock(timeout_s=0.1):
                pass
        elapsed = time.monotonic() - start
        # Must have waited roughly the timeout before failing.
        assert elapsed >= 0.1
        # Error message should mention the lock path for operator debugging.
        assert str(discovery.lock_path()) in str(excinfo.value)
    finally:
        release.set()
        t.join(timeout=5.0)


def test_lock_file_survives_release(xdg_state):
    """We never unlink the lock file on release — a concurrent locker
    holding its fd on the original inode would silently succeed on a fresh
    inode otherwise."""

    with discovery.discovery_lock(timeout_s=1.0):
        pass
    assert discovery.lock_path().exists()


def test_atomic_replace_overwrites_previous(xdg_state):
    first = discovery.write_discovery(port=1000, token="first-token-abc")
    # tiny sleep to ensure started_at can differ if we change the second value;
    # round-trip comparison is on all fields, not just port.
    time.sleep(1.0)
    second = discovery.write_discovery(port=2000, token="second-token-xyz")

    read_back = discovery.read_discovery()
    assert read_back is not None
    assert read_back == second
    assert read_back != first
    assert read_back.port == 2000
    assert read_back.token == "second-token-xyz"


def test_xdg_unset_falls_back_to_home(tmp_path, monkeypatch):
    """When XDG_STATE_HOME is unset, the path is rooted at ~/.local/state."""

    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Some platforms resolve Path.home() via pwd rather than HOME; pin both
    # paths we care about to be safe.
    monkeypatch.setattr(
        "src.service.discovery.Path.home",
        classmethod(lambda cls: tmp_path),  # noqa: ARG005
        raising=False,
    )

    expected_dir = tmp_path / ".local" / "state" / "sentinel"
    assert discovery.discovery_path() == expected_dir / "service.json"
    assert discovery.lock_path() == expected_dir / "service.lock"

    rec = discovery.write_discovery(port=9999, token="fallback-home-token-ok")
    assert (expected_dir / "service.json").exists()
    assert discovery.read_discovery() == rec


def test_no_tmp_files_leftover_after_write(xdg_state):
    discovery.write_discovery(port=1, token="tkn-no-leftover")
    state_dir = discovery.discovery_path().parent
    leftover = [p.name for p in state_dir.iterdir() if ".tmp." in p.name]
    assert leftover == []
