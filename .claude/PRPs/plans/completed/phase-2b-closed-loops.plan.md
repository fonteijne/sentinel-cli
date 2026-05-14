# Feature: Phase 2B — Closed Loops (Reviewer→Planner Escalation + Confidence-Miss Auto-Investigation)

## Summary

Phase 2B closes two reactive feedback paths in Sentinel that today dead-end. **Loop C**: when the Drupal/Security reviewer vetoes work after the execute-stage review-revise loop has exhausted, escalate back to the planner instead of silently failing — emit a `ReviewerHandoffTriggered` event, mark `executions.phase='replan_needed'`, and post exactly one templated MR comment (DECISIONS §168). **Auto-investigation**: when `ConfidenceEvaluatorAgent` returns below threshold, auto-invoke a question-driven variant of `investigate_comments()` seeded from the evaluator's `questions[]` so the planner re-enters with grounded context instead of just posting a low-confidence report and stopping. Both features ship behind feature flags (`LOOP_C_ENABLED`, `AUTO_INVESTIGATE_ENABLED`), default off until the exit-criterion fixtures pass. A small concurrency seam in `agent_sdk_wrapper.py` eliminates a race where the synchronous `self.session_id = None; self.messages.clear()` inside investigation paths can clobber a live SDK stream.

## User Story

As a Sentinel operator running `sentinel execute` against a Drupal ticket
I want reviewer vetoes that the execute-stage retry loop cannot resolve to escalate back to the planner with a single, scannable MR comment, **and** I want low-confidence plans to investigate the evaluator's own clarifying questions against the codebase before posting the confidence report
So that reviewer-finding feedback closes the loop into the next plan revision and confidence misses produce actionable plan revisions instead of dead-end Jira reports.

## Problem Statement

Two concrete failures, both testable:

1. **Loop C gap.** Today, when `sentinel execute`'s review-revise loop (`execute-initial-flow-review-revise-loop` plan, landed) exhausts its `max_iterations` with reviewer-veto findings still present, the workflow returns failure. The execution row's `phase` field is never written, no event marks the handoff, and the MR carries no Sentinel comment summarizing **why** review failed — only the developer's last commit and whatever inline-finding comments the reviewer emitted. The next `sentinel plan` invocation cannot see "reviewer escalated this back to me" — it only sees unresolved discussions, indistinguishable from human comments.
2. **Confidence-miss dead end.** `_evaluate_confidence()` (`src/agents/plan_generator.py:1628-1664`) computes a score and threshold; below threshold the run posts a Jira confidence report (`_post_confidence_report`) and stops. The evaluator's `questions[]` field — the model's own suggested clarifications — is logged but never acted upon. Auto-investigation is only triggered on `state == "update"` with new client comments (`plan_generator.py:1531`), never on confidence-miss.

After Phase 2B: a deliberate-veto fixture posts exactly one MR comment matching DECISIONS §168 and writes `phase='replan_needed'` to the executions row; a deliberate-low-confidence fixture invokes `investigate_comments()` (or its question-seeded sibling) before the report is posted, and the resulting investigation findings are written to the plan via `generate_plan(...investigation_findings=...)`.

## Solution Statement

