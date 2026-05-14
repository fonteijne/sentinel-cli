"""Tests for ``src.core.learning.extract`` (Phase 2C extraction half).

Fixture style mirrors ``tests/core/test_postmortem_clusters.py`` —
in-memory SQLite, ``PRAGMA foreign_keys=ON``, raw INSERTs of postmortems
with explicit ``created_at`` so window/ordering semantics are deterministic.
Parent ``executions`` rows are inserted to satisfy the FK and to drive
``project_key`` derivation.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterator

import pytest

from src.core.learning.extract import (
    ExtractionSummary,
    compute_confidence,
    extract_clusters,
    is_pure_symptom,
)
from src.core.persistence import apply_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_execution(
    c: sqlite3.Connection,
    *,
    exec_id: str,
    ticket_id: str,
) -> None:
    c.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            exec_id,
            ticket_id,
            "developer",
            "completed",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    c.commit()


def _seed_postmortem(
    c: sqlite3.Connection,
    *,
    execution_id: str,
    stack_type: str = "drupal",
    agent: str = "drupal_developer",
    failure_signature: str,
    created_at: str | None = None,
    confidence: int = 50,
) -> int:
    cur = c.execute(
        """
        INSERT INTO postmortems (
            execution_id, stack_type, agent, failure_signature,
            context_excerpt, fix_summary, provenance, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            execution_id,
            stack_type,
            agent,
            failure_signature,
            None,
            None,
            "auto",
            confidence,
            created_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    c.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _seed_three_projects_for_signature(
    c: sqlite3.Connection,
    *,
    failure_signature: str,
    project_count: int = 2,
    obs_count: int = 3,
) -> list[int]:
    """Seed ``obs_count`` postmortems spread across ``project_count`` projects.

    Project tickets are drawn from ``ACME-``, ``BRAVO-``, ``CHARLIE-`` so
    project_key derivation produces ``project_count`` distinct uppercase
    prefixes.
    """
    project_prefixes = ["ACME", "BRAVO", "CHARLIE"][:project_count]
    pm_ids: list[int] = []
    for i in range(obs_count):
        prefix = project_prefixes[i % len(project_prefixes)]
        exec_id = f"exec-{prefix.lower()}-{i}"
        ticket = f"{prefix}-{100 + i}"
        _seed_execution(c, exec_id=exec_id, ticket_id=ticket)
        pm_ids.append(
            _seed_postmortem(
                c,
                execution_id=exec_id,
                failure_signature=failure_signature,
            )
        )
    return pm_ids


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------


def test_compute_confidence_curve() -> None:
    # 1 obs / 1 proj → base only.
    assert compute_confidence(1, 1) == 50
    # 3 obs / 2 proj → 50 + 10·min(5,2) + 5·min(3,1) = 50 + 20 + 5 = 75.
    assert compute_confidence(3, 2) == 75
    # 6 obs / 4 proj → 50 + 10·min(5,5) + 5·min(3,3) = 50 + 50 + 15 = 115 → clamp 95.
    assert compute_confidence(6, 4) == 95
    # 0 obs / 0 proj → max(0, ...) clamps both terms to 0; base survives.
    assert compute_confidence(0, 0) == 50
    # Negative inputs clamped to 0; base survives.
    assert compute_confidence(-3, -7) == 50
    # Edge: huge inputs still clamp to 95.
    assert compute_confidence(10_000, 10_000) == 95


# ---------------------------------------------------------------------------
# is_pure_symptom
# ---------------------------------------------------------------------------


def test_is_pure_symptom_blocks_generic() -> None:
    # Bare blacklisted phrases — pure symptoms.
    assert is_pure_symptom("failed assertion") is True
    assert is_pure_symptom("Failed Assertion") is True  # case insensitive
    assert is_pure_symptom("syntax error") is True

    # Specific signatures with structural tokens — NOT pure symptom.
    assert is_pure_symptom("failed assertion in foo::bar") is False
    assert is_pure_symptom("phpunit::failed_assertion::sentinel_demo") is False
    # Has a digit (line number etc.) → not pure.
    assert is_pure_symptom("failed assertion 42") is False
    # Contains a dot → structural enough.
    assert is_pure_symptom("failed assertion in app.module") is False

    # Long signature without blacklist prefix — short-circuits to False.
    assert (
        is_pure_symptom("a" * 80)
        is False
    )
    # ≥ 30 chars after normalization, even if it starts with a blacklist phrase.
    long_with_prefix = "failed assertion " + "x" * 30
    assert is_pure_symptom(long_with_prefix) is False

    # Doesn't start with a blacklisted phrase at all.
    assert is_pure_symptom("undefined hook in module") is False
    assert is_pure_symptom("") is False


# ---------------------------------------------------------------------------
# extract_clusters — threshold + whack-a-mole filters
# ---------------------------------------------------------------------------


def test_extract_below_thresholds_rejected(conn: sqlite3.Connection) -> None:
    # 2 obs across 1 project (both ACME-...) → fails BOTH min_observations=3
    # and min_projects=2; counted as one rejection.
    _seed_execution(conn, exec_id="exec-acme-1", ticket_id="ACME-101")
    _seed_execution(conn, exec_id="exec-acme-2", ticket_id="ACME-102")
    _seed_postmortem(
        conn, execution_id="exec-acme-1", failure_signature="phpunit::Foo::testBar"
    )
    _seed_postmortem(
        conn, execution_id="exec-acme-2", failure_signature="phpunit::Foo::testBar"
    )

    summary = extract_clusters(conn)

    assert summary.considered == 1
    assert summary.accepted == 0
    assert summary.rejected_below_thresholds == 1
    assert summary.rejected_pure_symptom == 0
    assert summary.rules == []

    # No DB writes.
    rows = conn.execute("SELECT COUNT(*) AS n FROM feedback_rules").fetchone()
    assert rows["n"] == 0


def test_extract_pure_symptom_rejected(conn: sqlite3.Connection) -> None:
    # 5 observations, 3 projects — meets size thresholds, but the signature is
    # generic ("failed assertion"). Whack-a-mole filter must reject.
    _seed_three_projects_for_signature(
        conn, failure_signature="failed assertion", project_count=3, obs_count=5
    )

    summary = extract_clusters(conn)

    assert summary.considered == 1
    assert summary.accepted == 0
    assert summary.rejected_pure_symptom == 1
    assert summary.rejected_below_thresholds == 0

    rows = conn.execute("SELECT COUNT(*) AS n FROM feedback_rules").fetchone()
    assert rows["n"] == 0


# ---------------------------------------------------------------------------
# extract_clusters — happy path
# ---------------------------------------------------------------------------


def test_extract_happy_path_inserts_probation_row(conn: sqlite3.Connection) -> None:
    pm_ids = _seed_three_projects_for_signature(
        conn,
        failure_signature="phpunit::Foo::testBar::sentinel_demo",
        project_count=2,
        obs_count=3,
    )

    summary = extract_clusters(conn)

    assert summary.considered == 1
    assert summary.accepted == 1
    assert summary.rejected_below_thresholds == 0
    assert summary.rejected_pure_symptom == 0
    assert len(summary.rules) == 1

    result = summary.rules[0]
    assert result.rule_id > 0
    assert result.signature == "phpunit::Foo::testBar::sentinel_demo"
    assert result.scope == "drupal"
    assert result.agent_target == "drupal_developer"
    assert result.observation_count == 3
    assert result.distinct_projects == 2
    assert result.confidence == 75  # 50 + 20 + 5
    assert result.first_postmortem_id == min(pm_ids)
    assert result.last_postmortem_id == max(pm_ids)

    row = conn.execute(
        "SELECT * FROM feedback_rules WHERE id = ?", (result.rule_id,)
    ).fetchone()
    assert row is not None
    assert row["status"] == "probation"
    assert row["confidence"] == 75
    assert row["scope"] == "drupal"
    assert row["agent_target"] == "drupal_developer"
    assert row["rule_text"] == "phpunit::Foo::testBar::sentinel_demo"
    assert row["observation_count"] == 3
    assert row["distinct_projects"] == 2
    assert row["first_postmortem_id"] == min(pm_ids)
    assert row["last_postmortem_id"] == max(pm_ids)


# ---------------------------------------------------------------------------
# extract_clusters — idempotency
# ---------------------------------------------------------------------------


def test_extract_idempotent_on_rerun(conn: sqlite3.Connection) -> None:
    _seed_three_projects_for_signature(
        conn,
        failure_signature="phpunit::Idempotent::testRerun",
        project_count=2,
        obs_count=3,
    )

    first = extract_clusters(conn)
    assert first.accepted == 1
    rule_id_first = first.rules[0].rule_id

    row_after_first = conn.execute(
        "SELECT id, updated_at FROM feedback_rules"
    ).fetchone()
    updated_at_first = row_after_first["updated_at"]

    # ``upsert_rule`` stamps ``updated_at`` to ``datetime.now(...)`` — sleep a
    # hair so the second run's stamp is strictly later (matches wave-1 helper
    # test idiom).
    time.sleep(0.01)

    second = extract_clusters(conn)
    assert second.accepted == 1
    rule_id_second = second.rules[0].rule_id
    assert rule_id_first == rule_id_second

    # Single row in feedback_rules (UPSERT, not INSERT).
    count = conn.execute("SELECT COUNT(*) AS n FROM feedback_rules").fetchone()["n"]
    assert count == 1

    row_after_second = conn.execute(
        "SELECT id, updated_at FROM feedback_rules"
    ).fetchone()
    assert row_after_second["id"] == row_after_first["id"]
    assert row_after_second["updated_at"] > updated_at_first


# ---------------------------------------------------------------------------
# extract_clusters — dry run
# ---------------------------------------------------------------------------


def test_extract_dry_run_no_writes(conn: sqlite3.Connection) -> None:
    _seed_three_projects_for_signature(
        conn,
        failure_signature="phpunit::DryRun::testNoWrite",
        project_count=2,
        obs_count=3,
    )

    summary = extract_clusters(conn, dry_run=True)

    assert summary.accepted == 1
    assert len(summary.rules) == 1
    assert summary.rules[0].rule_id == -1

    # No row in feedback_rules.
    count = conn.execute("SELECT COUNT(*) AS n FROM feedback_rules").fetchone()["n"]
    assert count == 0


# ---------------------------------------------------------------------------
# extract_clusters — superseded postmortems excluded
# ---------------------------------------------------------------------------


def test_extract_skips_superseded_postmortems(conn: sqlite3.Connection) -> None:
    pm_ids = _seed_three_projects_for_signature(
        conn,
        failure_signature="phpunit::Superseded::testFoo",
        project_count=2,
        obs_count=3,
    )

    # Mark one postmortem as superseded by another → cluster effectively size 2.
    conn.execute(
        "UPDATE postmortems SET superseded_by = ? WHERE id = ?",
        (pm_ids[0], pm_ids[1]),
    )
    conn.commit()

    summary = extract_clusters(conn, min_observations=3, min_projects=2)

    # Cluster shrank to 2 observations → fails min_observations=3 threshold.
    assert summary.considered == 1
    assert summary.accepted == 0
    assert summary.rejected_below_thresholds == 1


# ---------------------------------------------------------------------------
# extract_clusters — empty project keys dropped
# ---------------------------------------------------------------------------


def test_extract_drops_empty_project_keys(conn: sqlite3.Connection) -> None:
    # All three postmortems' parent executions have non-Jira tickets (no dash).
    # ``project_key`` is empty for each → distinct_projects = 0 (we drop empties).
    for i in range(3):
        exec_id = f"exec-bare-{i}"
        _seed_execution(conn, exec_id=exec_id, ticket_id="whatever")
        _seed_postmortem(
            conn,
            execution_id=exec_id,
            failure_signature="phpunit::Bare::testNoTicketPrefix",
        )

    summary = extract_clusters(conn, min_observations=3, min_projects=2)

    assert summary.considered == 1
    assert summary.accepted == 0
    # 3 obs ≥ 3 but 0 projects < 2 → rejected as below thresholds.
    assert summary.rejected_below_thresholds == 1


# ---------------------------------------------------------------------------
# extract_clusters — event emission
# ---------------------------------------------------------------------------


class _FakeEventBus:
    """Tiny stand-in for ``src.core.events.bus.EventBus``.

    The extractor only calls ``.publish(event)``, so we just record what was
    passed in. Using a stub class (rather than ``MagicMock``) keeps the test
    output readable when an assertion fails.
    """

    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, event: object) -> None:
        self.published.append(event)


def test_extract_emits_event_when_bus_provided(conn: sqlite3.Connection) -> None:
    from src.core.events.types import FeedbackRuleExtracted  # local import; module isolated

    _seed_three_projects_for_signature(
        conn,
        failure_signature="phpunit::Event::testEmits",
        project_count=2,
        obs_count=3,
    )

    bus = _FakeEventBus()
    summary: ExtractionSummary = extract_clusters(conn, event_bus=bus)

    assert summary.accepted == 1
    assert len(bus.published) == 1

    event = bus.published[0]
    assert isinstance(event, FeedbackRuleExtracted)
    assert event.rule_id == summary.rules[0].rule_id
    assert event.signature == "phpunit::Event::testEmits"
    assert event.scope == "drupal"
    assert event.agent_target == "drupal_developer"
    assert event.observation_count == 3
    assert event.distinct_projects == 2
    assert event.confidence == 75
    assert event.execution_id.startswith("learning-extract-")
