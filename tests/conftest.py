"""Shared Phase 1 test fixtures.

Plan reference: phase-1-close-the-leash.plan.md §Tasks-12.

Six fixtures every Phase 1 test should be able to grab:

  1. ``postmortem_factory``      — dict-row builder
  2. ``structured_error_factory`` — :class:`StructuredError` builder
  3. ``sqlite_mem_conn``          — in-memory DB w/ migrations + parent execution row
  4. ``event_bus``                — :class:`EventBus` bound to ``sqlite_mem_conn``
  5. ``failing_forever_developer`` — Mock that always reports failure
  6. ``flaky_developer``          — factory: fails N times, then passes

Naming is deliberately distinct from existing local fixtures (e.g. the ``conn``
fixture in ``tests/core/test_postmortems.py``) so this module is purely additive.
A test that wants the in-memory DB asks for ``sqlite_mem_conn`` explicitly.

GOTCHA addressed in module: SQLite ``:memory:`` does not honor ``WAL`` journal
mode, but :func:`src.core.persistence.db.connect` issues the PRAGMA after open
and SQLite silently downgrades it. Confirmed working — no need to fall back to
``tmp_path``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from unittest.mock import Mock

import pytest

from src.agents._structured_errors import StructuredError
from src.core.events import EventBus
from src.core.persistence import apply_migrations, connect


# ---------------------------------------------------------------------------
# 1. postmortem_factory
# ---------------------------------------------------------------------------


@pytest.fixture
def postmortem_factory() -> Callable[..., dict[str, Any]]:
    """Build a postmortem-row dict with sensible defaults.

    Returns a callable ``make(**overrides) -> dict``. Defaults match the
    cap-out subscriber's insert shape (provenance='auto', fix_summary=None).
    """

    def make(**overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "execution_id": "test-exec-1",
            "stack_type": "drupal",
            "agent": "drupal_developer",
            "failure_signature": "phpstan.notfound undefined method",
            "context_excerpt": "[]",
            "fix_summary": None,
            "provenance": "auto",
            "confidence": 50,
        }
        row.update(overrides)
        return row

    return make


# ---------------------------------------------------------------------------
# 2. structured_error_factory
# ---------------------------------------------------------------------------


@pytest.fixture
def structured_error_factory() -> Callable[..., StructuredError]:
    """Build a :class:`StructuredError` with sensible defaults."""

    def make(**overrides: Any) -> StructuredError:
        base: dict[str, Any] = {
            "file": "src/foo.py",
            "line": 42,
            "rule": "test_failed",
            "message": "AssertionError",
        }
        base.update(overrides)
        # StructuredError is a TypedDict; cast through dict construction.
        return StructuredError(  # type: ignore[typeddict-item]
            file=base["file"],
            line=base["line"],
            rule=base["rule"],
            message=base["message"],
        )

    return make


# ---------------------------------------------------------------------------
# 3. sqlite_mem_conn
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_mem_conn() -> Iterator[sqlite3.Connection]:
    """In-memory SQLite connection with migrations + a parent execution row.

    Tests can rely on ``execution_id="test-exec-1"`` existing.

    GOTCHA: ``connect()`` issues ``PRAGMA journal_mode=WAL`` which SQLite
    silently no-ops on ``:memory:`` databases — confirmed working with
    ``apply_migrations``. If this ever starts failing on a future SQLite
    version, switch to a ``tmp_path``-backed file DB.
    """
    conn = connect(":memory:")
    apply_migrations(conn)
    conn.execute(
        """
        INSERT INTO executions (id, ticket_id, kind, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "test-exec-1",
            "TEST-1",
            "execute",
            "running",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. event_bus
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus(sqlite_mem_conn: sqlite3.Connection) -> EventBus:
    """An :class:`EventBus` bound to the in-memory DB."""
    return EventBus(sqlite_mem_conn)


# ---------------------------------------------------------------------------
# 5. failing_forever_developer
# ---------------------------------------------------------------------------


def _make_sdk_mock(side_effect_fn: Callable[..., Any]) -> Mock:
    """Build a Mock conforming to ``BaseDeveloperAgent.agent_sdk``.

    ``execute_with_tools`` is the only method the loop calls; we wire it as an
    async method with ``side_effect`` so test code can assert call_count and
    inspect prompts after the run.
    """
    wrapper = Mock()

    async def execute_with_tools(prompt: str, session_id: Any = None,
                                 system_prompt: str | None = None,
                                 cwd: str | None = None) -> dict[str, Any]:
        # Track the call so tests can assert on it.
        wrapper.execute_with_tools.calls.append(
            {"prompt": prompt, "session_id": session_id,
             "system_prompt": system_prompt, "cwd": cwd}
        )
        result: dict[str, Any] = side_effect_fn(prompt)
        return result

    wrapper.execute_with_tools = Mock(wraps=execute_with_tools)
    wrapper.execute_with_tools.calls = []
    wrapper.set_project = Mock()
    wrapper.agent_name = "test_developer"
    wrapper.model = "claude-4-5-sonnet"
    wrapper.llm_mode = "custom_proxy"
    wrapper.allowed_tools = ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]
    return wrapper