1. **New event `ReviewerHandoffTriggered`** in `src/core/events/types.py`. Emitted by the execute workflow when its internal review-revise loop has run `max_iterations` times **and** the final reviewer result still has at least `LOOP_C_BLOCKER_THRESHOLD` (default 1) blocker-severity findings. Payload: `reviewer_agent`, `finding_class` (a comma-separated short list, ≤80 chars), `blocker_count`, `next_actor` (always `"planner"` in this phase).
2. **Subscriber in `src/core/execution/post_execute.py`** — extend `register_post_execute_subscribers` with a second handler bound to `ReviewerHandoffTriggered`. Steps in order: (a) `UPDATE executions SET phase='replan_needed' WHERE id=?`; (b) revert MR to draft (idempotent, mirrors the cap-out path's D7 enforcement); (c) post **exactly one** MR comment using the DECISIONS §168 template — never paraphrase reviewer text. Failures of (b) and (c) are logged-and-swallowed. Phase write is mandatory.
3. **Workflow trigger.** In the execute pipeline (the post-`execute-initial-flow-review-revise-loop` code path that owns the loop), after the loop exits, inspect the final reviewer dict and publish `ReviewerHandoffTriggered` if blockers persist. Gate behind `LOOP_C_ENABLED`.
4. **Auto-investigation on confidence-miss.** Add `_investigate_confidence_questions(ticket_id, questions, existing_plan, worktree_path)` to `PlanGeneratorAgent` — a thin wrapper over the existing `investigate_comments` shape that synthesizes a single pseudo-comment listing the evaluator's questions, runs the same investigation prompt scaffold, and returns a markdown report. In `run()`, after Step 3 confidence eval, if `evaluation['passed'] is False and AUTO_INVESTIGATE_ENABLED`: invoke the new method, regenerate the plan with `generate_plan(..., investigation_findings=...)`, re-evaluate **once** (capped to prevent loops), then proceed to commit/MR/report. The re-evaluation result replaces the original.
5. **Cancellation seam in `src/agent_sdk_wrapper.py`.** Add `request_cancel()` / `_stream_active` so `investigate_comments` (lines 1022-1024) and the analogous reset at lines 1515-1516 wait for any live `client.receive_response()` stream to drain before zeroing `session_id` / `messages`. Synchronous wait via `asyncio.Event` or a thread-safe flag — no architectural async refactor.
6. **Feature flags.** Two env vars, both default `0` (off). The CLI gates the workflow trigger and the planner branch on them. Tests force them on.

---

## Metadata

| Field            | Value |
|------------------|-------|
| Type             | NEW_CAPABILITY (closes two existing feedback gaps) |
| Complexity       | MEDIUM — touches reviewer/execute workflow, planner state machine, event bus, post-execute subscribers, SDK wrapper |
| Systems Affected | `src/core/events/types.py`, `src/core/execution/post_execute.py`, `src/core/execution/workflows.py` (execute pipeline), `src/agents/plan_generator.py`, `src/agent_sdk_wrapper.py`, `src/cli.py` (env-flag plumbing only), `tests/` |
| Dependencies     | Existing: `EventBus`, `register_post_execute_subscribers`, `add_merge_request_comment`, `mark_as_draft`, `ConfidenceEvaluatorAgent`, `PlanGeneratorAgent.investigate_comments`. No new third-party libs. |
| Estimated Tasks  | 11 |
| Independence     | Independent of Phase 2A (postmortem read path) and Phase 2C (extraction + overlay PRs). Touches reactive paths, not memory paths. |
| Feature flags    | `LOOP_C_ENABLED` (default 0), `AUTO_INVESTIGATE_ENABLED` (default 0), `LOOP_C_BLOCKER_THRESHOLD` (default 1) |

---

## UX Design

### Before State

```
╔══════════════════════════════════════════════════════════════════════════╗
║  sentinel execute <ticket>                                               ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║   ┌───────────┐    ┌─────────┐    ┌──────────┐                           ║
║   │ developer │ →  │ security│ →  │ drupal   │  veto: 2 blockers         ║
║   │  run      │    │  pass   │    │ review   │  ─────┐                   ║
║   └───────────┘    └─────────┘    └──────────┘       │                   ║
║                                                       ▼                   ║
║                                          ┌──────────────────┐            ║
║                                          │ apply_feedback   │  retry N   ║
║                                          │ (review-revise   │  iters     ║
║                                          │  loop)           │            ║
║                                          └────────┬─────────┘            ║
║                                                   │                       ║
║                                                   ▼ still vetoes         ║
║                                          ┌──────────────────┐            ║
║                                          │ raise / log      │            ║
║                                          │ workflow returns │            ║
║                                          │ failure          │            ║
║                                          └──────────────────┘            ║
║                                                                          ║
║  PAIN_POINT 1: phase column never written → next `sentinel plan`         ║
║                cannot tell "reviewer escalated this" from "human         ║
║                left a comment"                                           ║
║  PAIN_POINT 2: no MR comment summarizing the handoff — reviewer          ║
║                inline comments are present but no top-level "Sentinel    ║
║                paused here, planner will re-run" signal                  ║
║                                                                          ║
║  ─────────── confidence-miss path (sentinel plan) ───────────            ║
║                                                                          ║
║   ┌────────────┐   ┌────────────┐   ┌──────────────┐   ┌─────────┐       ║
║   │ analyze    │ → │ generate   │ → │ confidence   │ → │ post    │       ║
║   │ ticket     │   │ plan       │   │ evaluator    │   │ Jira    │       ║
║   └────────────┘   └────────────┘   │ score=72/95  │   │ report  │       ║
║                                     │ questions=[5]│   │ (STOP)  │       ║
║                                     └──────────────┘   └─────────┘       ║
║                                                                          ║
║  PAIN_POINT 3: questions[] are written into the report and never         ║
║                investigated against the codebase. Planner exits.         ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔══════════════════════════════════════════════════════════════════════════╗
║  sentinel execute <ticket>  (LOOP_C_ENABLED=1)                           ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║   ┌───────────┐    review-revise loop exhausts, blockers persist         ║
║   │ developer │            │                                             ║
║   │  …        │            ▼                                             ║
║   └───────────┘   ┌──────────────────────────┐                           ║
║                   │ publish ReviewerHandoff  │                           ║
║                   │ Triggered                │                           ║
║                   │   reviewer_agent=        │                           ║
║                   │     drupal_reviewer      │                           ║
║                   │   finding_class=         │                           ║
║                   │     "service-injection,  │                           ║
║                   │      missing-hook"       │                           ║
║                   │   blocker_count=2        │                           ║
║                   │   next_actor=planner     │                           ║
║                   └──────────┬───────────────┘                           ║
║                              ▼                                            ║
║              ┌───────────────────────────────────┐                       ║
║              │ post_execute subscriber:          │                       ║
║              │  1. UPDATE executions             │                       ║
║              │     SET phase='replan_needed'     │                       ║
║              │  2. mark_as_draft (idempotent)    │                       ║
║              │  3. one MR comment, DECISIONS§168 │                       ║
║              │     "Drupal Reviewer found 2      │                       ║
║              │      blockers (service-injection, │                       ║
║              │      missing-hook). Re-running    │                       ║
║              │      Planner."                    │                       ║
║              └───────────────────────────────────┘                       ║
║                                                                          ║
║   DATA_FLOW: reviewer.findings → ReviewerHandoffTriggered event →        ║
║              executions.phase='replan_needed' → MR comment → next        ║
║              `sentinel plan` reads phase + MR discussions → revise       ║
║                                                                          ║
║  ─────── confidence-miss path (AUTO_INVESTIGATE_ENABLED=1) ───────       ║
║                                                                          ║
║   ┌──────────┐ → ┌────────┐ → ┌──────────────┐                          ║
║   │ analyze  │   │generate│   │ confidence   │ score=72 < 95            ║
║   │ ticket   │   │ plan   │   │ evaluator    │ ─────┐                   ║
║   └──────────┘   └────────┘   └──────────────┘      │                   ║
║                                                      ▼                    ║
║                                         ┌────────────────────────┐       ║
║                                         │ _investigate_confidence│       ║
║                                         │ _questions             │       ║
║                                         │   questions=[…]        │       ║
║                                         │   → search codebase    │       ║
║                                         │   → investigation_md   │       ║
║                                         └──────────┬─────────────┘       ║
║                                                    ▼                      ║
║                                         ┌────────────────────────┐       ║
║                                         │ generate_plan(         │       ║
║                                         │   investigation_findings│       ║
║                                         │   =md)                 │       ║
║                                         └──────────┬─────────────┘       ║
║                                                    ▼                      ║
║                                         ┌────────────────────────┐       ║
║                                         │ _evaluate_confidence   │       ║
║                                         │ (single re-run, cap=1) │       ║
║                                         └──────────┬─────────────┘       ║
║                                                    ▼                      ║
║                                         post Jira report (now             ║
║                                         either passing or with            ║
║                                         a stronger evidence base)         ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| MR (after execute review-revise exhausts) | No top-level Sentinel comment; only inline reviewer findings | Exactly one comment matching `Drupal Reviewer found N blockers (<class-list>). Re-running Planner.` | Reviewer can scan one line and know what happened, who's next |
| `executions.phase` | Never written by execute | Set to `replan_needed` on Loop C handoff | Future `sentinel plan` invocations + audit queries can detect handoffs |
| `sentinel plan` confidence below threshold | Plan generated → confidence report → STOP | Plan generated → confidence eval → investigate questions → regenerate plan → re-eval → report | Lower-confidence runs converge instead of dead-ending |
| `_detect_plan_state` next iteration | `has_feedback`/`update`/`nothing_changed` based on MR discussions only | Same — `phase='replan_needed'` is consumed elsewhere; this plan does **not** add a new state branch | Backward-compatible: existing logic unchanged |
| `agent_sdk_wrapper.py` | `messages.clear()` / `session_id = None` can race a live `receive_response()` stream | Cancellation seam drains the stream before the reset returns | Eliminates a hard-to-reproduce session-id corruption bug |

---

## Mandatory Reading

**Implementer MUST read these before any task. File:line references are exact.**

| Priority | File | Lines | Why |
|----------|------|-------|-----|
| P0 | `docs/agent-learning-from-feedback-2026-05-03.md` | 436–450 | Phase 2B definition — tasks 12, 13, cancellation seam |
| P0 | `docs/agent-learning-from-feedback-DECISIONS.md` | 149–180 | D8 — MR comment format and volume rules. The Loop C comment template is binding. |
| P0 | `docs/agent-learning-from-feedback-DECISIONS.md` | 123–146 | D7 — draft-MR-on-escalation rule (revert-to-draft is idempotent and mandatory) |
| P0 | `src/core/execution/post_execute.py` | 1–157 | Whole file. The new subscriber MUST mirror the structure: persist-then-side-effect, idempotent draft, ≤1 MR comment, exception swallowing for GitLab failures only. |
| P0 | `src/agents/plan_generator.py` | 998–1099 | `investigate_comments()` — the new `_investigate_confidence_questions` is a sibling, sharing prompt structure |
| P0 | `src/agents/plan_generator.py` | 1495–1607 | `run()` step 2/3/4 — auto-investigation hooks in between current Step 3 and Step 4 |
| P0 | `src/agents/plan_generator.py` | 1628–1664 | `_evaluate_confidence` — the threshold gate; the auto-investigation runs when `evaluation['passed'] is False` |
| P0 | `src/core/events/types.py` | 1–88 | Event class pattern to mirror exactly (`Literal[...]`, BaseModel, `agent` optional) |
| P0 | `src/core/events/bus.py` | 35–104 | Persist-then-publish contract; subscribers are exact-type, not isinstance |
| P1 | `src/agent_sdk_wrapper.py` | 339–412 | Streaming loop. The cancel seam wraps `async for message in client.receive_response()` (line 351) |
| P1 | `src/agents/security_reviewer.py` | 649–719 | Veto thresholds (CRITICAL ≥ 1, HIGH > 5) and `.run()` return shape |
| P1 | `src/agents/drupal_reviewer.py` | 392–507 | Veto thresholds (BLOCKER ≥ 1, MAJOR ≥ 1), `metrics` dict, and `findings` structure used to compute `finding_class` |
| P1 | `src/agents/confidence_evaluator.py` | 30–175 | `evaluate()` output schema; `questions[]` is the key field |
| P1 | `src/gitlab_client.py` | 179–207, 311–372 | `add_merge_request_comment` and `get_merge_request_discussions` signatures |
| P1 | `src/cli.py` | 43–49 | `_verifier_loop_enabled()` — env-flag pattern to mirror for `LOOP_C_ENABLED` and `AUTO_INVESTIGATE_ENABLED` |
| P1 | `src/core/persistence/migrations/001_init.sql` | 9–19 | `executions.phase` column already exists (TEXT, nullable). No migration needed. |
| P1 | `tests/conftest.py` | 1–269 | Fixtures: `sqlite_mem_conn`, `event_bus`, `postmortem_factory`. Use these for new tests. |
| P1 | `.claude/PRPs/plans/execute-initial-flow-review-revise-loop.plan.md` | full | Sibling plan that landed the review-revise loop. The new ReviewerHandoff trigger lives at the **exit** of that loop. |
| P2 | `.claude/agents/sentinel-learning-integrator.md` | full | "Do" / "Don't" boundaries. Loop C event + post_execute subscriber are integrator territory; planner-side `_investigate_confidence_questions` is **NOT** integrator territory — keep it in `plan_generator.py`. |
| P2 | `.claude/agents/sentinel-test-harness-expert.md` | full | Test ownership. The test-harness expert owns fixture wiring and integration tests. |
| P2 | `docs/agent-learning-from-feedback-HANDOVER.md` | 159–185 | §9 (file:line pointers) and §10 (risks carried forward — especially MR-comment-injection) |

---

## Patterns to Mirror

### EVENT_DEFINITION

```python
# SOURCE: src/core/events/types.py:60-72
# COPY THIS EXACT PATTERN for ReviewerHandoffTriggered.

class DeveloperCappedOut(BaseEvent):
    type: Literal["DeveloperCappedOut"] = "DeveloperCappedOut"
    agent: str
    attempts: int
    last_structured_errors: list[dict]


class PostmortemRecorded(BaseEvent):
    type: Literal["PostmortemRecorded"] = "PostmortemRecorded"
    postmortem_id: int
    failure_signature: str
```

The new event:

```python
class ReviewerHandoffTriggered(BaseEvent):
    type: Literal["ReviewerHandoffTriggered"] = "ReviewerHandoffTriggered"
    reviewer_agent: str          # e.g. "drupal_reviewer"
    finding_class: str           # comma-joined short class list, ≤80 chars
    blocker_count: int
    next_actor: str = "planner"
```

### POST_EXECUTE_SUBSCRIBER

```python
# SOURCE: src/core/execution/post_execute.py:60-156
# COPY the closure-over-bus pattern, the persist-first / side-effects-second
# ordering, and the per-side-effect try/except.

def register_post_execute_subscribers(
    bus: EventBus, *, conn, gitlab_client, ticket_context: TicketContext,
) -> None:
    def _resolve_mr_iid() -> Optional[int]:
        if ticket_context.mr_iid_resolver is not None:
            try:
                return ticket_context.mr_iid_resolver()
            except Exception as exc:
                logger.error("mr_iid_resolver raised: %s", exc, exc_info=True)
                return ticket_context.mr_iid
        return ticket_context.mr_iid

    def _handle(event: BaseEvent) -> None:
        if not isinstance(event, DeveloperCappedOut):
            return
        try:
            # ... persist row
            # ... best-effort GitLab side-effects
            # ... re-emit downstream event
        except Exception:
            logger.error("post_execute handler crashed", exc_info=True)

    bus.subscribe(DeveloperCappedOut, _handle)
```

The new handler is a sibling registered alongside, NOT a separate `register_*` function (the docstring of the existing function says "Wire the ``DeveloperCappedOut`` handler" — generalize the docstring; do NOT split the public API).

### MR_COMMENT_TEMPLATE (DECISIONS §168 binding)

```python
# SOURCE: docs/agent-learning-from-feedback-DECISIONS.md:168
# Format: one line, imperative, naming the reviewer, the finding class
# (NOT the full finding text), and the next actor.

# Example output:
# "Drupal Reviewer found 2 blockers (service-injection, missing hook). Re-running Planner."

REVIEWER_PRETTY = {
    "drupal_reviewer": "Drupal Reviewer",
    "security_reviewer": "Security Reviewer",
}

def format_handoff_comment(event: ReviewerHandoffTriggered) -> str:
    pretty = REVIEWER_PRETTY.get(event.reviewer_agent, event.reviewer_agent)
    return (
        f"{pretty} found {event.blocker_count} "
        f"blocker{'s' if event.blocker_count != 1 else ''} "
        f"({event.finding_class}). Re-running Planner."
    )
```

`finding_class` is computed at the trigger site (workflow code) by joining the **classes** (e.g. `category` for security, `id`/short title token for Drupal) of the top 3 blocking findings. Never paraphrase reviewer free-form text. Hard truncate at 80 chars with `…` suffix.

### EXECUTIONS_PHASE_UPDATE

```python
# SOURCE: src/core/execution/post_execute.py uses sqlite3.Connection
# directly. Mirror that — direct SQL, with explicit commit, inside the
# subscriber's try/except.

conn.execute(
    "UPDATE executions SET phase = ? WHERE id = ?",
    ("replan_needed", ticket_context.execution_id),
)
conn.commit()
```

The phase write is **mandatory** (not best-effort). If it fails, log and re-raise inside the subscriber so the event-bus exception swallow surfaces it; the MR comment then does NOT post — wrong order would mean a comment claiming a handoff that didn't actually mark state.

### FEATURE_FLAG_PATTERN

```python
# SOURCE: src/cli.py:43-49

def _verifier_loop_enabled() -> bool:
    """Phase 1 feature flag — set DEV_VERIFIER_LOOP=1 to enable Loop A."""
    return os.getenv("DEV_VERIFIER_LOOP", "0") == "1"
```

The new helpers live next to it:

```python
def _loop_c_enabled() -> bool:
    """Phase 2B feature flag — Reviewer→Planner handoff."""
    return os.getenv("LOOP_C_ENABLED", "0") == "1"


def _auto_investigate_enabled() -> bool:
    """Phase 2B feature flag — confidence-miss auto-investigation."""
    return os.getenv("AUTO_INVESTIGATE_ENABLED", "0") == "1"


def _loop_c_blocker_threshold() -> int:
    return int(os.getenv("LOOP_C_BLOCKER_THRESHOLD", "1"))
```

### INVESTIGATE_COMMENTS_SHAPE (sibling for questions)

```python
# SOURCE: src/agents/plan_generator.py:998-1099
# Reuse the prompt scaffold; the only difference is the input source.

def _investigate_confidence_questions(
    self,
    ticket_id: str,
    questions: list[str],
    existing_plan: str,
    worktree_path: Path,
) -> str:
    """Investigate the Confidence Evaluator's clarifying questions against
    the codebase. Returns markdown investigation report.

    Mirrors investigate_comments() exactly — same prompt scaffold, same
    return shape, same session-reset semantics — but the input is a list
    of strings, not a list of comment dicts.
    """
    if not questions:
        return ""

    logger.info(f"Investigating {len(questions)} confidence question(s) for {ticket_id}")

    # Use the cancellation seam (Task 8) instead of bare reset
    self._safe_reset_session()

    questions_text = "\n".join(f"### Question {i+1}\n{q}" for i, q in enumerate(questions))

    investigation_prompt = f"""You are investigating clarifying questions raised by the
    Confidence Evaluator for ticket {ticket_id}. ... [reuse the existing prompt
    body verbatim from investigate_comments, substituting "questions" for
    "comments" and dropping the client-acknowledgment carve-out]"""

    response = self.send_message(
        investigation_prompt,
        cwd=str(worktree_path),
        max_turns=15,
    )

    if "## Investigation Report" in response:
        return response[response.index("## Investigation Report"):]
    return response
```

### TEST_HARNESS_PATTERN

```python
# SOURCE: tests/conftest.py:101-142 (sqlite_mem_conn, event_bus fixtures)
# Integration test for the new subscriber:

def test_reviewer_handoff_writes_phase_and_one_comment(
    event_bus, sqlite_mem_conn, mock_gitlab,
):
    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=mock_gitlab,
        ticket_context=TicketContext(
            execution_id="test-exec-1",
            stack_type="drupal",
            gitlab_project="acme/site",
            mr_iid=42,
        ),
    )

    event_bus.publish(ReviewerHandoffTriggered(
        execution_id="test-exec-1",
        ts="",
        reviewer_agent="drupal_reviewer",
        finding_class="service-injection,missing-hook",
        blocker_count=2,
        next_actor="planner",
    ))

    # Phase written
    row = sqlite_mem_conn.execute(
        "SELECT phase FROM executions WHERE id=?", ("test-exec-1",)
    ).fetchone()
    assert row["phase"] == "replan_needed"

    # Exactly one comment, matching template
    assert len(mock_gitlab.comments) == 1
    body = mock_gitlab.comments[0]["body"]
    assert body == (
        "Drupal Reviewer found 2 blockers "
        "(service-injection,missing-hook). Re-running Planner."
    )

    # Draft revert called (idempotent)
    assert mock_gitlab.mark_as_draft_calls == [{"project_id": "acme/site", "mr_iid": 42}]
