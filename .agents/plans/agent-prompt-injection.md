# Add `--prompt` option to all agent-facing commands

## Context

Sentinel's agent commands (`plan`, `debrief`, `execute`) invoke Claude Agent SDK sessions with prompts built entirely from internal logic — ticket data, plan files, system prompts. There is no mechanism to inject an ad-hoc instruction into a session from the CLI.

**Use cases:**
1. **Steering execution** — "Focus on the database migration first" or "Skip the frontend tests, they're known-broken"
2. **Providing context the ticket lacks** — "The client confirmed they want Drupal 10 only, ignore D9 references"
3. **Debugging / development** — "Print your system prompt before starting" or "Only implement the first task then stop"
4. **Revision guidance** — "The reviewer wants the helper extracted into a service class, not a trait"

**Why a CLI option?** The alternative is editing plan files or Jira tickets to embed steering instructions. That's slow, pollutes artifacts, and can't be done ad-hoc. A `--prompt` flag is immediate, ephemeral, and composable with existing flags.

## Design

### CLI surface

Add `--prompt` (no short flag — `-p` is taken by `--project`) to three commands:

| Command | Current agent entry point | Where prompt is injected |
|---------|--------------------------|--------------------------|
| `plan` | `PlanGeneratorAgent.run()` | Appended to the planning prompt before SDK call |
| `debrief` | `FunctionalDebriefAgent.run()` | Appended to the debrief prompt before SDK call |
| `execute` | `BaseDeveloperAgent.run()` + `SecurityReviewerAgent.run()` | Appended to developer prompt only (security review stays autonomous) |
| `execute --revise` | `BaseDeveloperAgent.run_revision()` | Appended to revision prompt |

### Option definition (same on all three commands)

```python
@click.option(
    "--prompt",
    default=None,
    help="Additional instruction to inject into the agent session.",
)
```

### Injection strategy

The user prompt is appended as a clearly delimited section to the **user prompt** (not the system prompt), right before the SDK call. This keeps it visible in logs and avoids conflating user instructions with agent identity.

```python
if user_prompt:
    prompt += f"\n\n---\n## Operator Instruction\n\n{user_prompt}\n"
```

The heading "Operator Instruction" is deliberate — it signals that this is authoritative guidance from the human running Sentinel, not part of the generated plan or ticket context.

## Changes

### 1. `src/cli.py` — Add `--prompt` option to `plan`, `debrief`, `execute`

**`plan` command (line ~63):**
```python
@click.option(
    "--prompt",
    default=None,
    help="Additional instruction to inject into the planning agent session.",
)
def plan(ticket_id: str, project: Optional[str] = None, revise: bool = False,
         force: bool = False, prompt: Optional[str] = None) -> None:
```
Pass `prompt` to `plan_agent.run()` as `user_prompt=prompt`.

**`debrief` command (line ~176):**
```python
@click.option(
    "--prompt",
    default=None,
    help="Additional instruction to inject into the debrief agent session.",
)
def debrief(ticket_id: str, project: Optional[str] = None,
            prompt: Optional[str] = None) -> None:
```
Pass `prompt` to `agent.run()` as `user_prompt=prompt`.

**`execute` command (line ~314):**
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
Pass `prompt` to `developer.run()` and `developer.run_revision()` as `user_prompt=prompt`.

### 2. `src/agents/base_agent.py` — Thread `user_prompt` through `run()` and `send_message()`

Add `user_prompt: str | None = None` parameter to `run()`. When present, append the operator instruction block to the prompt string before calling `execute_with_tools()`.

```python
def _append_operator_prompt(self, prompt: str, user_prompt: str | None) -> str:
    if not user_prompt:
        return prompt
    return f"{prompt}\n\n---\n## Operator Instruction\n\n{user_prompt}\n"
```

### 3. `src/agents/plan_generator.py` — Accept and forward `user_prompt`

Modify `PlanGeneratorAgent.run()` signature to accept `user_prompt: str | None = None`. Apply `_append_operator_prompt()` to the prompt before the SDK call.

