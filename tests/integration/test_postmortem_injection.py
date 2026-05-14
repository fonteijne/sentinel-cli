"""Phase 2A exit-criterion integration tests: postmortem visibility in prompts.

Plan ref: phase-2a-pitfalls-visible.plan.md Task 11.

This module is THE EXIT CRITERION for Phase 2A: Run-N's postmortem must show
up in Run-N+1's planner prompt under a ``## Known pitfalls`` header. The
fixture loads the **real** ``prompts/`` directory so we verify the actual
``plan_generator.md`` + ``shared/base_instructions.md`` get the pitfalls block
appended — not a synthetic stand-in.

Other behaviors verified here (each is an exit-criterion line in HANDOVER §7):

  * Two-stack isolation — drupal pitfalls do NOT leak into a python-scoped load.
  * Confidence floor — rows below 70 are not injected.
  * Superseded rows — never injected.
  * Cache invalidation — a fresh row appears after PostmortemRecorded fires.
  * Flag-off byte-for-byte parity — POSTMORTEM_INJECTION=0 yields the same
    bytes as a no-kwargs load.

Setup follows ``tests/integration/test_verifier_retry.py`` — in-memory SQLite
via the shared ``sqlite_mem_conn`` fixture, ``test-exec-1`` already inserted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.events import EventBus, PostmortemRecorded
from src.core.learning.cache_invalidator import register_prompt_cache_invalidator
from src.core.persistence import insert_postmortem
from src.prompt_loader import PromptLoader


# Real prompts directory — the test verifies the production-side files render
# correctly when pitfalls are appended. NOT a temp tree.
REAL_PROMPTS_DIR = Path("/workspace/sentinel/prompts")


@pytest.fixture
def loader() -> PromptLoader:
    """Fresh :class:`PromptLoader` rooted at the real prompts directory.

    A new instance per test so cache state doesn't leak across cases.
    """
    return PromptLoader(prompts_dir=REAL_PROMPTS_DIR)


@pytest.fixture(autouse=True)
def _enable_injection_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 2A kill-switch: most tests in this module run with the flag ON.

    The single flag-off test re-sets it to "0" inside the test body.
    """
    monkeypatch.setenv("POSTMORTEM_INJECTION", "1")


# ---------------------------------------------------------------------------
# 1. THE EXIT CRITERION: Run-N postmortem visible in Run-N+1's prompt
# ---------------------------------------------------------------------------


def test_run_n_postmortem_visible_in_run_n_plus_1_prompt(
    sqlite_mem_conn: sqlite3.Connection, loader: PromptLoader
) -> None:
    """Phase 2A exit criterion.

    Run N inserts a postmortem; Run N+1's planner prompt must contain it under
    a ``## Known pitfalls`` heading with the bullet-header format the renderer
    emits.
    """
    pm_id = insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="phpunit::failed_assertion::sentinel_demo",
        context_excerpt="failure context",
        fix_summary=None,
        provenance="auto",
        confidence=88,
    )

    prompt = loader.load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )

    assert "## Known pitfalls" in prompt, (
        "Known pitfalls section missing — flag off, or renderer skipped"
    )
    assert "phpunit::failed_assertion::sentinel_demo" in prompt
    # Bullet header substring — proves the renderer ran (not a raw row dump).
    assert f"[postmortem:{pm_id} stack:drupal" in prompt


# ---------------------------------------------------------------------------
# 2. Stack isolation
# ---------------------------------------------------------------------------


def test_parallel_two_stack_isolation(
    sqlite_mem_conn: sqlite3.Connection, loader: PromptLoader
) -> None:
    """A drupal postmortem must NOT appear in a python-scoped prompt load."""
    insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="drupal.only.iso",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=80,
    )

    drupal_prompt = loader.load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )
    python_prompt = loader.load(
        "plan_generator", stack_type="python", conn=sqlite_mem_conn
    )

    assert "drupal.only.iso" in drupal_prompt
    assert "drupal.only.iso" not in python_prompt
    # No python rows → renderer emits no bullets. The literal "## Known pitfalls"
    # heading appears in base_instructions.md (hardening clause), so probe for
    # the renderer-emitted bullet marker instead.
    assert "[postmortem:" not in python_prompt
    assert "[postmortem:" in drupal_prompt


# ---------------------------------------------------------------------------
# 3. Confidence floor (70)
# ---------------------------------------------------------------------------


