"""Authentication + rate-limit tests for the Command Center (plan 05 Task 6).

Covers the full Task 6 checklist:
* HTTP: no header / wrong scheme / correct token / wrong token / audit log
* ``/health`` unauthenticated
* WebSocket: no token / header accept / query-param loopback / wrong token
* HTTP query-param must not be honoured
* Token file: mode 0o600, env wins over file, atomic concurrent create
* CORS: ``*`` rejected at startup
* Rate limiter: concurrent + windowed (unit + integration)
"""

from __future__ import annotations

import logging
import stat
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.core.execution.models import ExecutionKind
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized

from tests.service.conftest import TEST_TOKEN


# ---------------------------------------------------------------------------
# Local fake-supervisor variant of ``authed_client`` for rate-limit integration.
# ---------------------------------------------------------------------------


class _FakeSupervisor:
    def __init__(self) -> None:
        self.spawn_calls: list[str] = []

    def spawn(self, execution_id: str) -> int:
        self.spawn_calls.append(execution_id)
        return 99999

    def cancel(self, execution_id: str) -> None:  # pragma: no cover - unused
        pass


@pytest.fixture
def authed_client_with_fake_supervisor(authed_env) -> Iterator[TestClient]:
    """authed_client variant that swaps in a fake supervisor.

    Used only by the windowed rate-limit test — we need to POST 31 times
    without actually spawning subprocesses.
    """
    from src.service.app import create_app
    from src.service.deps import get_supervisor

    app = create_app()
    app.dependency_overrides[get_supervisor] = lambda: _FakeSupervisor()
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c


# ---------------------------------------------------------------------------
# HTTP bearer auth
# ---------------------------------------------------------------------------


def test_http_no_header_is_401(unauthed_client):
    r = unauthed_client.get("/executions")
    assert r.status_code == 401


def test_http_wrong_scheme_is_401(unauthed_client):
    r = unauthed_client.get(
        "/executions",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert r.status_code == 401


def test_http_correct_token_200(authed_client):
    r = authed_client.get("/executions")
    assert r.status_code == 200


def test_http_wrong_token_is_401_and_logs(unauthed_client, caplog):
    caplog.set_level(logging.WARNING, logger="src.service.auth")
    r = unauthed_client.get(
        "/executions",
        headers={"Authorization": "Bearer completely-wrong-token-value"},
    )
    assert r.status_code == 401
    assert any("auth failure" in rec.message for rec in caplog.records)


def test_health_unauthenticated_200(unauthed_client):
    r = unauthed_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_http_query_param_ignored(unauthed_client, service_token):
    """HTTP must NOT accept ``?token=`` — query strings leak into logs."""
    r = unauthed_client.get(f"/executions?token={service_token}")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# WebSocket bearer auth
# ---------------------------------------------------------------------------


def _seed_execution() -> str:
    ensure_initialized()
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        e = repo.create("T-1", "ACME", ExecutionKind.PLAN)
        return e.id
    finally:
        conn.close()


def test_ws_no_token_is_rejected(unauthed_client):
    execution_id = _seed_execution()
    with pytest.raises(WebSocketDisconnect):
        with unauthed_client.websocket_connect(
            f"/executions/{execution_id}/stream"
        ) as ws:
            ws.receive_json()


def test_ws_auth_header_accepted(authed_client):
    execution_id = _seed_execution()
    # No events seeded; connection opens, first frame should be a heartbeat
    # (on silence) or an end frame if terminal. Just assert we can connect
    # — assertions on frames belong in test_stream.py.
    import src.service.routes.stream as stream_module

    # Short heartbeat so we receive a frame quickly and close cleanly.
    original_hb = stream_module.HEARTBEAT_INTERVAL_S
    stream_module.HEARTBEAT_INTERVAL_S = 0.2
    try:
        with authed_client.websocket_connect(
            f"/executions/{execution_id}/stream"
        ) as ws:
            frame = ws.receive_json()
            assert frame["kind"] in ("heartbeat", "event", "end")
    finally:
        stream_module.HEARTBEAT_INTERVAL_S = original_hb


def test_ws_query_param_token_accepted_from_loopback(
    unauthed_client, service_token, monkeypatch
):
    """Query-param ``?token=`` must work from loopback clients.

    TestClient's client host is ``"testclient"`` — not in the default
    ``_LOOPBACK_HOSTS`` set. We monkeypatch the set for this test only so the
    handshake sees the request as coming from loopback.
    """
    import src.service.auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "_LOOPBACK_HOSTS",
        auth_module._LOOPBACK_HOSTS | {"testclient"},
    )

    execution_id = _seed_execution()
    import src.service.routes.stream as stream_module

    original_hb = stream_module.HEARTBEAT_INTERVAL_S
    stream_module.HEARTBEAT_INTERVAL_S = 0.2
    try:
        with unauthed_client.websocket_connect(
            f"/executions/{execution_id}/stream?token={service_token}"
        ) as ws:
            frame = ws.receive_json()
            assert frame["kind"] in ("heartbeat", "event", "end")
    finally:
        stream_module.HEARTBEAT_INTERVAL_S = original_hb