@pytest.fixture
def failing_forever_developer() -> Mock:
    """Mock SDK whose ``execute_with_tools`` always returns a "wrote a file" result.

    Intended pattern: attach to a real :class:`BaseDeveloperAgent`-subclass
    instance via ``agent.agent_sdk = failing_forever_developer``. Combine with
    a monkeypatched ``run_tests`` / ``run_static_checks`` that return
    ``{"passed": False, ...}`` so the loop reaches its cap.

    The mock itself records every call so cap-out tests can assert
    ``execute_with_tools.call_count == MAX_ATTEMPTS``.
    """
    def always_emit_one_edit(_prompt: str) -> dict[str, Any]:
        return {
            "content": "I changed a file.",
            "tool_uses": [
                {"tool": "Edit", "input": {"file_path": "/tmp/foo.py"}}
            ],
            "session_id": "test-session-failing-forever",
        }

    return _make_sdk_mock(always_emit_one_edit)


# ---------------------------------------------------------------------------
# 6. flaky_developer
# ---------------------------------------------------------------------------


@pytest.fixture
def flaky_developer() -> Callable[[int], Mock]:
    """Factory: returns a Mock whose ``execute_with_tools`` is "flaky".

    Usage::

        sdk = flaky_developer(2)  # fails first 2 calls, succeeds on 3rd
        agent.agent_sdk = sdk

    The mock itself doesn't decide pass/fail — it just emits a tool_use each
    call. Pair with the helper :func:`make_flaky_verifier_returns` below to
    encode the matching ``run_tests`` / ``run_static_checks`` sequence.
    """

    def factory(_n: int) -> Mock:
        def emit_edit(_prompt: str) -> dict[str, Any]:
            return {
                "content": "I changed a file.",
                "tool_uses": [
                    {"tool": "Edit", "input": {"file_path": "/tmp/bar.py"}}
                ],
                "session_id": "test-session-flaky",
            }

        return _make_sdk_mock(emit_edit)

    return factory


@pytest.fixture
def make_flaky_verifier_returns() -> Callable[[int], list[dict[str, Any]]]:
    """Helper: produce the list of ``run_tests`` returns matching ``flaky_developer(n)``.

    Returns ``n`` failure dicts followed by one success dict — total ``n+1``
    entries, suitable for ``Mock(side_effect=[...])`` on ``run_tests``.
    """

    def make(n: int) -> list[dict[str, Any]]:
        fail = {
            "passed": False,
            "test_results": "FAILED tests/test_x.py::test_a - AssertionError",
            "structured_errors": [
                {
                    "file": "tests/test_x.py",
                    "line": 0,
                    "rule": "test_failed",
                    "message": "AssertionError",
                }
            ],
            "return_code": 1,
        }
        success = {
            "passed": True,
            "test_results": "ok",
            "structured_errors": [],
            "return_code": 0,
        }
        return [dict(fail) for _ in range(n)] + [dict(success)]

    return make
