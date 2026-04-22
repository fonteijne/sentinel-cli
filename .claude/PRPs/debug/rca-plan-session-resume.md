# Root Cause Analysis

**Issue**: `sentinel plan <ticket>` on the `has_feedback` state path crashes after `revise_plan()` with `ProcessError: Command failed with exit code 1`, surfaced to the user as a misleading "Jira API connectivity issues" message.
**Root Cause**: In the deployed code (HEAD `d053045`), `PlanGeneratorAgent.run()` does **not** reset `self.session_id` between `revise_plan()` (tool-enabled, `cwd=worktree`) and the follow-up `analyze_ticket()` call (`cwd=None`). The SDK therefore re-invokes the bundled Claude CLI with `resume=<previous-session>` under a different project context; the CLI fails to validate the resumed session and exits with code 1 during `ClaudeSDKClient.initialize()`.
**Severity**: High — blocks every `has_feedback` (MR-discussion) re-run for any ticket.
**Confidence**: High.

---

## Evidence Chain

**WHY**: The `sentinel plan DHLEXS_DHLEXC-356` CLI run prints "Jira API connectivity issues" and exits.
↓ **BECAUSE**: `analyze_ticket()` raised `RuntimeError`, and its catch-all `except Exception` formats the message as Jira-related.
  Evidence: `src/agents/plan_generator.py:177-188`
  ```python
  except Exception as e:
      error_msg = (
          f"Failed to analyze ticket {ticket_id} - Unexpected error.\n\n"
          f"Error: {str(e)}\n\n"
          f"This may indicate:\n"
          f"  - Jira API connectivity issues\n"
          f"  - Invalid ticket ID format\n"
          f"  - Missing ticket data\n\n"
          f"Please verify the ticket exists and is accessible."
      )
  ```
  Log proof: `2026-04-21 11:06:54,985 - src.agents.plan_generator - ERROR - Failed to analyze ticket DHLEXS_DHLEXC-356 - Unexpected error.`

**WHY**: `analyze_ticket()` raised.
↓ **BECAUSE**: `self.send_message(analysis_prompt, cwd=None)` propagated a `ProcessError` from `ClaudeSDKClient.__aenter__ → connect() → initialize()`.
  Evidence: Traceback in log
  ```
  File "/usr/local/lib/python3.11/site-packages/claude_agent_sdk/_internal/query.py", line 153, in initialize
      response = await self._send_control_request(...)
  claude_agent_sdk._errors.ProcessError: Command failed with exit code 1
  ```

**WHY**: The bundled Claude CLI subprocess exited with code 1 during `initialize()`.
↓ **BECAUSE**: The CLI started cleanly, never received a user query, and emitted `SessionEnd` 44 ms after startup without logging any `[ERROR]`/`[WARN]` line.
  Evidence: `logs/cli_stderr.log` timestamps between the two runs
  ```
  2026-04-21T11:06:54.928Z [DEBUG] [init] configureGlobalMTLS starting
  ...normal startup (0 plugins, 0 skills, LSP init ok)...
  2026-04-21T11:06:54.972Z [DEBUG] Getting matching hook commands for SessionEnd with query: other
  2026-04-21T11:06:54.973Z [DEBUG] LSP server manager shut down successfully
  ```
  No `Stream started` event ever appears for this run — the CLI aborted its `initialize` control-request without reaching the query stage.

**WHY**: The CLI aborted `initialize()` under normal boot conditions.
↓ **BECAUSE**: The SDK spawned the CLI with a `resume` pointing at a session that belonged to a different project/cwd. The first call ran with `cwd=/root/sentinel-workspaces/dhlexs_dhlexc/DHLEXS_DHLEXC-356` and produced `session_id=f2bdc900-6b53-4d99-accd-6f757bf21a39`; the second call ran with `cwd=None` (so the CLI inherits the Python subprocess cwd, `/app`) but the SDK still asked it to `resume=f2bdc900-...`. Claude Code scopes session storage per project, so the resume target is not valid in the new project context.
  Evidence: `logs/agent_diagnostics.jsonl`
  ```json
  {"event":"exec_complete","cwd":"/root/sentinel-workspaces/.../DHLEXS_DHLEXC-356","session_id":"f2bdc900-6b53-4d99-accd-6f757bf21a39"}
  {"event":"exec_start","cwd":null,"session_id":"f2bdc900-6b53-4d99-accd-6f757bf21a39","allowed_tools":["Read","Grep","Glob","Bash(git *)"]}
  ```
  And `src/agent_sdk_wrapper.py:313`:
  ```python
  "resume": session_id if session_id else None,
  ```