def test_ws_wrong_query_param_rejected(unauthed_client, monkeypatch):
    import src.service.auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "_LOOPBACK_HOSTS",
        auth_module._LOOPBACK_HOSTS | {"testclient"},
    )

    execution_id = _seed_execution()
    with pytest.raises(WebSocketDisconnect):
        with unauthed_client.websocket_connect(
            f"/executions/{execution_id}/stream?token=wrong-token"
        ) as ws:
            ws.receive_json()


# ---------------------------------------------------------------------------
# Token file semantics
# ---------------------------------------------------------------------------


def test_token_file_mode_is_0600(tmp_path, monkeypatch):
    """Fresh file is created with mode 0o600 and reused on second call."""
    import src.service.auth as auth_module

    token_file = tmp_path / "service_token"
    monkeypatch.setattr(auth_module, "TOKEN_FILE", token_file)
    # Env var must be absent or the file branch never runs.
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)

    t1 = auth_module.load_or_create_token()
    assert token_file.exists()
    mode = token_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    assert len(t1) >= 32

    # Second call reads the existing file and returns the same value.
    t2 = auth_module.load_or_create_token()
    assert t2 == t1


def test_env_var_wins_over_file(tmp_path, monkeypatch):
    import src.service.auth as auth_module

    token_file = tmp_path / "service_token"
    token_file.write_text("a" * 50)  # valid length, but should be ignored
    monkeypatch.setattr(auth_module, "TOKEN_FILE", token_file)
    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", "env-wins-token-abc" + "x" * 20)

    got = auth_module.load_or_create_token()
    assert got == "env-wins-token-abc" + "x" * 20
    assert got != "a" * 50


def test_atomic_create_concurrent_calls_agree(tmp_path, monkeypatch):
    """N threads racing ``load_or_create_token`` must all return the same token.

    With the fix in place (loser no longer ``tmp.unlink()``s the winner's
    workspace), the O_EXCL race resolves cleanly: exactly one thread wins
    the ``O_CREAT|O_EXCL`` open, writes + renames, and the losers fall into
    ``_read_token_file``'s bounded-retry branch and observe the winner's
    rename. Every thread must return the same token string.

    A ``threading.Barrier(N)`` synchronizes the threads so they all hit
    the atomic-create branch within microseconds of each other — without
    the barrier, the first thread would complete before later threads even
    start and the race would be uninteresting.
    """
    import threading
    import src.service.auth as auth_module

    token_file = tmp_path / "service_token"
    monkeypatch.setattr(auth_module, "TOKEN_FILE", token_file)
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            token = auth_module.load_or_create_token()
        except BaseException as exc:  # pragma: no cover - diagnostic
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(token)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"worker(s) raised: {errors!r}"
    assert len(results) == n_threads
    # The load-bearing invariant: every thread saw the SAME token. One
    # winner wrote, N-1 losers read, all agreed.
    assert len(set(results)) == 1, f"threads disagreed: {set(results)!r}"
    assert len(results[0]) >= 32

    # File mode survives the race — the winner's O_CREAT used 0o600, and no
    # loser unlink/retry path clobbered it.
    assert token_file.exists()
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_stale_tmp_does_not_block_startup(tmp_path, monkeypatch):
    """A stale tmp from a crashed prior process must not wedge startup.

    Regression guard: the earlier implementation used a shared ``.tmp``
    sibling whose leftover contents would indefinitely block fresh callers
    via ``O_CREAT|O_EXCL``. The current implementation uses unique
    PID-derived tmp filenames, so an orphaned tmp is irrelevant.

    Scenario: previous process crashed mid-create, leaving multiple stale
    tmp-looking files around. A fresh ``load_or_create_token`` must still
    produce a valid token and a 0o600 real file.
    """
    import src.service.auth as auth_module

    token_file = tmp_path / "service_token"
    monkeypatch.setattr(auth_module, "TOKEN_FILE", token_file)
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)

    # Leave several stale tmp-looking files behind; none of them collide
    # with the current caller's unique PID-derived tmp path.
    (tmp_path / "service_token.tmp").write_text(
        "partial-write-from-crashed-process"
    )
    (tmp_path / "service_token.tmp.99999.deadbeef").write_text("stale")

    got = auth_module.load_or_create_token()
    assert len(got) >= 32
    assert token_file.exists()
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    # A second call should be idempotent: same token, via the file-branch.
    assert auth_module.load_or_create_token() == got


