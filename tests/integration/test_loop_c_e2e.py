"""Loop C end-to-end tests at the CLI-helper boundary.

This file exercises the *publish gate*: the pure helpers in ``src.cli`` that
classify reviewer findings, format the ``finding_class`` token, and gate
publication on environment flags. The full container e2e (sentinel execute →
appserver → reviewer) lives in ``test_container_e2e.sh``; here we cover the
helper logic that decides "should the workflow even publish a
ReviewerHandoffTriggered event" — the single decision point per
DECISIONS §168/§180.

Coverage map:
  - ``_extract_blockers``: severity-filter rules per reviewer agent (Drupal
    BLOCKER/MAJOR; Security critical + high-when->5; unknown-agent → []).
  - ``_format_finding_class``: top-3 cap, 80-char truncate with ``…`` suffix,
    Drupal id-or-title-fallback, security category lookup, ``unknown`` fallback.
  - Env flags: ``LOOP_C_ENABLED``, ``AUTO_INVESTIGATE_ENABLED``,
    ``LOOP_C_BLOCKER_THRESHOLD``.
"""

from __future__ import annotations

import pytest

from src.cli import (
    _auto_investigate_enabled,
    _extract_blockers,
    _format_finding_class,
    _loop_c_blocker_threshold,
    _loop_c_enabled,
)


# ---------------------------------------------------------------------------
# _extract_blockers
# ---------------------------------------------------------------------------


def test_extract_blockers_drupal_filters_to_blocker_and_major():
    findings = [
        {"id": "svc-injection", "severity": "BLOCKER"},
        {"id": "missing-hook", "severity": "MAJOR"},
        {"id": "style-warning", "severity": "INFO"},
        {"id": "lint-nit", "severity": "WARNING"},
    ]
    out = _extract_blockers(
        "drupal_reviewer", {"approved": False, "findings": findings}
    )
    assert [f["id"] for f in out] == ["svc-injection", "missing-hook"]


def test_extract_blockers_security_critical_only_when_high_at_or_below_5():
    """`high` severity only counts when `len(high) > 5`. Edge: 5 → dropped."""
    findings = [{"category": "xss", "severity": "critical"}] + [
        {"category": "auth", "severity": "high"} for _ in range(5)
    ]
    out = _extract_blockers(
        "security_reviewer", {"approved": False, "findings": findings}
    )
    # 1 critical, 5 high → high count not > 5, so high dropped.
    assert len(out) == 1
    assert out[0]["category"] == "xss"


def test_extract_blockers_security_promotes_high_when_above_5():
    findings = [{"category": "xss", "severity": "critical"}] + [
        {"category": "auth", "severity": "high"} for _ in range(6)
    ]
    out = _extract_blockers(
        "security_reviewer", {"approved": False, "findings": findings}
    )
    # 1 critical + 6 high → all 7 returned.
    assert len(out) == 7


def test_extract_blockers_unknown_agent_returns_empty():
    out = _extract_blockers(
        "mystery_reviewer",
        {"approved": False, "findings": [{"severity": "BLOCKER", "id": "x"}]},
    )
    assert out == []


# ---------------------------------------------------------------------------
# _format_finding_class
# ---------------------------------------------------------------------------


def test_format_finding_class_drupal_top_3_only():
    blockers = [
        {"id": "svc-injection"},
        {"id": "missing-hook"},
        {"id": "third"},
        {"id": "fourth"},  # truncated by top-3 cap
    ]
    fc = _format_finding_class("drupal_reviewer", blockers)
    assert fc == "svc-injection,missing-hook,third"
    assert len(fc) <= 80


def test_format_finding_class_security_uses_category():
    blockers = [{"category": "xss"}, {"category": "sqli"}]
    assert _format_finding_class("security_reviewer", blockers) == "xss,sqli"


def test_format_finding_class_truncates_at_80_chars():
    """Long ids → output exactly 80 chars, ends with ``…`` suffix."""
    blockers = [{"id": "a" * 40}, {"id": "b" * 40}, {"id": "c" * 40}]
    fc = _format_finding_class("drupal_reviewer", blockers)
    assert len(fc) == 80
    assert fc.endswith("…")
    # The ``…`` is one Unicode codepoint, so the prefix is exactly 79 chars.
    assert fc[:-1] == ("a" * 40 + "," + "b" * 38)


def test_format_finding_class_drupal_falls_back_to_unknown():
    """Drupal blockers without id/title → 'unknown'."""
    blockers = [{"severity": "BLOCKER"}, {"severity": "MAJOR"}]
    fc = _format_finding_class("drupal_reviewer", blockers)
    assert fc == "unknown,unknown"


def test_format_finding_class_drupal_uses_title_first_word_when_no_id():
    blockers = [{"title": "Service injection breaks DI"}]
    fc = _format_finding_class("drupal_reviewer", blockers)
    # First whitespace-separated token of title.
    assert fc == "Service"


# ---------------------------------------------------------------------------
# Env-flag publish gate.
# ---------------------------------------------------------------------------


def test_loop_c_enabled_default_off(monkeypatch):
    monkeypatch.delenv("LOOP_C_ENABLED", raising=False)
    assert _loop_c_enabled() is False


def test_loop_c_enabled_when_set_to_one(monkeypatch):
    monkeypatch.setenv("LOOP_C_ENABLED", "1")
    assert _loop_c_enabled() is True


def test_loop_c_enabled_only_one_truthy_value(monkeypatch):
    """Decision: only ``"1"`` enables the flag — ``"true"`` and ``"yes"`` do not."""
    monkeypatch.setenv("LOOP_C_ENABLED", "true")
    assert _loop_c_enabled() is False


def test_auto_investigate_flag_round_trip(monkeypatch):
    monkeypatch.delenv("AUTO_INVESTIGATE_ENABLED", raising=False)
    assert _auto_investigate_enabled() is False
    monkeypatch.setenv("AUTO_INVESTIGATE_ENABLED", "1")
    assert _auto_investigate_enabled() is True


def test_loop_c_blocker_threshold_default(monkeypatch):
    monkeypatch.delenv("LOOP_C_BLOCKER_THRESHOLD", raising=False)
    assert _loop_c_blocker_threshold() == 1


def test_loop_c_blocker_threshold_override(monkeypatch):
    monkeypatch.setenv("LOOP_C_BLOCKER_THRESHOLD", "3")
    assert _loop_c_blocker_threshold() == 3