**WHY**: The second call resumed the previous session instead of starting fresh.
↓ **BECAUSE**: `BaseAgent._send_message_async` always sends `session_id=self.session_id` and updates `self.session_id` from the CLI response. `self.session_id` still held `f2bdc900-...` from the end of `revise_plan()` when `analyze_ticket()` started. `PlanGeneratorAgent.run()` in the deployed code path (`has_feedback` branch) does **not** null `self.session_id` between the two calls.
  Evidence: committed `HEAD` version of `run()` — `git show HEAD:src/agents/plan_generator.py` lines 1452–1467:
  ```python
  if state == "has_feedback":
      ...
      revision_result = self.revise_plan(...)
      plan_content = revision_result["revised_plan"]

      t0 = time.monotonic()
      logger.info(f"[RUN] Step 2b: Analyzing ticket (post-revision)...")
      analysis = self.analyze_ticket(ticket_id, worktree_path)   # ← no session reset
  ```
  Log proof: `[LLM] plan_generator: sending request (prompt=25635 chars, cwd=None, session=f2bdc900-6b53-4d99-accd-6f757bf21a39, max_turns=None)`

↓ **ROOT CAUSE**: The deployed `run()` omits `self.session_id = None; self.messages.clear()` between `revise_plan()` and `analyze_ticket()`. The resume-with-mismatched-cwd this produces crashes the Claude CLI subprocess, which `analyze_ticket()`'s catch-all exception handler then dresses up as a Jira connectivity problem.
  Evidence: `src/agents/plan_generator.py` in the working tree already contains the fix at lines 1505–1508 (as an uncommitted diff), proving the contributor had identified the same issue:
  ```python
  # Reset session — revise_plan used a tool-enabled session with a different cwd;
  # analyze_ticket is a standalone text-only call that must not resume it.
  self.session_id = None
  self.messages.clear()
  ```

### Misleading-message co-factor

Independent of the crash, the "Jira API connectivity issues" text is produced by a catch-all `except Exception` in `analyze_ticket()` that classifies *every* failure as Jira-related. Even after the session bug is fixed, any future SDK/LLM failure would still be misreported and would send future debuggers down the wrong rabbit hole (as it did here).

---

## Validation

| Test | Question | Result |
|------|----------|--------|
| Causation | Does resuming a session from a different cwd lead to CLI exit 1 → ProcessError → "Jira" message? | Yes — evidence chain is continuous: diagnostics log (resume sent) → stderr log (early SessionEnd) → Python traceback (ProcessError in initialize) → catch-all handler (Jira message). |
| Necessity | If `self.session_id` were reset before `analyze_ticket()`, would the symptom occur? | No. The SDK would spawn a fresh CLI session with `resume=None` and proceed normally (same as the `initial`/`update` paths, which work). |
| Sufficiency | Is the missing reset alone enough? | Yes for the crash. The misleading error text is a separate but compounding issue (observability bug), not a cause of the crash. |

### Git history

- **Introduced**: commit `d98a84b` ("feat: unify plan generator with state detection and confidence evaluation") — when the `has_feedback` branch was added that chains `revise_plan()` → `analyze_ticket()` using the same agent instance.
- **Latest touched**: `a50b4e0` ("feat: add --prompt CLI option and post-revision MR comments") — still does not reset the session.
- **HEAD**: `d053045` (merge of `feature/small-improvements`) — container is running this.
- **Type**: Original bug, never shipped a correct version for the `has_feedback` path.
- **Fix already drafted**: uncommitted working-tree change to `src/agents/plan_generator.py` adds the reset at lines 1505–1508.

### Alternative hypotheses ruled out