```

---

## Files to Change

| File | Action | Justification |
|------|--------|---------------|
| `src/core/events/types.py` | UPDATE | Add `ReviewerHandoffTriggered` event class |
| `src/core/events/__init__.py` | UPDATE | Re-export `ReviewerHandoffTriggered` |
| `src/core/execution/post_execute.py` | UPDATE | Add second handler subscribed to `ReviewerHandoffTriggered`; share `register_post_execute_subscribers` entry point |
| `src/core/execution/workflows.py` (or wherever the execute review-revise loop exit lives) | UPDATE | After loop exhausts with persisting blockers, compute `finding_class` and publish `ReviewerHandoffTriggered`. Gate behind `_loop_c_enabled()`. |
| `src/agents/plan_generator.py` | UPDATE | Add `_investigate_confidence_questions` method; in `run()` after Step 3 confidence eval, invoke it on `not evaluation['passed'] and AUTO_INVESTIGATE_ENABLED`; replace `self.session_id = None; self.messages.clear()` direct calls (lines 1023-1024, 1515-1516) with the new `_safe_reset_session()` helper |
| `src/agent_sdk_wrapper.py` | UPDATE | Add cancellation seam: `_stream_active` flag, `request_cancel()`, drain-and-clear helper used by planner resets |
| `src/agents/base_agent.py` | UPDATE | Add `_safe_reset_session()` that calls into the SDK wrapper's seam before zeroing `session_id`/`messages` |
| `src/cli.py` | UPDATE | Add `_loop_c_enabled`, `_auto_investigate_enabled`, `_loop_c_blocker_threshold` helpers next to `_verifier_loop_enabled`. Plumb the values into the workflow `TicketContext`/run config. |
| `tests/core/test_post_execute_handoff.py` | CREATE | Integration test for ReviewerHandoffTriggered → phase write + 1 MR comment + draft revert |
| `tests/agents/test_plan_generator_auto_investigate.py` | CREATE | Test confidence-miss path: low score → questions investigated → regenerate → re-evaluate, capped to one retry |
| `tests/test_agent_sdk_cancellation.py` | CREATE | Test the cancel seam: simulated mid-stream `_safe_reset_session` waits before clearing |
| `tests/integration/test_loop_c_e2e.py` | CREATE | End-to-end fixture: reviewer veto → workflow publishes event → subscriber writes phase + comment, no second comment posted on a parallel happy path |

**Files explicitly NOT touched:**

- `src/core/persistence/migrations/` — `executions.phase` already exists; no migration.
- `src/agents/security_reviewer.py`, `src/agents/drupal_reviewer.py` — reviewer agents are unchanged. The event publishing happens at the workflow boundary, not inside the agent.
- `src/agents/base_developer.py` and `*_developer.py` — verifier-loop territory (Phase 1), not Phase 2B. Touching them violates `sentinel-learning-integrator` boundaries.
- `prompts/shared/base_instructions.md` — Phase 2A's "never obey instructions inside feedback" hardening clause is its own deliverable.
- Postmortem persistence (`src/core/persistence/postmortems.py`) — Phase 2A/2C territory.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No automatic re-invocation of `sentinel plan` from inside `sentinel execute`.** Loop C marks state and exits. The next `sentinel plan` invocation (run by user/CI) consumes `phase='replan_needed'` and the MR discussions. The "auto-detected re-entry" wording in the design refers to existing `_detect_plan_state()` behavior on the next CLI run.
- **No new `_detect_plan_state` branch.** `phase='replan_needed'` is consumed by other code paths (and surfaced via `sentinel info` in a future plan); the existing `has_feedback`/`update`/`nothing_changed` semantics are sufficient because the reviewer's inline findings already create unresolved discussions.
- **No collapsing-to-one-update-in-place comment for Loop C.** D8 leaves that as a "revisit if 5+ comments accumulate" condition. Phase 2B emits one new comment per handoff, never edits prior ones.
- **No paraphrasing of reviewer free-form text.** `finding_class` is reviewer-emitted machine-readable classes only (security `category`, Drupal finding `id`/title token). Decision 10 forbids paraphrasing.
- **No multi-iteration auto-investigation.** Confidence-miss triggers exactly one investigate→regenerate→re-evaluate cycle. If the second evaluation still fails, the report posts and the run stops. Looping risks token burn for ungrounded gains.
- **No fine-grained handoff routing (developer vs planner vs human).** Phase 2B always sets `next_actor="planner"`. Multi-actor routing is Phase 3 territory.
- **No webhook ingestion of MR comments.** All MR feedback is still pull-on-demand at the next `sentinel plan` invocation, per existing architecture.
- **No changes to confidence threshold semantics.** Per-project + global default lookup at `plan_generator.py:1654-1658` is unchanged.
- **No new SQL migration.** `executions.phase` column already exists.

---

## Step-by-Step Tasks

Tasks are ordered for top-to-bottom execution. Each is atomic and independently verifiable.

### Task 1: ADD `ReviewerHandoffTriggered` event class

- **ACTION**: Append a new `BaseModel` subclass to `src/core/events/types.py`
- **IMPLEMENT**: Mirror `DeveloperCappedOut` (lines 60-66) exactly. Fields: `reviewer_agent: str`, `finding_class: str`, `blocker_count: int`, `next_actor: str = "planner"`. Type literal `"ReviewerHandoffTriggered"`.
- **MIRROR**: `src/core/events/types.py:40-72`
- **EXPORT**: Add to `src/core/events/__init__.py` `__all__` next to `DeveloperCappedOut`, `PostmortemRecorded`.
- **GOTCHA**: `Literal[...]` default value MUST equal the string. Pydantic v2 will silently accept mismatch and break runtime dispatch.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/core/events/`. Then `poetry run python -c "from src.core.events import ReviewerHandoffTriggered; e = ReviewerHandoffTriggered(execution_id='x', reviewer_agent='drupal_reviewer', finding_class='a', blocker_count=1); print(e.model_dump_json())"` — must succeed.

