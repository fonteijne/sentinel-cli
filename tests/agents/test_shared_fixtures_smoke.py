"""Smoke tests for the Phase 1 shared fixtures defined in tests/conftest.py.

Plan reference: phase-1-close-the-leash.plan.md §Tasks-12.

Each test exercises one fixture in isolation. If a fixture is broken at the
contract level, this file fails first and signposts which fixture needs fixing
before the rest of the Phase 1 suite gets investigated.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Callable
from unittest.mock import Mock

from src.agents._structured_errors import StructuredError
from src.core.events import EventBus, TestResultRecorded


def test_postmortem_factory_defaults(
    postmortem_factory: Callable[..., dict],
) -> None:
    row = postmortem_factory()

    assert row["execution_id"] == "test-exec-1"
    assert row["stack_type"] == "drupal"
    assert row["agent"] == "drupal_developer"
    assert row["failure_signature"] == "phpstan.notfound undefined method"
    assert row["context_excerpt"] == "[]"
    assert row["fix_summary"] is None
    assert row["provenance"] == "auto"
    assert row["confidence"] == 50

    # Override path: passing a kwarg replaces only that field.
    row_override = postmortem_factory(stack_type="python", agent="python_developer")
    assert row_override["stack_type"] == "python"
    assert row_override["agent"] == "python_developer"
    # Untouched defaults still present.
    assert row_override["provenance"] == "auto"


def test_structured_error_factory_overrides(
    structured_error_factory: Callable[..., StructuredError],
) -> None:
    err = structured_error_factory(rule="custom_rule")

    assert err["file"] == "src/foo.py"
    assert err["line"] == 42
    assert err["rule"] == "custom_rule"
    assert err["message"] == "AssertionError"

    # Multiple overrides round-trip.
    err2 = structured_error_factory(file="x.py", line=7, message="boom")
    assert err2["file"] == "x.py"
    assert err2["line"] == 7
    assert err2["message"] == "boom"
    # Default rule still default.
    assert err2["rule"] == "test_failed"


def test_sqlite_mem_conn_has_executions_row(
    sqlite_mem_conn: sqlite3.Connection,
) -> None:
    row = sqlite_mem_conn.execute(
        "SELECT id, ticket_id, kind, status FROM executions WHERE id = ?",
        ("test-exec-1",),
    ).fetchone()

    assert row is not None, "fixture should pre-insert execution_id='test-exec-1'"
    assert row["id"] == "test-exec-1"
    assert row["ticket_id"] == "TEST-1"
    assert row["kind"] == "execute"
    assert row["status"] == "running"

    # And migrations actually applied (postmortems table exists).
    tables = {
        r[0]
        for r in sqlite_mem_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "postmortems" in tables
    assert "events" in tables


def test_event_bus_publishes_against_fixture_conn(
    event_bus: EventBus,
    sqlite_mem_conn: sqlite3.Connection,
) -> None:
    event_bus.publish(
        TestResultRecorded(
            execution_id="test-exec-1",
            passed=False,
            attempt=1,
            structured_errors_count=2,
            agent="python_developer",
        )
    )

    rows = event_bus.get_events("test-exec-1")
    assert len(rows) == 1, f"expected exactly 1 event row, got {len(rows)}"
    assert rows[0]["type"] == "TestResultRecorded"
    assert rows[0]["agent"] == "python_developer"
    assert rows[0]["execution_id"] == "test-exec-1"
    assert rows[0]["seq"] == 1


def test_failing_forever_developer_returns_failure_pattern(
    failing_forever_developer: Mock,
) -> None:
    # The fixture is async; drive it with asyncio.run to mirror how
    # base_developer.implement_feature() actually calls it.
    async def call_three_times() -> list[dict]:
        results = []
        for _ in range(3):
            r = await failing_forever_developer.execute_with_tools(
                prompt="do work", session_id=None,
                system_prompt="sys", cwd="/tmp",
            )
            results.append(r)
        return results

    results = asyncio.run(call_three_times())

    assert len(results) == 3
    # Mock recorded 3 calls.
    assert failing_forever_developer.execute_with_tools.call_count == 3
    # Each result has the expected shape (one Edit tool_use).
    for r in results:
        assert r["tool_uses"][0]["tool"] == "Edit"
        assert r["tool_uses"][0]["input"]["file_path"] == "/tmp/foo.py"


def test_flaky_developer_succeeds_after_n(
    flaky_developer: Callable[[int], Mock],
    make_flaky_verifier_returns: Callable[[int], list[dict]],
) -> None:
    sdk = flaky_developer(2)
    verifier_returns = make_flaky_verifier_returns(2)

    # Sequence is 2 fails + 1 success, total 3 entries.
    assert len(verifier_returns) == 3
    assert verifier_returns[0]["passed"] is False
    assert verifier_returns[1]["passed"] is False
    assert verifier_returns[2]["passed"] is True

    # The SDK mock itself doesn't pick pass/fail — it just emits tool_uses.
    async def call_once() -> dict:
        return await sdk.execute_with_tools(
            prompt="x", session_id=None, system_prompt="s", cwd="/tmp",
        )

    r = asyncio.run(call_once())
    assert r["tool_uses"][0]["tool"] == "Edit"
    assert sdk.execute_with_tools.call_count == 1