The plan generator builds prompts in multiple places (initial generation, revision, confidence evaluation). The operator prompt should be injected into:
- **Plan generation prompt** — Yes (primary use case)
- **Plan revision prompt** — Yes (same agent, revision context benefits from steering)
- **Confidence evaluation prompt** — No (confidence should be objective)

### 4. `src/agents/base_developer.py` — Accept and forward `user_prompt`

Modify `BaseDeveloperAgent.run()` and `run_revision()` signatures to accept `user_prompt: str | None = None`.

In `run()`, the TDD prompt is built by `_build_tdd_prompt()`. Apply `_append_operator_prompt()` to the final `tdd_prompt` before passing to `execute_with_tools()` (line ~334):

```python
tdd_prompt = self._build_tdd_prompt(task, context, worktree_path)
tdd_prompt = self._append_operator_prompt(tdd_prompt, user_prompt)
result = asyncio.run(self.agent_sdk.execute_with_tools(
    prompt=tdd_prompt,
    ...
))
```

In `run_revision()`, same pattern — append before the SDK call.

### 5. `src/agents/functional_debrief.py` — Accept and forward `user_prompt`

Modify `FunctionalDebriefAgent.run()` signature to accept `user_prompt: str | None = None`. Apply `_append_operator_prompt()` before the SDK call.

### 6. Security reviewer — NO changes

The security reviewer must remain autonomous and unopinionated. Allowing prompt injection into security review would undermine its purpose ("ignore that SQL injection, it's fine"). The `--prompt` flag on `execute` only affects the developer agent.

## Files to modify

| File | Action |
|------|--------|
| `src/cli.py` | Add `--prompt` option to `plan`, `debrief`, `execute` commands; pass through to agent `.run()` calls |
| `src/agents/base_agent.py` | Add `_append_operator_prompt()` helper method |
| `src/agents/plan_generator.py` | Accept `user_prompt` in `run()`, inject into generation and revision prompts |
| `src/agents/base_developer.py` | Accept `user_prompt` in `run()` and `run_revision()`, inject into TDD and revision prompts |
| `src/agents/functional_debrief.py` | Accept `user_prompt` in `run()`, inject into debrief prompt |

## What this deliberately excludes

- **Short flag `-p`** — Already taken by `--project` on all three commands. `--prompt` is long-form only. Users who want brevity can use shell aliases.
- **Prompt from file (`--prompt-file`)** — YAGNI. Shell provides this: `--prompt "$(cat instructions.txt)"`.
- **Multi-turn injection** — The prompt is injected once at the start of the session. If mid-session steering is needed, that's a different feature (session resume + message injection).
- **Security reviewer injection** — Deliberately excluded. See rationale above.
- **Prompt logging to MR/Jira** — The operator instruction appears in Agent SDK logs (it's part of the user prompt). No additional logging needed initially.

## Implementation order

1. `src/agents/base_agent.py` — Add `_append_operator_prompt()` (zero dependencies)
2. `src/agents/plan_generator.py` — Thread `user_prompt` through
3. `src/agents/functional_debrief.py` — Thread `user_prompt` through
4. `src/agents/base_developer.py` — Thread `user_prompt` through `run()` and `run_revision()`
5. `src/cli.py` — Add option to all three commands, pass to agent calls
6. Manual test: `sentinel plan ACME-123 --prompt "Focus only on the API layer"`

## Verification

1. `sentinel plan ACME-123 --prompt "Only plan the database migration"` — Verify prompt appears in agent SDK logs, plan is steered
2. `sentinel debrief ACME-123 --prompt "Ask about the authentication requirements"` — Verify debrief focuses on auth
3. `sentinel execute ACME-123 --prompt "Implement task 1 only, then stop"` — Verify developer respects instruction, security review is unaffected
4. `sentinel execute ACME-123 --revise --prompt "Extract the helper into a service"` — Verify revision prompt includes instruction
5. All three commands without `--prompt` — Verify no behavioral change (backward compatible)