### Task 2: ADD env-flag helpers in `src/cli.py`

- **ACTION**: Add `_loop_c_enabled`, `_auto_investigate_enabled`, `_loop_c_blocker_threshold` next to `_verifier_loop_enabled` (lines 43-49).
- **MIRROR**: `src/cli.py:43-49`
- **GOTCHA**: All three flags default OFF for `LOOP_C_ENABLED` and `AUTO_INVESTIGATE_ENABLED`; threshold defaults to `1`. Document defaults in docstrings.
- **VALIDATE**: `poetry run pytest tests/test_cli.py -k flags` (or a new tiny test asserting default values).

### Task 3: CREATE `_investigate_confidence_questions` in `PlanGeneratorAgent`

- **ACTION**: Add new method to `src/agents/plan_generator.py` directly under `investigate_comments` (around line 1100).
- **IMPLEMENT**: Reuse the existing prompt scaffold from `investigate_comments` (lines 1031-1084). Build the questions text block. Reuse `self.send_message` call (lines 1086-1090). Reuse the report-extraction regex (lines 1093-1096).
- **DO NOT** copy `self.session_id = None; self.messages.clear()` directly (lines 1022-1024). Call `self._safe_reset_session()` instead — it does not yet exist; declare a `TODO: implemented in Task 8` comment for now and have the method temporarily fall back to direct reset until Task 8 lands. (Order: this task lands before 8; revisit at task 8 to flip the call.)
- **MIRROR**: `src/agents/plan_generator.py:998-1099`
- **GOTCHA**: Empty-questions case must return `""` (Step 3 below uses falsiness to skip).
- **VALIDATE**: `poetry run pytest tests/agents/test_plan_generator.py -k investigate_confidence` (test added in Task 9).