def test_below_confidence_floor_not_injected(
    sqlite_mem_conn: sqlite3.Connection, loader: PromptLoader
) -> None:
    """A postmortem with confidence=50 (below the 70 floor) must not be injected."""
    insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="too.low.to.inject",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=50,
    )

    prompt = loader.load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )

    assert "too.low.to.inject" not in prompt
    # No rows passed the floor → renderer emits no bullets. The literal
    # "## Known pitfalls" string appears in base_instructions.md (hardening
    # clause), so probe for the renderer-emitted bullet marker instead.
    assert "[postmortem:" not in prompt


# ---------------------------------------------------------------------------
# 4. Superseded rows
# ---------------------------------------------------------------------------


def test_superseded_postmortem_not_injected(
    sqlite_mem_conn: sqlite3.Connection, loader: PromptLoader
) -> None:
    """A row with ``superseded_by`` set must NOT appear in the rendered prompt."""
    old_id = insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="superseded.signature",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=80,
    )
    new_id = insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="replacement.signature",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=80,
    )

    sqlite_mem_conn.execute(
        "UPDATE postmortems SET superseded_by = ? WHERE id = ?",
        (new_id, old_id),
    )
    sqlite_mem_conn.commit()

    prompt = loader.load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )

    assert "replacement.signature" in prompt
    assert "superseded.signature" not in prompt, (
        "superseded row leaked into prompt injection"
    )


# ---------------------------------------------------------------------------
# 5. Cache invalidation on PostmortemRecorded
# ---------------------------------------------------------------------------


def test_cache_invalidation_on_postmortem_recorded(
    sqlite_mem_conn: sqlite3.Connection,
    event_bus: EventBus,
    loader: PromptLoader,
) -> None:
    """``PostmortemRecorded`` clears the cache so subsequent loads see new rows.

    Without invalidation the second ``load()`` would return the cached prompt
    text from before the new row landed. With it wired, the new signature
    appears.
    """
    register_prompt_cache_invalidator(event_bus, loader)

    # Seed one row, then prime the cache.
    insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="initial.cached.sig",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=80,
    )
    first = loader.load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )
    assert "initial.cached.sig" in first

    # Insert a new row that the cached prompt does NOT yet reflect.
    new_id = insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="post.cache.invalidated.sig",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=80,
    )

    # Without the invalidator, this would return the stale cached prompt.
    event_bus.publish(
        PostmortemRecorded(
            execution_id="test-exec-1",
            ts="",
            postmortem_id=new_id,
            failure_signature="post.cache.invalidated.sig",
        )
    )

    second = loader.load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )
    assert "post.cache.invalidated.sig" in second, (
        "cache was not invalidated — new postmortem missing from prompt"
    )
    assert "initial.cached.sig" in second  # original still present


# ---------------------------------------------------------------------------
# 6. Flag-off byte-for-byte parity
# ---------------------------------------------------------------------------


def test_flag_off_yields_byte_for_byte_identical(
    sqlite_mem_conn: sqlite3.Connection,
    loader: PromptLoader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the kill-switch off, the prompt is identical to a no-kwargs load.

    Insert a postmortem first to make sure the flag-off path actually skips
    the renderer (rather than passing through because the table is empty).
    """
    monkeypatch.setenv("POSTMORTEM_INJECTION", "0")

    insert_postmortem(
        sqlite_mem_conn,
        execution_id="test-exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="should.not.appear.flag.off",
        context_excerpt="ctx",
        fix_summary=None,
        provenance="auto",
        confidence=88,
    )

    # Two parallel PromptLoaders with isolated caches: one called with the
    # injection kwargs (but flag off), one called without.
    flagged = PromptLoader(prompts_dir=REAL_PROMPTS_DIR).load(
        "plan_generator", stack_type="drupal", conn=sqlite_mem_conn
    )
    baseline = PromptLoader(prompts_dir=REAL_PROMPTS_DIR).load("plan_generator")

    assert flagged == baseline, (
        "flag-off load diverged from no-kwargs load — injection happened anyway"
    )
    assert "should.not.appear.flag.off" not in flagged
    # base_instructions.md contains the literal "## Known pitfalls" string in
    # its hardening clause; check for the renderer-emitted bullet marker
    # instead — that only exists when injection actually fired.
    assert "[postmortem:" not in flagged