# ---------------------------------------------------------------------------
# CORS validation
# ---------------------------------------------------------------------------


def test_validate_cors_star_raises():
    from src.service.app import _validate_cors

    with pytest.raises(RuntimeError):
        _validate_cors(["*"])

    # Mixed list with "*" also rejected.
    with pytest.raises(RuntimeError):
        _validate_cors(["http://localhost:3000", "*"])

    # Empty list and proper origins are fine.
    _validate_cors([])
    _validate_cors(["http://localhost:3000"])


# ---------------------------------------------------------------------------
# Rate limiter — unit tests (no HTTP needed)
# ---------------------------------------------------------------------------


def test_rate_limiter_unit_concurrent():
    """max_concurrent=2: 2 reserves succeed, 3rd denied until release."""
    from src.service.rate_limit import TokenRateLimiter

    limiter = TokenRateLimiter(max_concurrent=2, max_per_minute=1000)
    key = "tok-a"

    ok1, _ = limiter.check_and_reserve(key)
    ok2, _ = limiter.check_and_reserve(key)
    ok3, retry3 = limiter.check_and_reserve(key)
    assert ok1 and ok2
    assert not ok3
    assert retry3 >= 1

    limiter.release(key)
    ok4, _ = limiter.check_and_reserve(key)
    assert ok4

    # Different key unaffected.
    okB, _ = limiter.check_and_reserve("tok-b")
    assert okB


def test_rate_limiter_unit_windowed():
    """max_per_minute=3: 4th inside the window is denied with Retry-After."""
    from src.service.rate_limit import TokenRateLimiter

    # Large concurrent so only the windowed limit can trip.
    limiter = TokenRateLimiter(max_concurrent=1000, max_per_minute=3)
    key = "tok-win"

    for _ in range(3):
        ok, _ = limiter.check_and_reserve(key)
        assert ok
        limiter.release(key)

    ok4, retry = limiter.check_and_reserve(key)
    assert not ok4
    # Retry-After hint is bounded: just-after entry = ~60s, +1s cushion.
    assert 1 <= retry <= 62


def test_rate_limiter_prunes_cold_keys_on_release():
    """Idle tokens must not permanently inhabit the limiter dicts.

    Bounds the limiter's memory footprint by recently-active tokens rather
    than every token ever observed. Two observable guarantees:

    * After ``release`` drops in-flight to zero, the key is removed from
      ``_in_flight`` (the window is kept while it still has recent entries
      because the 60s rate check needs them).
    * If the window happens to be empty at release time (e.g. we pre-aged
      it), the key is also removed from ``_window`` on that same release.
    """
    from src.service.rate_limit import TokenRateLimiter

    limiter = TokenRateLimiter(max_concurrent=5, max_per_minute=5)
    key = "idle-tok"

    ok, _ = limiter.check_and_reserve(key)
    assert ok
    assert key in limiter._in_flight
    limiter.release(key)
    assert key not in limiter._in_flight, "in_flight should drop on release"
    assert limiter._window.get(key), (
        "Window keeps the recent entry for the 60s rate check"
    )

    # Simulate the window having aged out, then release once more. Now both
    # counters are empty → opportunistic prune should drop the key entirely.
    ok2, _ = limiter.check_and_reserve(key)
    assert ok2
    limiter._window[key].clear()  # simulate age-out before release
    limiter.release(key)
    assert key not in limiter._in_flight
    assert key not in limiter._window, (
        "Idle key with empty window must be pruned from both maps"
    )


# ---------------------------------------------------------------------------
# Rate limiter — integration test via HTTP POSTs
# ---------------------------------------------------------------------------


def test_rate_limit_over_minute_returns_429_with_retry_after(
    authed_client_with_fake_supervisor,
):
    """31st POST in a minute → 429 with ``Retry-After`` header.

    We use the fake supervisor so no real subprocesses spawn. The default
    ``max_per_minute`` is 30 (see ``config/config.yaml``). The 31st request
    must be rejected with a ``Retry-After`` hint.
    """
    client = authed_client_with_fake_supervisor

    body = {"ticket_id": "PROJ-1", "project": "proj", "kind": "execute"}
    # First 30 succeed.
    for i in range(30):
        r = client.post("/executions", json=body)
        assert r.status_code == 202, f"request {i} got {r.status_code}: {r.text}"

    # 31st is rate-limited.
    r = client.post("/executions", json=body)
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1