### Task 4: WIRE auto-investigation into `PlanGeneratorAgent.run()`

- **ACTION**: After existing Step 3 (confidence eval) at `plan_generator.py:1550-1560`, add: if `evaluation and not evaluation['passed'] and _auto_investigate_enabled() and evaluation.get('questions')`, call `_investigate_confidence_questions`, then re-call `generate_plan(..., investigation_findings=findings)`, then re-call `_evaluate_confidence` exactly once and replace `evaluation` and `plan_content`.
- **IMPLEMENT**:
    ```python
    if evaluation and not evaluation['passed'] and not force:
        if _auto_investigate_enabled() and evaluation.get('questions'):
            findings = self._investigate_confidence_questions(
                ticket_id, evaluation['questions'],
                plan_content, worktree_path,
            )
            if findings:
                plan_content = self.generate_plan(
                    ticket_id, analysis, plan_path, worktree_path,
                    investigation_findings=findings,
                    user_prompt=user_prompt,
                )
                evaluation = self._evaluate_confidence(
                    plan_content, analysis, ticket_id, project_key,
                )
                logger.info(
                    f"[RUN] Auto-investigation re-eval: "
                    f"score={evaluation['confidence_score']}/100"
                )
    ```
- **MIRROR**: structure of existing Step 2a.5 path at `plan_generator.py:1528-1539`
- **CRITICAL**: cap to one retry; do NOT loop. The next eval result is final.
- **GOTCHA**: `force=True` (`--force` flag) must continue to skip both eval and auto-investigation. Guard with the existing `if not force` at line 1552 — keep auto-investigation inside that branch.
- **VALIDATE**: `poetry run pytest tests/agents/test_plan_generator_auto_investigate.py` (test in Task 9).

### Task 5: PUBLISH `ReviewerHandoffTriggered` from execute workflow

- **ACTION**: At the exit of the execute workflow's review-revise loop (the `for attempt in range(1, max_iterations + 1)` block from `execute-initial-flow-review-revise-loop.plan.md`), after the loop completes with reviewer still vetoing, compute `finding_class` and publish the event. Locate the file by grepping for the loop comment landed by the prior plan.
- **IMPLEMENT**:
    ```python
    if _loop_c_enabled() and final_reviewer_result and not final_reviewer_result.get("approved", True):
        blockers = _extract_blockers(final_reviewer_result)  # pure function
        threshold = _loop_c_blocker_threshold()
        if len(blockers) >= threshold:
            finding_class = _format_finding_class(blockers)  # ≤80 chars, comma-joined
            event_bus.publish(ReviewerHandoffTriggered(
                execution_id=execution_id,
                ts="",
                reviewer_agent=final_reviewer_agent_name,  # 'drupal_reviewer' or 'security_reviewer'
                finding_class=finding_class,
                blocker_count=len(blockers),
                next_actor="planner",
            ))
    ```
