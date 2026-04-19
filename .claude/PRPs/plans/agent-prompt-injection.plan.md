# Feature: `--prompt` Option for Agent CLI Commands

## Summary

Add a `--prompt` CLI option to all agent-facing Sentinel commands (`plan`, `debrief`, `execute`) that injects an ad-hoc operator instruction into the agent's user prompt before the SDK call. This enables runtime steering of agent behavior without editing plan files or Jira tickets. The injection is ephemeral (not persisted), clearly delimited as an "Operator Instruction," and deliberately excluded from the security reviewer.

## User Story

As a Sentinel operator
I want to inject ad-hoc instructions into agent sessions via `--prompt`
So that I can steer execution, provide missing context, or debug agent behavior without modifying persistent artifacts

## Problem Statement

Agent prompts are built entirely from internal logic — ticket data, plan files, system prompts. There is no mechanism to inject an ad-hoc instruction at runtime. The only alternatives (editing plans, updating Jira) are slow, pollute artifacts, and can't be done ephemerally.

## Solution Statement

Add `--prompt` (long-form only — `-p` is taken by `--project`) to the `plan`, `debrief`, and `execute` commands. The string is appended to the user prompt as a clearly delimited `## Operator Instruction` block before the SDK call. A shared helper `_append_operator_prompt()` on `BaseAgent` handles injection consistently. Security reviewer is excluded.

## Metadata

