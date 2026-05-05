"""Tests for the canonical execution-options model.

The acceptance criteria for the Command Center replacement work require:
* CLI / API / UI never silently drop a CLI flag.
* Unsupported options fail validation rather than being dropped.
* The persisted form is versioned so newer workers can refuse to resume
  old/incompatible option sets.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.execution.options import (
    OPTIONS_SCHEMA_VERSION,
    DebriefOptions,
    ExecuteOptions,
    PlanOptions,
    from_metadata_options,
    to_metadata_options,
)


# ----------------------------------------------------------------- ExecuteOptions


def test_execute_options_round_trip_through_metadata():
    options = ExecuteOptions(
        revise=True,
        force=True,
        no_env=True,
        max_iterations=3,
        max_turns=12,
        prompt="please be thorough",
    )
    persisted = to_metadata_options(options)
    assert persisted["schema_version"] == OPTIONS_SCHEMA_VERSION
    assert persisted["values"]["revise"] is True
    assert persisted["values"]["max_iterations"] == 3

    rehydrated = from_metadata_options("execute", persisted)
    assert isinstance(rehydrated, ExecuteOptions)
    assert rehydrated.revise is True
    assert rehydrated.no_env is True
    assert rehydrated.max_iterations == 3
    assert rehydrated.max_turns == 12
    assert rehydrated.prompt == "please be thorough"


def test_execute_options_rejects_unknown_flag():
    """The whole point of this work: an unsupported flag must FAIL — not be
    silently dropped — so operators learn immediately."""
    with pytest.raises(ValidationError):
        ExecuteOptions.model_validate({"revise": True, "bogus": 1})


def test_execute_options_max_iterations_bounds_are_enforced():
    with pytest.raises(ValidationError):
        ExecuteOptions(max_iterations=0)
    with pytest.raises(ValidationError):
        ExecuteOptions(max_iterations=999)


def test_execute_options_prompt_length_capped():
    with pytest.raises(ValidationError):
        ExecuteOptions(prompt="x" * 9000)


# -------------------------------------------------------------------- PlanOptions


def test_plan_options_does_not_accept_revise():
    """``--revise`` is intentionally not on PlanOptions: ``sentinel plan`` is
    deprecated for that flag and silently ignored it locally. Remotely, it
    must raise so an operator does not get a misleading green run.
    """
    with pytest.raises(ValidationError):
        PlanOptions.model_validate({"force": True, "revise": True})


def test_plan_options_supports_force_and_prompt():
    p = PlanOptions(force=True, prompt="frob the widget")
    assert p.force is True
    assert p.prompt == "frob the widget"


# ----------------------------------------------------------------- DebriefOptions


def test_debrief_options_accepts_follow_up_ticket_pattern():
    d = DebriefOptions(follow_up_ticket="ACME-123")
    assert d.follow_up_ticket == "ACME-123"


def test_debrief_options_rejects_invalid_ticket_id():
    with pytest.raises(ValidationError):
        DebriefOptions(follow_up_ticket="not-a-ticket")


def test_debrief_options_treats_empty_string_as_none():
    d = DebriefOptions.model_validate({"follow_up_ticket": ""})
    assert d.follow_up_ticket is None


# ---------------------------------------------------------- versioning behaviour


def test_legacy_unversioned_rows_are_validated_against_current_schema():
    """Old scaffold rows persisted ``{"revise": True}`` directly under
    ``options`` (no schema_version). The rehydrator should accept that on
    a best-effort basis but still apply ``extra="forbid"`` so a legacy row
    with a misnamed flag fails loudly the next time someone retries it."""
    rehydrated = from_metadata_options("execute", {"revise": True})
    assert isinstance(rehydrated, ExecuteOptions)
    assert rehydrated.revise is True

    with pytest.raises(ValidationError):
        from_metadata_options("execute", {"revise": True, "bogus_flag": 7})


def test_future_schema_version_is_refused():
    persisted = {
        "schema_version": OPTIONS_SCHEMA_VERSION + 5,
        "values": {"revise": True},
    }
    with pytest.raises(ValueError, match="schema_version"):
        from_metadata_options("execute", persisted)


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown execution kind"):
        from_metadata_options("nonsense", None)