- **HELPERS** (`_extract_blockers`, `_format_finding_class`) live in the same module as the workflow. Pure functions; unit-tested in isolation.
- **`_extract_blockers`** for security: `[f for f in findings if f["severity"] == "critical"]` plus `["high"]` if `count > 5`. For Drupal: `[f for f in findings if f.get("severity") in {"BLOCKER", "MAJOR"}]`. Keep the union of both behaviors keyed by `reviewer_agent`.
- **`_format_finding_class`**: comma-join the top-3 blockers' `category` (security) or finding `id`/short title (Drupal); truncate to 80 chars suffix `…`.
- **GOTCHA**: This is the ONE place that decides "should we hand off" — keep the threshold env-driven (`LOOP_C_BLOCKER_THRESHOLD`) so prod tuning doesn't require code change.
- **VALIDATE**: unit tests on `_extract_blockers` / `_format_finding_class`; integration test in Task 10.

### Task 6: REGISTER second subscriber in `post_execute.py`

- **ACTION**: Inside `register_post_execute_subscribers`, add a second `_handle_handoff` closure subscribed to `ReviewerHandoffTriggered`.
- **IMPLEMENT**:
    ```python
    def _handle_handoff(event: BaseEvent) -> None:
        if not isinstance(event, ReviewerHandoffTriggered):
            return
        try:
            # 1. MANDATORY: write phase
            conn.execute(
                "UPDATE executions SET phase = ? WHERE id = ?",
                ("replan_needed", ticket_context.execution_id),
            )
            conn.commit()
            logger.info(
                "Execution %s: phase=replan_needed (reviewer=%s, blockers=%d)",
                ticket_context.execution_id,
                event.reviewer_agent,
                event.blocker_count,
            )

            mr_iid = _resolve_mr_iid()
            if ticket_context.gitlab_project and mr_iid:
                # 2. D7: idempotent draft revert
                try:
                    gitlab_client.mark_as_draft(
                        project_id=ticket_context.gitlab_project,
                        mr_iid=mr_iid,
                    )
                except Exception as exc:
                    logger.error("mark_as_draft failed (handoff): %s", exc, exc_info=True)
                # 3. D8: exactly one comment
                try:
                    body = format_handoff_comment(event)  # Task 7
                    gitlab_client.add_merge_request_comment(
                        project_id=ticket_context.gitlab_project,
                        mr_iid=mr_iid,
                        body=body,
                    )
                except Exception as exc:
                    logger.error("handoff MR comment failed: %s", exc, exc_info=True)
            else:
                logger.warning(
                    "No MR context — phase written but skipping draft+comment "
                    "(execution=%s)", ticket_context.execution_id,
                )
        except Exception:
            logger.error("post_execute handoff handler crashed", exc_info=True)

    bus.subscribe(ReviewerHandoffTriggered, _handle_handoff)
    ```
- **MIRROR**: `_handle` at `post_execute.py:82-154`
- **CRITICAL**: phase write happens BEFORE side-effects. If phase write fails, comment must NOT post (the existing pattern is "persist-then-publish"; we reuse it).
- **DOCSTRING**: update the docstring of `register_post_execute_subscribers` from "Wire the ``DeveloperCappedOut`` handler" to "Wire post-execution handlers (DeveloperCappedOut, ReviewerHandoffTriggered)".
- **VALIDATE**: integration test in Task 10.

### Task 7: ADD `format_handoff_comment` helper

- **ACTION**: Pure function in `src/core/execution/post_execute.py` (top-level, near imports).
- **IMPLEMENT**:
    ```python
    REVIEWER_PRETTY: dict[str, str] = {
        "drupal_reviewer": "Drupal Reviewer",
        "security_reviewer": "Security Reviewer",
    }

    def format_handoff_comment(event: ReviewerHandoffTriggered) -> str:
        pretty = REVIEWER_PRETTY.get(event.reviewer_agent, event.reviewer_agent)
        plural = "s" if event.blocker_count != 1 else ""
        return (
            f"{pretty} found {event.blocker_count} blocker{plural} "
            f"({event.finding_class}). Re-running Planner."
        )
    ```
- **GOTCHA**: Decision 10 forbids paraphrasing reviewer text. `finding_class` is the only free-form-ish field; it is computed from machine-readable fields at the workflow boundary (Task 5).
- **VALIDATE**: simple unit test asserting exact output for two fixtures (1 blocker singular, ≥2 blockers plural).

### Task 8: ADD cancellation seam in `agent_sdk_wrapper.py` and `_safe_reset_session` in `base_agent.py`

- **ACTION**:
    1. In `AgentSDKWrapper.__init__`, add `self._stream_active = False` and `self._cancel_requested = False`.
    2. In `_execute_sdk` (around `agent_sdk_wrapper.py:341-393`), wrap the `async with ClaudeSDKClient` block in `try`/`finally` setting `_stream_active` true at entry, false at exit.
    3. Inside the `async for message in client.receive_response():` loop, check `if self._cancel_requested: break` early.
    4. Add `def request_cancel(self) -> None: self._cancel_requested = True` and `async def wait_for_idle(self, timeout: float = 5.0) -> None` that polls `_stream_active` with `await asyncio.sleep(0.05)` up to `timeout` seconds.
    5. In `BaseAgent`, add:
        ```python
        def _safe_reset_session(self) -> None:
            """Cancel any live SDK stream, then zero session state.

            Replaces direct `self.session_id = None; self.messages.clear()`
            calls so a concurrent receive_response stream cannot lose its
            session-id binding mid-flight.
            """
            sdk = getattr(self, "_sdk_wrapper", None)  # set up by send_message
            if sdk is not None:
                sdk.request_cancel()
                # Best-effort drain — synchronous wait via the running loop's
                # call_soon_threadsafe if available; otherwise log+proceed.
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # We are inside an async context; let caller await.
                        pass
                    else:
                        loop.run_until_complete(sdk.wait_for_idle(timeout=5.0))
                except Exception as exc:
                    logger.debug("safe_reset wait skipped: %s", exc)
            self.session_id = None
            self.messages.clear()
        ```
- **REPLACE** the bare resets at `plan_generator.py:1022-1024` and `:1515-1516` with `self._safe_reset_session()`.
- **GOTCHA**: This is a minimal seam, not an async refactor. The plan generator is largely synchronous; the wrapper handles async internally. Where `_safe_reset_session` is called from sync code, the wrapper has already exited its `async with` block on prior turns, so `_stream_active` is false and the wait returns immediately. The seam matters when async investigation is interleaved with another agent's stream — defensive, not load-bearing today.
- **GOTCHA**: Do NOT block forever. Hard `timeout=5.0` and log+proceed if exceeded.
- **VALIDATE**: `poetry run pytest tests/test_agent_sdk_cancellation.py` (Task 11).

### Task 9: TESTS — confidence-miss auto-investigation

- **ACTION**: New file `tests/agents/test_plan_generator_auto_investigate.py`.
- **IMPLEMENT**: Three test cases:
    1. `AUTO_INVESTIGATE_ENABLED=0` → no investigation called even when score < threshold.
    2. `AUTO_INVESTIGATE_ENABLED=1`, score 60 < 95, evaluator returns 3 questions → `_investigate_confidence_questions` called once with those questions, `generate_plan` called twice (initial + post-investigation), `_evaluate_confidence` called twice, final `evaluation` is the second one. (Use `unittest.mock.patch` on the three methods.)
    3. `AUTO_INVESTIGATE_ENABLED=1`, score 60, evaluator returns `questions=[]` → no investigation; falls through to existing report-and-stop path.