| Field            | Value                                                        |
| ---------------- | ------------------------------------------------------------ |
| Type             | ENHANCEMENT                                                  |
| Complexity       | LOW                                                          |
| Systems Affected | CLI (`cli.py`), base agent, plan generator, debrief, developer agents |
| Dependencies     | None (uses only existing libraries)                          |
| Estimated Tasks  | 6                                                            |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════╗
║                           BEFORE STATE                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║   $ sentinel plan ACME-123                                          ║
║   $ sentinel debrief ACME-123                                       ║
║   $ sentinel execute ACME-123                                       ║
║   $ sentinel execute ACME-123 --revise                              ║
║                                                                     ║
║   No way to steer what the agent focuses on.                        ║
║   Must edit plan files, Jira tickets, or system prompts.            ║
║                                                                     ║
║   DATA FLOW:                                                        ║
║   CLI args → agent.run() → internal prompt build → SDK call         ║
║              (no external injection point)                           ║
║                                                                     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════╗
║                            AFTER STATE                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║   $ sentinel plan ACME-123 --prompt "Focus on the API layer"        ║
║   $ sentinel debrief ACME-123 --prompt "Ask about auth reqs"        ║
║   $ sentinel execute ACME-123 --prompt "Implement task 1 only"      ║
║   $ sentinel execute ACME-123 --revise --prompt "Extract to svc"    ║
║                                                                     ║
║   Operator instruction is injected ephemerally into agent prompt.   ║
║   Visible in SDK logs. Not persisted to plan/Jira.                  ║
║                                                                     ║
║   DATA FLOW:                                                        ║
║   CLI args ──→ agent.run(user_prompt=...) ──→ prompt build          ║
║                                                  │                  ║
║                                                  ▼                  ║
║                                         _append_operator_prompt()   ║
║                                                  │                  ║
║                                                  ▼                  ║
║                                            SDK call (prompt         ║
║                                            now includes             ║
║                                            ## Operator Instruction) ║
║                                                                     ║
║   Security reviewer: UNCHANGED (no injection)                       ║
║                                                                     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| `sentinel plan` | No steering | `--prompt "..."` steers plan generation | Can focus planner on specific areas |
| `sentinel debrief` | No steering | `--prompt "..."` steers debrief | Can direct functional questions |
| `sentinel execute` | No steering | `--prompt "..."` steers developer agent | Can limit scope, provide context |
| `sentinel execute --revise` | No steering | `--prompt "..."` steers revision | Can guide revision approach |
| Security reviewer | Autonomous | Unchanged | Remains autonomous (by design) |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `src/agents/base_agent.py` | 129-201 | `send_message()` and `_send_message_async()` — the two prompt dispatch paths to understand |
| P0 | `src/agents/base_agent.py` | 229-242 | `run()` abstract method signature with `**kwargs` |
| P0 | `src/cli.py` | 63-81 | `plan` command option definitions and signature |
| P0 | `src/cli.py` | 176-183 | `debrief` command option definitions and signature |
| P0 | `src/cli.py` | 314-343 | `execute` command option definitions and signature |
| P1 | `src/agents/plan_generator.py` | 1373-1378 | `run()` signature — accepts `**kwargs` |
| P1 | `src/agents/plan_generator.py` | 472-541 | `generate_plan()` prompt construction and `send_message()` call |
| P1 | `src/agents/functional_debrief.py` | 40-46 | `run()` signature — accepts `**kwargs` |
| P1 | `src/agents/functional_debrief.py` | 334-356 | `_generate_debrief()` prompt build and `send_message()` call |
| P1 | `src/agents/functional_debrief.py` | 417-448 | `_generate_followup()` prompt build and `send_message()` call |
| P1 | `src/agents/base_developer.py` | 629-634 | `run()` signature — accepts `**kwargs` |
| P1 | `src/agents/base_developer.py` | 329-339 | `implement_feature()` prompt build and direct `agent_sdk.execute_with_tools()` call |
| P1 | `src/agents/base_developer.py` | 749-754 | `run_revision()` signature — accepts `**kwargs` |
| P2 | `src/cli.py` | 126 | `plan_agent.run()` call site |
| P2 | `src/cli.py` | 219 | `agent.run()` call site (debrief) |
| P2 | `src/cli.py` | 569 | `developer.run()` call site |
| P2 | `src/cli.py` | 404 | `developer.run_revision()` call site |

---

## Patterns to Mirror

**CLICK OPTION PATTERN:**
```python
# SOURCE: src/cli.py:65-68
# COPY THIS PATTERN for --prompt (long-form only, no short flag):
@click.option(
    "--project",
    "-p",
    help="Project key (e.g., ACME). If not provided, extracted from ticket ID.",
)
```

**EXPLICIT PARAMETER PATTERN:**
```python
# SOURCE: src/agents/functional_debrief.py:40-46
# All agent run() methods use explicit named params + **kwargs for forward compat:
def run(  # type: ignore[override]
    self,
    ticket_id: str,
    project: str | None = None,
    worktree_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
# user_prompt should be added as an explicit named parameter, not hidden in **kwargs
```

**PROMPT SECTION INJECTION PATTERN:**
```python
# SOURCE: src/agents/plan_generator.py:461-468
# Existing pattern: conditional sections appended to prompt strings
findings_section = ""
if investigation_findings:
    findings_section = (
        "## Pre-Research Findings\n\n"
        "The following findings were verified by searching the codebase...\n"
        f"{investigation_findings}\n\n"
    )
```

**SEND_MESSAGE DISPATCH PATTERN:**
```python
# SOURCE: src/agents/base_agent.py:180-201
# Agents use send_message() which calls _send_message_async()
def send_message(
    self, content: str, role: str = "user", cwd: str | None = None,
    max_turns: int | None = None,
) -> str:
    result = asyncio.run(self._send_message_async(content, role, cwd, max_turns=max_turns))
    return result
```

**DIRECT SDK CALL PATTERN:**
```python
# SOURCE: src/agents/base_developer.py:334-339
# BaseDeveloperAgent.implement_feature() bypasses send_message():
result = asyncio.run(self.agent_sdk.execute_with_tools(
    prompt=tdd_prompt,
    session_id=None,
    system_prompt=self.system_prompt,
    cwd=str(worktree_path),
))
```

---

## Files to Change

| File | Action | Justification |
|------|--------|---------------|
| `src/agents/base_agent.py` | UPDATE | Add `_append_operator_prompt()` helper method |
| `src/agents/plan_generator.py` | UPDATE | Accept `user_prompt` in `run()`, thread to `generate_plan()` and `revise_plan()` |
| `src/agents/functional_debrief.py` | UPDATE | Accept `user_prompt` in `run()`, thread to `_generate_debrief()` and `_generate_followup()` |
| `src/agents/base_developer.py` | UPDATE | Accept `user_prompt` in `run()` and `run_revision()`, inject into `implement_feature()` |
| `src/cli.py` | UPDATE | Add `--prompt` option to `plan`, `debrief`, `execute`; pass to agent calls |

---

## NOT Building (Scope Limits)

- **Short flag `-p`** — Already taken by `--project` on all three commands. Long-form `--prompt` only. Shell aliases cover brevity.
- **Prompt from file (`--prompt-file`)** — YAGNI. Shell provides: `--prompt "$(cat instructions.txt)"`.
- **Multi-turn injection** — Prompt is injected once at session start. Mid-session steering is a different feature.
- **Security reviewer injection** — Deliberately excluded. Allowing `--prompt` on security review would undermine its integrity.
- **Prompt logging to MR/Jira** — The operator instruction appears in Agent SDK logs (it's part of the user prompt). No additional logging.
- **Confidence evaluation injection** — Confidence should remain objective; `--prompt` is not injected there.

---

## Step-by-Step Tasks

### Task 1: ADD `_append_operator_prompt()` to `BaseAgent` in `src/agents/base_agent.py`

- **ACTION**: Add a helper method to `BaseAgent` that appends the operator instruction block to a prompt string
- **WHERE**: After `get_history()` (line 227), before the `run()` abstract method (line 229)
- **IMPLEMENT**:
  ```python
  def _append_operator_prompt(self, prompt: str, user_prompt: str | None) -> str:
      """Append operator instruction to a prompt if provided.

      Args:
          prompt: The base prompt string
          user_prompt: Optional operator instruction to inject

      Returns:
          Prompt with operator instruction appended, or original prompt if no user_prompt
      """
      if not user_prompt:
          return prompt
      return f"{prompt}\n\n---\n## Operator Instruction\n\n{user_prompt}\n"
  ```
- **MIRROR**: Follows the same conditional-section pattern as `plan_generator.py:461-468`
- **GOTCHA**: Method lives on `BaseAgent`, not on subclasses — all agents get it for free
- **VALIDATE**: `python -c "from src.agents.base_agent import BaseAgent"` — import succeeds

### Task 2: Thread `user_prompt` through `PlanGeneratorAgent` in `src/agents/plan_generator.py`

- **ACTION**: Accept `user_prompt` as an explicit named parameter in `run()`, forward to `generate_plan()` and `revise_plan()`
- **IMPLEMENT**:
  1. In `run()` (line 1373): Add `user_prompt` as an explicit named parameter:
     ```python
     def run(  # type: ignore[override]
         self,
         ticket_id: str,
         worktree_path: Path,
         force: bool = False,
         user_prompt: str | None = None,
         **kwargs: Any,
     ) -> Dict[str, Any]:
     ```
  2. Forward `user_prompt` to every `generate_plan()` call and every `revise_plan()` call within `run()`. Search the method body for all call sites — there may be multiple (initial generation, revision, re-generation after feedback).
  3. In `generate_plan()` (line 409): Add `user_prompt: str | None = None` parameter. Apply injection to `plan_prompt` before `self.send_message()` (line 541):
     ```python
     plan_prompt = self._append_operator_prompt(plan_prompt, user_prompt)
     response = self.send_message(plan_prompt, cwd=worktree_cwd, max_turns=30)
     ```
  4. In `revise_plan()`: Add `user_prompt: str | None = None` parameter. Apply injection to the revision prompt before `send_message()`.
- **MIRROR**: `plan_generator.py:461-468` — conditional prompt section pattern
- **GOTCHA**: Do NOT inject into confidence evaluation prompts — confidence should remain objective
- **GOTCHA**: `run()` has multiple code paths (initial, has_feedback, update). Trace ALL paths that call `generate_plan()` or `revise_plan()` and ensure `user_prompt` is forwarded to each one.
- **VALIDATE**: `python -c "from src.agents.plan_generator import PlanGeneratorAgent"` — import succeeds

### Task 3: Thread `user_prompt` through `FunctionalDebriefAgent` in `src/agents/functional_debrief.py`

- **ACTION**: Accept `user_prompt` in `run()`, forward to `_generate_debrief()` and `_generate_followup()`
- **IMPLEMENT**:
  1. In `run()` (line 40): Add `user_prompt` as an explicit named parameter:
     ```python
     def run(  # type: ignore[override]
         self,
         ticket_id: str,
         project: str | None = None,
         worktree_path: str | Path | None = None,
         user_prompt: str | None = None,
         **kwargs: Any,
     ) -> Dict[str, Any]:
     ```
  2. Forward `user_prompt` to all `_generate_debrief()` calls (line 82) and `_generate_followup()` calls (lines 111, 154)
  3. In `_generate_debrief()` (line 293): Add `user_prompt: str | None = None` parameter. Apply injection to `prompt` before `self.send_message()` (line 356):
     ```python
     prompt = self._append_operator_prompt(prompt, user_prompt)
     response = self.send_message(prompt, cwd=self._cwd)
     ```
  4. In `_generate_followup()` (line 373): Same pattern — add parameter, inject before `self.send_message()` (line 448)
- **MIRROR**: `functional_debrief.py:334-356` — prompt build → send_message pattern
- **VALIDATE**: `python -c "from src.agents.functional_debrief import FunctionalDebriefAgent"` — import succeeds

### Task 4: Thread `user_prompt` through `BaseDeveloperAgent` in `src/agents/base_developer.py`

- **ACTION**: Accept `user_prompt` in `run()` and `run_revision()`, inject into `implement_feature()`
- **IMPLEMENT**:
  1. In `run()` (line 629): Add `user_prompt` as an explicit named parameter:
     ```python
     def run(  # type: ignore[override]
         self,
         plan_file: str | Path,
         worktree_path: str | Path,
         user_prompt: str | None = None,
         **kwargs: Any,
     ) -> Dict[str, Any]:
     ```
  2. Forward `user_prompt` to `implement_feature()` calls (line 673 and line 724 for config fix)
  3. In `implement_feature()` (line 286): Add `user_prompt: str | None = None` parameter. Apply injection to `tdd_prompt` before the direct SDK call (line 334):
     ```python
     tdd_prompt = self._build_tdd_prompt(task, context, worktree_path)
     tdd_prompt = self._append_operator_prompt(tdd_prompt, user_prompt)
     result = asyncio.run(self.agent_sdk.execute_with_tools(
         prompt=tdd_prompt,
         ...
     ))
     ```
  4. In `run_revision()` (line 749): Add `user_prompt` as an explicit named parameter:
     ```python
     def run_revision(  # type: ignore[override]
         self,
         ticket_id: str,
         worktree_path: str | Path,
         user_prompt: str | None = None,
         **kwargs: Any,
     ) -> Dict[str, Any]:
     ```
     Forward `user_prompt` to `implement_feature()` calls within the revision flow
- **MIRROR**: `base_developer.py:329-339` — prompt build → direct SDK call pattern
- **GOTCHA**: `implement_feature()` uses `asyncio.run(self.agent_sdk.execute_with_tools())` directly, NOT `send_message()`. Injection must happen on `tdd_prompt` before the SDK call.
- **GOTCHA**: Config fix retry calls (line 724) should also receive `user_prompt` — the operator instruction is relevant to config fixing context too.
- **VALIDATE**: `python -c "from src.agents.base_developer import BaseDeveloperAgent"` — import succeeds (abstract, but verifies no syntax errors)

### Task 5: Add `--prompt` option to CLI commands in `src/cli.py`

- **ACTION**: Add `--prompt` Click option to `plan`, `debrief`, and `execute` commands; pass through to agent calls
- **IMPLEMENT**:
  1. **`plan` command** (after line 79):
     ```python
     @click.option(
         "--prompt",
         default=None,
         help="Additional instruction to inject into the planning agent session.",
     )
     def plan(ticket_id: str, project: Optional[str] = None, revise: bool = False,
              force: bool = False, prompt: Optional[str] = None) -> None:
     ```
     At line 126, pass to agent:
     ```python
     result = plan_agent.run(ticket_id=ticket_id, worktree_path=worktree_path,
                             force=force, user_prompt=prompt)
     ```

  2. **`debrief` command** (after line 181):
     ```python
     @click.option(
         "--prompt",
         default=None,
         help="Additional instruction to inject into the debrief agent session.",
     )
     def debrief(ticket_id: str, project: Optional[str] = None,
                 prompt: Optional[str] = None) -> None:
     ```
     At line 219, pass to agent:
     ```python
     result = agent.run(ticket_id=ticket_id, project=project,
                        worktree_path=worktree_path, user_prompt=prompt)
     ```

  3. **`execute` command** (after line 341):
     ```python
     @click.option(
         "--prompt",
         default=None,
         help="Additional instruction to inject into the developer agent session.",
     )
     def execute(ticket_id: str, project: Optional[str] = None, max_iterations: int = 5,
                 force: bool = False, revise: bool = False, no_env: bool = False,
                 prompt: Optional[str] = None) -> None:
     ```
     At line 404 (revision path):
     ```python
     result = developer.run_revision(ticket_id=ticket_id, worktree_path=worktree_path,
                                     user_prompt=prompt)
     ```
     At line 569 (normal path):
     ```python
     dev_result = developer.run(plan_file=plan_file, worktree_path=worktree_path,
                                user_prompt=prompt)
     ```
- **MIRROR**: `cli.py:65-68` — Click option definition pattern
- **GOTCHA**: No short flag — `-p` is taken by `--project` on all three commands
- **GOTCHA**: `execute` has TWO call paths — normal `run()` and `--revise` `run_revision()`. Both need `user_prompt`.
- **VALIDATE**: `sentinel plan --help` shows `--prompt` in output

### Task 6: Manual Verification

- **ACTION**: Verify all commands accept `--prompt` and pass it through
- **VALIDATE**:
  1. `sentinel plan --help` — shows `--prompt` option
  2. `sentinel debrief --help` — shows `--prompt` option
  3. `sentinel execute --help` — shows `--prompt` option
  4. All three commands without `--prompt` — verify no behavioral change (backward compatible)
  5. `python -m pytest tests/ -v -k "not integration"` — existing tests pass

---

## Testing Strategy

### Verification Approach

This is a LOW complexity enhancement with no new modules, no new logic branches, and no new dependencies. The changes are mechanical threading of a parameter through existing call chains. Verification is via:

1. **Import checks** — Each modified module imports without error
2. **`--help` output** — Each command shows the new `--prompt` option
3. **Existing test suite** — No regressions (`user_prompt` defaults to `None`, so all existing callers work unchanged)
4. **Manual smoke test** — `sentinel plan ACME-123 --prompt "Focus on API"` injects correctly

### Edge Cases Checklist

- [x] `--prompt` not provided → `user_prompt=None` → `_append_operator_prompt()` returns original prompt unchanged
- [x] `--prompt ""` → Empty string is falsy → no injection (correct behavior)
- [x] `--prompt` with special characters (quotes, newlines) → Click handles shell escaping; content is just a string appended to prompt
- [x] Security reviewer → NOT modified → no injection path exists (by design)
- [x] Confidence evaluation → NOT modified → remains objective
- [x] Config fix retry in `run()` → receives `user_prompt` for consistency

---

## Validation Commands

### Level 1: STATIC ANALYSIS

```bash
python -c "from src.agents.base_agent import BaseAgent; print('OK')"
python -c "from src.agents.plan_generator import PlanGeneratorAgent; print('OK')"
python -c "from src.agents.functional_debrief import FunctionalDebriefAgent; print('OK')"
python -c "from src.agents.base_developer import BaseDeveloperAgent; print('OK')"
python -c "from src.cli import cli; print('OK')"
```

**EXPECT**: All print "OK"

### Level 2: CLI VERIFICATION

```bash
cd /workspace/sentinel && python -m sentinel plan --help | grep -q "prompt" && echo "PASS" || echo "FAIL"
cd /workspace/sentinel && python -m sentinel debrief --help | grep -q "prompt" && echo "PASS" || echo "FAIL"
cd /workspace/sentinel && python -m sentinel execute --help | grep -q "prompt" && echo "PASS" || echo "FAIL"
```

**EXPECT**: All print "PASS"

### Level 3: EXISTING TESTS

```bash
cd /workspace/sentinel && python -m pytest tests/ -v -k "not integration" --timeout=60
```

**EXPECT**: All tests pass (no regressions — `user_prompt` defaults to `None`)

## Implementation Order

1. `src/agents/base_agent.py` — Add `_append_operator_prompt()` (zero dependencies)
2. `src/agents/plan_generator.py` — Thread `user_prompt` through `run()` → `generate_plan()` / `revise_plan()`
3. `src/agents/functional_debrief.py` — Thread `user_prompt` through `run()` → `_generate_debrief()` / `_generate_followup()`
4. `src/agents/base_developer.py` — Thread `user_prompt` through `run()` → `implement_feature()` and `run_revision()` → `implement_feature()`
5. `src/cli.py` — Add `--prompt` option to all three commands, pass to agent calls
6. Validation — Import checks, `--help` verification, existing test suite