| Hypothesis | Why ruled out |
|------------|---------------|
| The planner agent is actually trying to hit the Jira API | Diagnostics show only two tool uses in the run, both on the plan file (`Read`/`Write` on `.agents/plans/DHLEXS_DHLEXC-356.md`). No Bash/WebFetch/curl tool calls. The "Jira" word only appears in the user-facing error template. |
| Ticket context is incomplete and drives the LLM to search externally | Irrelevant — the crash happens during SDK `initialize()`, before any LLM reasoning turn. |
| Custom-proxy auth failure | Stderr shows `[API:auth] OAuth token check complete` and a successful prior run (revise_plan) against the same proxy 3 minutes earlier. No auth errors logged for the second run. |
| `cwd=None` alone breaks the CLI | The later `integration_test` diagnostics in the same log file show multiple successful `cwd=null` invocations with `session_id=null` or a valid session. The crash only happens when `cwd=null` **and** `resume=<session-from-other-cwd>` are combined. |

---

## Fix Specification

### What Needs to Change

Two independent changes, in order of priority:

1. **Commit and deploy the already-drafted session reset** in `src/agents/plan_generator.py`. This prevents `analyze_ticket()` from inheriting a session created under a different `cwd`.
2. **Tighten `analyze_ticket()`'s exception handler** so only `JiraError` (or equivalent) produces the "Jira connectivity" message. Everything else should surface the real exception type and message.

### Implementation Guidance

**Change 1** — already present in the working tree; commit as-is:

```python
# src/agents/plan_generator.py — inside run(), has_feedback branch
revision_result = self.revise_plan(
    ticket_id, state_info["existing_plan"],
    state_info["discussions"], plan_path,
    user_prompt=user_prompt,
    cwd=str(worktree_path),
    ticket_context=ticket_context,
)
plan_content = revision_result["revised_plan"]

# NEW — required reset before the next SDK call
self.session_id = None
self.messages.clear()

analysis = self.analyze_ticket(ticket_id, worktree_path, ctx=ctx)
```

**Change 2** — narrow the catch-all in `analyze_ticket()`:

```python
# Current (problematic):
except Exception as e:
    error_msg = (
        f"Failed to analyze ticket {ticket_id} - Unexpected error.\n\n"
        f"Error: {str(e)}\n\n"
        f"This may indicate:\n"
        f"  - Jira API connectivity issues\n"
        ...
    )
    raise RuntimeError(error_msg) from e

# Required (fixed):
except JiraError as e:                       # or whatever your Jira exception class is
    raise RuntimeError(
        f"Failed to analyze ticket {ticket_id} - Jira issue: {e}"
    ) from e
except Exception as e:
    raise RuntimeError(
        f"Failed to analyze ticket {ticket_id} ({type(e).__name__}): {e}"
    ) from e
```

Consider applying the same lesson to the other `except Exception` sites in `plan_generator.py` (e.g. the confidence-report and investigation-report posters already do this correctly — use them as the pattern).

### Additional design note (non-blocking)

The `has_feedback` path re-runs `analyze_ticket()` after `revise_plan()` only so that `_evaluate_confidence(plan_content, analysis, ticket_id, project_key)` can pass `analysis["ticket_data"]`. Since `ctx.ticket_data` is already cached on the shared `TicketContextBuilder`, this second LLM round-trip could be skipped entirely by constructing a minimal `analysis` dict from `ctx` — saving ~15-30 s and one SDK invocation per has_feedback run. Optional follow-up.

### Files to Modify

- `src/agents/plan_generator.py:1505-1508` — add the session reset (already drafted in working tree).
- `src/agents/plan_generator.py:177-188` — narrow the catch-all exception handler.
- (Optional) `src/agents/plan_generator.py` has_feedback branch — skip the redundant `analyze_ticket()`.

### Verification

1. Re-run `sentinel plan DHLEXS_DHLEXC-356` with an unresolved MR discussion on the ticket. Expect `Step 2b: Analysis done` to complete without a ProcessError.
2. In `logs/agent_diagnostics.jsonl`, confirm the second `exec_start` event has `session_id: null` (not the revise-plan session id).
3. Induce a deliberate Claude CLI failure (e.g. point `ANTHROPIC_BASE_URL` at an unreachable host) and confirm the user-facing error now names the real exception, not "Jira API connectivity issues".
4. Smoke-test the `initial` and `update` re-entry paths to make sure the reset doesn't regress them (they already start with `session_id=None`, so this should be a no-op for them).