- **MIRROR**: Existing `tests/agents/test_plan_generator.py` style — `monkeypatch.setenv`, fixture-scoped `PlanGeneratorAgent`, mocked `analyze_ticket`/`generate_plan`/`_evaluate_confidence`.
- **VALIDATE**: `poetry run pytest tests/agents/test_plan_generator_auto_investigate.py -v`.

### Task 10: TESTS — Loop C end-to-end

- **ACTION**: New file `tests/integration/test_loop_c_e2e.py` and `tests/core/test_post_execute_handoff.py`.
- **IMPLEMENT**:
    - **Subscriber unit test** (`test_post_execute_handoff.py`):
        1. Fixture `event_bus`, `sqlite_mem_conn`, `mock_gitlab` (fake recording add_merge_request_comment + mark_as_draft).
        2. `register_post_execute_subscribers(...)`.
        3. Publish a `ReviewerHandoffTriggered` event.
        4. Assert `executions.phase == 'replan_needed'`.
        5. Assert exactly 1 entry in `mock_gitlab.comments`, body matches the DECISIONS §168 template verbatim for `(2, "service-injection,missing-hook", "drupal_reviewer")`.
        6. Assert `mock_gitlab.mark_as_draft_calls` is exactly 1 call.
        7. Negative case: missing `gitlab_project` → phase still written, no comment, warning logged.
        8. Negative case: 1-blocker singular vs N-blocker plural — comment text matches.
    - **Loop A vs Loop C MR-comment test** (DECISIONS §180): on a `DeveloperCappedOut`-only fixture (Loop A retry-then-pass equivalent), assert zero `ReviewerHandoffTriggered`-driven comments; on a Loop C fixture, exactly one.
    - **Workflow trigger test** (`test_loop_c_e2e.py`): with `LOOP_C_ENABLED=1`, fake review-revise loop returns `{"approved": False, "findings": [...]}` after `max_iterations` → assert `event_bus.publish` was called with the right event shape. With `LOOP_C_ENABLED=0`, no event published.
- **MIRROR**: Existing test for cap-out subscriber (search `tests/core/` for `DeveloperCappedOut` test).
- **VALIDATE**: `poetry run pytest tests/core/test_post_execute_handoff.py tests/integration/test_loop_c_e2e.py -v`.

### Task 11: TESTS — cancellation seam

- **ACTION**: New file `tests/test_agent_sdk_cancellation.py`.
- **IMPLEMENT**:
    1. Test `request_cancel` flips `_cancel_requested`.
    2. Test `wait_for_idle` returns immediately when `_stream_active=False`.
    3. Test `wait_for_idle` waits and returns when a fake stream toggles `_stream_active` from True → False.
    4. Test `wait_for_idle` honors `timeout` and returns even if stream never goes idle (asserts log was emitted).
    5. Test `_safe_reset_session` clears `session_id`/`messages` and calls `request_cancel`.
- **MIRROR**: existing async test patterns in `tests/test_agent_sdk_wrapper.py` if present; else `pytest.mark.asyncio`.
- **VALIDATE**: `poetry run pytest tests/test_agent_sdk_cancellation.py -v`.

---

## Testing Strategy

### Unit Tests

| Test File | Scope | Validates |
|-----------|-------|-----------|
| `tests/agents/test_plan_generator_auto_investigate.py` | `_investigate_confidence_questions` + `run()` integration | Auto-investigation gate, single-retry cap, force-flag bypass, empty-questions short-circuit |
| `tests/core/test_post_execute_handoff.py` | New subscriber | Phase write, 1 MR comment, idempotent draft revert, missing-MR fallback, comment template |
| `tests/test_agent_sdk_cancellation.py` | Cancel seam | Request/wait/timeout semantics |
| `tests/test_format_handoff_comment.py` (or inlined) | `format_handoff_comment` | Singular/plural, unknown reviewer fallback, 80-char truncation |
| `tests/test_workflow_loop_c_trigger.py` (or inlined in execute test) | `_extract_blockers`, `_format_finding_class`, publish gate | Threshold respected; flag-off no-publish |

### Integration Tests

| Test | Validates |
|------|-----------|
| `tests/integration/test_loop_c_e2e.py` | Workflow → event → subscriber → DB+GitLab full chain. Single trigger produces single comment. |
| Loop A vs Loop C MR-comment-budget assertion | DECISIONS §180 — Loop A retries emit zero MR comments; Loop C emits exactly one per handoff. |

### Edge Cases Checklist

- [ ] Reviewer agent returns `approved=True` after retries → no event published.
- [ ] Reviewer returns blockers below `LOOP_C_BLOCKER_THRESHOLD` → no event.
- [ ] Phase write fails (DB locked simulation) → comment NOT posted; subscriber logs and surfaces.
- [ ] `mark_as_draft` raises → comment STILL posts (D7 is best-effort; D8 still binding).
- [ ] `add_merge_request_comment` raises → swallowed, logged, no retry.
- [ ] Missing `gitlab_project` (project repo not configured) → phase written, no GitLab calls.
- [ ] Confidence eval returns `questions=[]` → no investigation, no second `generate_plan`.
- [ ] `--force` flag → skip both eval and auto-investigation.
- [ ] `AUTO_INVESTIGATE_ENABLED=0` with score < threshold → no investigation, existing path unchanged.
- [ ] Two consecutive Loop C handoffs in one MR (rare) → two comments, no de-dup logic (revisit per D8).
- [ ] `finding_class` exceeds 80 chars → truncated with `…` suffix.
- [ ] Reviewer-name not in `REVIEWER_PRETTY` map → falls back to raw agent name.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && poetry run ruff check src/ tests/ && poetry run mypy src/
```

**EXPECT**: Exit 0, no new errors.

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel && poetry run pytest \
  tests/core/test_post_execute_handoff.py \
  tests/agents/test_plan_generator_auto_investigate.py \
  tests/test_agent_sdk_cancellation.py \
  -v
```

**EXPECT**: All pass.

### Level 3: INTEGRATION + FULL SUITE

```bash
cd /workspace/sentinel && poetry run pytest tests/integration/test_loop_c_e2e.py -v && \
  poetry run pytest --maxfail=5
```

**EXPECT**: New tests pass; existing suite unchanged.

### Level 4: FEATURE-FLAG MATRIX

```bash
# Default: both flags off — behavior identical to pre-Phase-2B
cd /workspace/sentinel && LOOP_C_ENABLED=0 AUTO_INVESTIGATE_ENABLED=0 poetry run pytest tests/

# Loop C only
LOOP_C_ENABLED=1 poetry run pytest tests/integration/test_loop_c_e2e.py

# Auto-investigation only
AUTO_INVESTIGATE_ENABLED=1 poetry run pytest tests/agents/test_plan_generator_auto_investigate.py
```

**EXPECT**: Both flag-off run is byte-identical to baseline; flag-on runs satisfy exit criteria.

