"""Renderer tests for ``src.core.learning.pitfalls``.

The renderer takes ``sqlite3.Row``-shaped rows. We synthesize them via an
in-memory ``SELECT`` so the test inputs match what
``query_active_postmortems`` actually returns at runtime.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.core.learning.pitfalls import (
    MAX_PITFALL_CHARS,
    render_pitfalls_section,
)


def _row(**fields: Any) -> sqlite3.Row:
    """Build a ``sqlite3.Row`` with the given fields via a no-op SELECT."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"? AS {k}" for k in fields)
    return conn.execute(f"SELECT {cols}", tuple(fields.values())).fetchone()


def _make_row(
    *,
    id: int,
    stack_type: str = "drupal",
    agent: str = "drupal_developer",
    confidence: int = 80,
    failure_signature: str = "phpunit::failed_assertion::foo",
    context_excerpt: str | None = "assertSame failed",
) -> sqlite3.Row:
    return _row(
        id=id,
        stack_type=stack_type,
        agent=agent,
        confidence=confidence,
        failure_signature=failure_signature,
        context_excerpt=context_excerpt,
    )


# ---------------------------------------------------------------------------


def test_empty_rows_returns_empty() -> None:
    section, dropped = render_pitfalls_section([])
    assert section == ""
    assert dropped == []


def test_single_row_emits_header_and_bullet() -> None:
    row = _make_row(id=1, confidence=88, failure_signature="sig-A")
    section, dropped = render_pitfalls_section([row])

    assert "## Known pitfalls" in section
    assert "[postmortem:1 stack:drupal agent:drupal_developer conf:88]" in section
    assert "sig-A" in section
    assert "assertSame failed" in section
    assert dropped == []


def test_no_truncation_under_cap() -> None:
    rows = [
        _make_row(id=i, confidence=80, failure_signature=f"sig-{i}")
        for i in range(3)
    ]
    section, dropped = render_pitfalls_section(rows)

    assert dropped == []
    for i in range(3):
        assert f"sig-{i}" in section


def test_truncation_drops_tail_when_over_cap() -> None:
    """Lowest-priority rows (input tail) are dropped first."""
    rows = [
        _make_row(
            id=i,
            confidence=90 - i,  # decreasing — input order matches priority
            failure_signature=f"sig-{i}",
            context_excerpt="x" * 200,
        )
        for i in range(50)
    ]
    section, dropped = render_pitfalls_section(rows, max_chars=2000)

    assert len(dropped) > 0
    # The earliest (highest-confidence) row must always fit.
    assert "sig-0" in section
    # Tail row (lowest-confidence, id=49) should be in dropped, not section.
    assert 49 in dropped
    assert "sig-49" not in section
    # Section length must be under the cap.
    assert len(section) <= 2000


def test_handles_null_context_excerpt() -> None:
    row = _make_row(
        id=1,
        stack_type="python",
        agent="python_developer",
        confidence=75,
        failure_signature="sig-null",
        context_excerpt=None,
    )
    section, dropped = render_pitfalls_section([row])

    assert "sig-null" in section
    assert dropped == []


def test_context_excerpt_truncated_to_200_chars() -> None:
    """Renderer caps context_excerpt at 200 chars to limit injection surface."""
    row = _make_row(
        id=1, failure_signature="sig", context_excerpt="A" * 1000
    )
    section, dropped = render_pitfalls_section([row])

    # Only 200 As should appear in the bullet.
    assert "A" * 200 in section
    assert "A" * 201 not in section


def test_dropped_ids_preserve_input_order() -> None:
    """``dropped`` should list IDs in the order they were skipped."""
    rows = [
        _make_row(
            id=i,
            confidence=90 - i,
            failure_signature=f"sig-{i}",
            context_excerpt="x" * 200,
        )
        for i in range(20)
    ]
    section, dropped = render_pitfalls_section(rows, max_chars=1500)

    # IDs in dropped should be a strictly increasing tail of the input order.
    assert dropped == sorted(dropped)


def test_max_pitfall_chars_is_8000() -> None:
    assert MAX_PITFALL_CHARS == 8000
