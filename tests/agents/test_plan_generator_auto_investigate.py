"""Tests for the Phase 2B Step 3.5 auto-investigation hook in
``PlanGeneratorAgent.run()``.

Three behaviors gate on:
  1. ``AUTO_INVESTIGATE_ENABLED`` env flag (off by default → no investigation),
  2. ``evaluation['passed']`` (passing eval → no investigation),
  3. ``evaluation['questions']`` non-empty (no questions → short-circuit).

When all three line up: ``_investigate_confidence_questions`` runs once,
``generate_plan`` runs again with the findings, and ``_evaluate_confidence``
runs again exactly once. The retry is capped at 1 — there is no third loop.

Mock surface area: every method ``run()`` calls is stubbed via
``monkeypatch.setattr`` on the agent instance, including the SDK-touching
``analyze_ticket``/``generate_plan``/``_evaluate_confidence`` methods, the
git/GitLab side-effect helpers, and the auto-profile helper. We never enter
the real SDK or real Jira/GitLab clients.

We instantiate ``PlanGeneratorAgent`` via ``__new__`` to skip the
heavyweight ``__init__`` (which loads config, prompts, jira, gitlab) — the
hook under test only depends on instance methods, not on construction state.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pytest

from src.agents.plan_generator import PlanGeneratorAgent


# ---------------------------------------------------------------------------
# Bare-bones agent instance.
# ---------------------------------------------------------------------------


@pytest.fixture
def agent(monkeypatch):
    """A ``PlanGeneratorAgent`` skeleton with every ``run()`` callee stubbed.

    The auto-investigation hook only inspects ``evaluation`` shape and the
    ``_auto_investigate_enabled()`` env flag, then re-runs ``generate_plan`` /
    ``_evaluate_confidence`` once. We don't need a real config/SDK/Jira/GitLab
    for any of that — we just need the dependent methods to record their calls.
    """
    a = PlanGeneratorAgent.__new__(PlanGeneratorAgent)
    # Minimal instance state ``run()`` reads directly (not via methods).
    a.jira = Mock()
    a.gitlab = Mock()
    a.session_id = None
    a.messages = []

    # Side-effect helpers (commit/push/MR/Jira/discussions): no-ops.
    monkeypatch.setattr(a, "set_project", Mock())
    monkeypatch.setattr(a, "_auto_profile_if_needed", Mock())
    monkeypatch.setattr(
        a,
        "_detect_plan_state",
        Mock(return_value={"state": "initial", "discussions": [], "new_comments": []}),
    )
    monkeypatch.setattr(a, "analyze_ticket", Mock(return_value={"requirements": ["r1"]}))
    monkeypatch.setattr(a, "commit_and_push_plan", Mock(return_value=True))
    monkeypatch.setattr(
        a, "create_or_get_mr", Mock(return_value=("https://gitlab/mr/1", True))
    )
    monkeypatch.setattr(a, "_post_confidence_report", Mock())
    monkeypatch.setattr(a, "_reply_to_discussions", Mock())
    monkeypatch.setattr(a, "_post_investigation_report", Mock())

    # ``ctx`` is built inside run(); the constructor only takes (jira, ticket_id).
    # Stub the builder import-site so it doesn't try to fetch a real ticket.
    import src.agents.plan_generator as plan_mod

    fake_ctx = Mock()
    fake_ctx.format_ticket_context.return_value = "ticket context"
    monkeypatch.setattr(
        plan_mod, "TicketContextBuilder", Mock(return_value=fake_ctx)
    )

    return a


@pytest.fixture
def worktree():
    with TemporaryDirectory() as t:
        yield Path(t)


def _eval_failing(score: int = 60, questions=None) -> dict:
    return {
        "passed": False,
        "confidence_score": score,
        "questions": list(questions) if questions is not None else ["Q1", "Q2"],
        "report_markdown": "## Report\nlow",
    }


def _eval_passing(score: int = 96) -> dict:
    return {
        "passed": True,
        "confidence_score": score,
        "questions": [],
        "report_markdown": "## Report\nfine",
    }


# ---------------------------------------------------------------------------
# Case 1: flag OFF, low score, questions present → no investigation.
# ---------------------------------------------------------------------------


def test_auto_investigate_disabled_skips_step_3_5(agent, worktree, monkeypatch):
    monkeypatch.setenv("AUTO_INVESTIGATE_ENABLED", "0")

    gen = Mock(return_value="# Plan v1")
    eval_fn = Mock(return_value=_eval_failing())
    investigate = Mock(return_value="should not be called")
    monkeypatch.setattr(agent, "generate_plan", gen)
    monkeypatch.setattr(agent, "_evaluate_confidence", eval_fn)
    monkeypatch.setattr(agent, "_investigate_confidence_questions", investigate)

    result = agent.run("PROJ-1", worktree)

    investigate.assert_not_called()
    assert gen.call_count == 1
    assert eval_fn.call_count == 1
    # Final eval is the (one and only) eval — passed=False, score=60.
    assert result["evaluation"]["confidence_score"] == 60


# ---------------------------------------------------------------------------
# Case 2: flag ON, low score, 3 questions → investigate + re-gen + re-eval.
# ---------------------------------------------------------------------------


def test_auto_investigate_enabled_runs_one_retry_cycle(agent, worktree, monkeypatch):
    monkeypatch.setenv("AUTO_INVESTIGATE_ENABLED", "1")

    gen = Mock(side_effect=["# Plan v1", "# Plan v2"])
    # Two evals: initial fails (questions present), retry passes.
    first_eval = _eval_failing(score=60, questions=["Q1", "Q2", "Q3"])
    second_eval = _eval_passing(score=92)
    eval_fn = Mock(side_effect=[first_eval, second_eval])
    investigate = Mock(return_value="## Findings\nFound it.")
    monkeypatch.setattr(agent, "generate_plan", gen)
    monkeypatch.setattr(agent, "_evaluate_confidence", eval_fn)
    monkeypatch.setattr(agent, "_investigate_confidence_questions", investigate)

    result = agent.run("PROJ-2", worktree)

    # Investigate called exactly once with the questions list from the first eval.
    investigate.assert_called_once()
    inv_args = investigate.call_args
    # Positional args: (ticket_id, questions, plan_content, worktree_path)
    assert inv_args.args[1] == ["Q1", "Q2", "Q3"]

    # generate_plan and _evaluate_confidence each called exactly twice.
    assert gen.call_count == 2
    assert eval_fn.call_count == 2

    # The second generate_plan call carries investigation_findings.
    second_gen_kwargs = gen.call_args_list[1].kwargs
    assert second_gen_kwargs.get("investigation_findings") == "## Findings\nFound it."

    # Final evaluation surfaced in result is the SECOND one.
    assert result["evaluation"]["confidence_score"] == 92
    assert result["evaluation"]["passed"] is True


# ---------------------------------------------------------------------------
# Case 3: flag ON but questions=[] → no investigation.
# ---------------------------------------------------------------------------


def test_auto_investigate_skipped_when_questions_empty(agent, worktree, monkeypatch):
    monkeypatch.setenv("AUTO_INVESTIGATE_ENABLED", "1")

    gen = Mock(return_value="# Plan v1")
    eval_fn = Mock(return_value=_eval_failing(score=60, questions=[]))
    investigate = Mock(return_value="unused")
    monkeypatch.setattr(agent, "generate_plan", gen)
    monkeypatch.setattr(agent, "_evaluate_confidence", eval_fn)
    monkeypatch.setattr(agent, "_investigate_confidence_questions", investigate)

    result = agent.run("PROJ-3", worktree)

    investigate.assert_not_called()
    assert gen.call_count == 1
    assert eval_fn.call_count == 1
    # Single eval — final evaluation is unchanged.
    assert result["evaluation"]["questions"] == []