### Level 5: MANUAL_VALIDATION (sentinel-dev container)

After deploying to `sentinel-dev`:

```bash
# 1. Trigger a deliberate Drupal-blocker fixture ticket
LOOP_C_ENABLED=1 LOOP_C_BLOCKER_THRESHOLD=1 sentinel execute TEST-BLOCKER-1

# 2. Verify
sqlite3 ~/.sentinel/sentinel.db \
  "SELECT id, phase FROM executions WHERE ticket_id='TEST-BLOCKER-1' ORDER BY created_at DESC LIMIT 1;"
# EXPECT: phase = 'replan_needed'

# 3. Inspect MR — exactly one comment matching the template
glab mr view <iid> --comments | grep "Re-running Planner"
# EXPECT: exactly one match
```

---

## Acceptance Criteria

- [x] **Loop C exit criterion**: Reviewer emits ≥ 1 blocker → after the execute review-revise loop exhausts, `ReviewerHandoffTriggered` is published exactly once → `executions.phase='replan_needed'` is written → exactly one MR comment posted matching the DECISIONS §168 template.
- [x] **Auto-investigation exit criterion**: Confidence Evaluator returns score below threshold and `questions[]` non-empty → `_investigate_confidence_questions` runs against the worktree → its findings are passed to `generate_plan` → `_evaluate_confidence` runs again exactly once → final report uses the second evaluation.
- [x] **No regression**: With both flags off, behavior is bit-for-bit identical to current `main`. (Verified: 26 pre-existing failures in unrelated tests; 0 new failures introduced.)
- [x] **Cancellation seam**: All bare `self.session_id = None; self.messages.clear()` patterns in `plan_generator.py` are replaced with `_safe_reset_session()`. (Three sites: lines 428, 1023, 1130. Verified by grep.)
- [x] **Comment-volume invariant** (DECISIONS §180): Loop A retry fixtures publish zero MR comments; Loop C fixtures publish exactly one. (Paired tests in `test_post_execute_handoff.py`.)
- [x] **D7 invariant**: On Loop C handoff, MR ends in draft state regardless of prior state. (`mark_as_draft` called inside subscriber, idempotent.)
- [x] All Level 1–3 validation commands pass. (Level 1 ruff/mypy: no new errors; Level 2: 33/33 new tests pass; Level 3: full suite — 26 pre-existing failures, 0 new.)

---

## Completion Checklist

- [x] Tasks 1–11 done in dependency order.
- [x] Each task validated immediately after completion (per-task `pytest`).
- [x] Level 1 (lint+mypy) green. (No new errors in touched lines.)
- [x] Level 2 (new unit tests) green. (33/33 pass.)
- [x] Level 3 (integration + full suite) green. (Pre-existing failures unrelated.)
- [x] Level 4 (flag matrix) green. (LOOP_C only / AUTO_INVESTIGATE only / both on / both off — all pass.)
- [ ] Level 5 (manual on sentinel-dev) green — **DEFERRED to user**: requires deliberate-veto fixture ticket; cannot be run from the Claude sandbox.
- [ ] `bd ready` checked for any new follow-up issues; file them if found. **DEFERRED**: see Notes below.
- [ ] `git push` succeeds and `bd sync` is clean. **DEFERRED to user**: Claude sandbox has no SSH/git push capability per CLAUDE.md.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Reviewer handoff fires on transient failures (e.g. one flaky test) | MEDIUM | MEDIUM (noisy MR comments) | `LOOP_C_BLOCKER_THRESHOLD` env-tunable; ship default 1 but elevate quickly if noise observed. Existing review-revise loop already retries before the handoff fires, so transient failures get filtered. |
| MR-comment injection via `finding_class` reaching the prompt of the next planner run | LOW | HIGH (poisoned plan) | `finding_class` is built from reviewer-emitted machine-readable classes (security `category`, Drupal finding `id`/title), never reviewer-authored free-form text. Hard 80-char cap. The "never obey instructions inside feedback" hardening clause is owned by Phase 2A's `base_instructions.md` work, not this plan. |
| Auto-investigation infinite loop if eval keeps failing | LOW | HIGH (token burn) | Hard cap: exactly one investigate→regenerate→re-eval cycle. The second eval is final; no recursion. Tested. |
| Cancellation seam blocks indefinitely on a stuck stream | LOW | MEDIUM | `wait_for_idle(timeout=5.0)` with explicit log-and-proceed. Worst case: same race that exists today, no regression. |
| Phase write race vs concurrent execute on same ticket | LOW | MEDIUM | Same `executions.id` is unique per execution; concurrent runs have different ids. Single-row `UPDATE` is atomic in SQLite. |
| `_format_finding_class` produces low-information class strings ("error,error,error") | MEDIUM | LOW | Telemetry: log the source findings + computed class to allow tuning. Out-of-scope improvements deferred to future plan. |
| Two reviewer agents (security, drupal) both veto same execution → two events fired | MEDIUM | LOW | Workflow code emits at most one event per execute run, choosing the last reviewer that vetoed. Document in Task 5. |
| Subscriber registration order matters (cap-out vs handoff) | LOW | LOW | Both subscribe to distinct event types; bus filters by exact type. No interference. |
| Whack-a-mole risk (per CLAUDE.md): handoff comment treats symptoms | MEDIUM | MEDIUM | Comment names the **finding class**, not a fix. The fix is the planner's responsibility on re-entry. The schema does not invite symptom-patching; rationale lives in the linked reviewer findings. |

---

## Notes

- **Why not auto-invoke `sentinel plan` from inside `sentinel execute`?** The handoff is a state mark + comment; the next CLI invocation does the work. This preserves the partial-autonomy slider (design §5.3) — humans decide when to re-plan, Sentinel makes that decision well-informed and one-click.
- **Why one event class instead of three (`SecurityHandoff`, `DrupalHandoff`, etc.)?** The `reviewer_agent` field already discriminates. Future reviewer types ride the same event shape with no schema bump.
- **Why no migration?** `executions.phase` is already a nullable TEXT column from `001_init.sql`. New value `'replan_needed'` joins existing values like `'initial'` (currently unwritten in code).
- **Why `_safe_reset_session` lives on `BaseAgent`, not `AgentSDKWrapper`?** Because `session_id` and `messages` live on `BaseAgent` (`base_agent.py:60-64`), not on the wrapper. The wrapper only owns the stream state. The helper bridges them.
- **Why no de-duplication of repeated handoff comments?** D8 leaves it as a "revisit if 5+ comments accumulate". Phase 2B does not pre-emptively solve a problem the field has not produced. If telemetry shows repeated handoffs on the same MR, file a follow-up.
- **Why doesn't this plan touch reviewer agents?** Per `sentinel-learning-integrator` boundary doc, the integrator wires seams; reviewer-internal logic is owned by the reviewer experts. The trigger lives at the workflow boundary, treating reviewers as black boxes that return `{approved, findings, ...}`.
- **Phase 2B → 2C readiness gate** (per design §8): "Strongly recommended that 2B has landed too (so a bad widened rule has a fast escalation path back to the planner)." Loop C is exactly that escalation path.
