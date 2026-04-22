# Feature: Project Facts Injection for Client-Facing Agents

## Summary

Introduce a structured project-facts document (`.sentinel/project-facts.yml`) that is written by the stack profiler alongside the existing `project-context.md`, and is automatically injected into the prompts of every client-facing agent (`ConfidenceEvaluatorAgent`, `FunctionalDebriefAgent`, and the existing plan-generation paths). Agents receive an explicit "VERIFIED — do not ask the client about these" block so they stop treating already-known facts (Drupal version, PHP version, project language, modules, build tooling) as gaps. A post-filter guardrail on the confidence report drops any question whose subject intersects a known fact, as a safety net when the prompt directive is ignored.

## User Story

As a **Sentinel end-user** (client receiving agent output in Jira)
I want **Sentinel to never ask me about facts it has already profiled from my project**
So that **I trust Sentinel as a system that knows my project, and I only spend time answering genuinely ambiguous questions**

## Problem Statement

`ConfidenceEvaluatorAgent` currently posts questions like *"Welke Drupal versie draait er momenteel op het project?"* to Jira, even though `stack_profiler.py:240-258` has already resolved the Drupal version from `composer.json` and persisted it to `.sentinel/project-context.md`. Root cause: `plan_generator.py:_load_stack_context()` injects the profile only into `generate_plan()`'s prompt; `ConfidenceEvaluatorAgent.evaluate()`, `FunctionalDebriefAgent._generate_debrief()`, `analyze_ticket()`, and `investigate_comments()` never receive it. The evaluator then treats absent stack metadata in the ticket as a gap and generates a clarifying question — erasing the trust Sentinel builds by profiling.

## Solution Statement

1. **Structured facts file**: Profiler writes a machine-readable `.sentinel/project-facts.yml` next to the human-readable `project-context.md`. Single source of truth, same profiler run.
2. **Loader module**: New `src/project_facts.py` with `load_project_facts()` and `format_facts_for_prompt()` — one entry point, used everywhere.
3. **Injection helper**: New `BaseAgent._append_known_facts()` method mirroring the existing `_append_operator_prompt()` shape.
4. **Threaded into client-facing agents**: `ConfidenceEvaluatorAgent.evaluate()` and `FunctionalDebriefAgent._generate_debrief()` accept an optional `known_facts` dict; `PlanGeneratorAgent` loads facts once per `run()` and passes them to every downstream call (analysis, generation, evaluation, investigation, debrief where applicable).
5. **Guardrail post-filter**: `_post_confidence_report()` runs a fact-match pass and drops questions whose tokens hit any known fact key/value before posting to Jira.

## Metadata

| Field            | Value                                                                  |
| ---------------- | ---------------------------------------------------------------------- |
| Type             | ENHANCEMENT (bug root cause — trust surface)                           |
| Complexity       | MEDIUM                                                                 |
| Systems Affected | `stack_profiler`, `base_agent`, `plan_generator`, `confidence_evaluator`, `functional_debrief`, new `project_facts` module |
| Dependencies     | None new — `pyyaml ^6.0.1` already in `pyproject.toml`                 |
| Estimated Tasks  | 10                                                                     |

---

## UX Design

### Before State

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                               BEFORE STATE                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║   stack_profiler ──► .sentinel/project-context.md  (markdown, prose)         ║
║                              │                                                ║
║                              ▼                                                ║
║                 PlanGenerator._load_stack_context()                           ║
║                              │                                                ║
║                              ▼                                                ║
║                 generate_plan()   ◄── receives profile                        ║
║                                                                              ║
║                 analyze_ticket()       ◄── NO profile                         ║
║                 investigate_comments() ◄── NO profile                         ║
║                 ConfidenceEvaluator    ◄── NO profile  ░░░ asks client ░░░   ║
║                 FunctionalDebrief      ◄── NO profile  ░░░ asks client ░░░   ║
║                                                                              ║
║   JIRA COMMENT (end-user sees):                                              ║
║   ┌───────────────────────────────────────────────────────────────┐          ║
║   │ 🤖 Sentinel Confidence Report — 78/100                         │         ║
║   │                                                                │         ║
║   │ Questions to Clarify                                           │         ║
║   │  1. Welke Drupal versie draait er momenteel op het project?   │         ║
║   │  2. Welke PHP versie wordt gebruikt?                          │         ║
║   │  3. (real ambiguous question about the ticket)                │         ║
║   └───────────────────────────────────────────────────────────────┘          ║
║                                                                              ║
║   PAIN: Sentinel asks the client about facts it has already profiled.        ║
║         Erodes trust ("does this thing even remember what it just did?").    ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                AFTER STATE                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║   stack_profiler ──┬─► .sentinel/project-context.md   (markdown, for humans) ║
║                    └─► .sentinel/project-facts.yml    (structured contract)  ║
║                              │                                                ║
║                              ▼                                                ║
║                    project_facts.load_project_facts()                         ║
║                              │                                                ║
║                 ┌────────────┼────────────┬────────────┬────────────┐        ║
║                 ▼            ▼            ▼            ▼            ▼        ║
║            analyze_      generate_    investigate_  Confidence  Functional   ║
║            ticket()      plan()       comments()    Evaluator   Debrief      ║
║            (facts)       (facts)      (facts)       (facts)     (facts)      ║
║                                                            │                  ║
║                                                            ▼                  ║
║                                         questions ── guardrail filter        ║
║                                                            │                  ║
║                                                            ▼                  ║
║                                              _post_confidence_report()       ║
║                                                                              ║
║   JIRA COMMENT (end-user sees):                                              ║
║   ┌───────────────────────────────────────────────────────────────┐          ║
║   │ 🤖 Sentinel Confidence Report — 94/100                         │         ║
║   │                                                                │         ║
║   │ Questions to Clarify                                           │         ║
║   │  1. (real ambiguous question about the ticket)                │         ║
║   └───────────────────────────────────────────────────────────────┘          ║
║                                                                              ║
║   VALUE: Sentinel acts as a system that KNOWS the project. Only              ║
║          genuinely-ambiguous questions reach the client.                     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                            | Before                                           | After                                                 | User Impact                                         |
| --------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------- | --------------------------------------------------- |
| Jira confidence report                              | Includes fact-questions (Drupal version, PHP …)  | Fact-questions filtered out; only real gaps remain    | Client only sees questions that need their input    |
| Jira functional debrief                             | May ask stack questions unprompted               | Stack facts pre-declared in agent prompt              | Debrief focuses on functional, not technical, gaps  |
| `.sentinel/` directory                              | `project-context.md` only                        | `project-context.md` + `project-facts.yml`            | Facts are inspectable and machine-readable          |
| `ConfidenceEvaluatorAgent.evaluate()` call          | `(plan, ticket, analysis)`                       | `(plan, ticket, analysis, known_facts=...)`           | Agents must receive facts explicitly                |
| `FunctionalDebriefAgent._generate_debrief()` prompt | No facts block                                   | Facts block injected                                  | Fewer redundant questions in debrief comment        |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                         | Lines         | Why Read This                                                                          |
| -------- | -------------------------------------------- | ------------- | -------------------------------------------------------------------------------------- |
| P0       | `src/agents/base_agent.py`                   | 232-236       | `_append_operator_prompt()` — shape to mirror for `_append_known_facts()`              |
| P0       | `src/agents/plan_generator.py`               | 285-330       | `_load_stack_context()` — the current (partial) injection we're generalizing           |
| P0       | `src/agents/plan_generator.py`               | 332-371       | `_auto_profile_if_needed()` — where the facts file will also be written                |
| P0       | `src/agents/plan_generator.py`               | 1281-1354     | `_post_confidence_report()` — where the guardrail post-filter inserts                  |
| P0       | `src/agents/plan_generator.py`               | 1628-1664     | `_evaluate_confidence()` — where we load facts and pass into the evaluator             |
| P0       | `src/agents/confidence_evaluator.py`         | 30-140        | `evaluate()` signature + `eval_prompt` — signature and prompt change site              |
| P1       | `src/agents/functional_debrief.py`           | 297-368       | `_generate_debrief()` — inject facts here                                              |
| P1       | `src/stack_profiler.py`                      | 123-234       | `profile()` and `format_for_llm_prompt()` — the dict we serialize to `project-facts.yml`|
| P1       | `src/config_loader.py`                       | 67-88         | YAML load pattern with `yaml.safe_load` (mirror this)                                  |
| P1       | `src/config_loader.py`                       | 12-28         | `_deep_merge()` — for global→project merge if we keep that path                        |
| P2       | `tests/test_confidence_evaluator.py`         | 1-154         | Test structure (pytest + Mock) to mirror                                               |
| P2       | `tests/test_stack_profiler.py`               | 13-200        | `tempfile.TemporaryDirectory()` repo fixtures to mirror                                |
| P2       | `.claude/PRPs/plans/agent-prompt-injection.plan.md` | all    | The `_append_operator_prompt()` precedent — same injection style we're extending        |

**External Documentation:**

| Source                                                                                              | Section                  | Why Needed                                                              |
| --------------------------------------------------------------------------------------------------- | ------------------------ | ----------------------------------------------------------------------- |
| [PyYAML 6.0 Docs](https://pyyaml.org/wiki/PyYAMLDocumentation#loading-yaml) (match pyproject ^6.0.1) | `safe_load` / `safe_dump`| Loader/writer API — `safe_load` only, never `load`, to prevent RCE    |

---

## Patterns to Mirror

**OPERATOR-PROMPT HELPER (shape template):**

```python
# SOURCE: src/agents/base_agent.py:232-236
# COPY THIS SHAPE for _append_known_facts():
def _append_operator_prompt(self, prompt: str, user_prompt: str | None) -> str:
    if not user_prompt:
        return prompt
    return f"{prompt}\n\n---\n## Operator Instruction\n\n{user_prompt}\n"
```

**EXISTING STACK CONTEXT LOAD (the pattern we generalize):**

```python
# SOURCE: src/agents/plan_generator.py:285-330
# Current partial implementation — superseded by project_facts.load_project_facts()
def _load_stack_context(self, ticket_id: str, worktree_path: Path) -> str:
    project_key = ticket_id.split("-")[0]
    project_config = self.config.get_project_config(project_key)
    stack_type = project_config.get("stack_type", "")
    if not stack_type:
        return ""
    context_parts: list[str] = []
    context_path = worktree_path / ".sentinel" / "project-context.md"
    if context_path.exists():
        content = context_path.read_text()
        context_parts.append(f"\n## Project Context\n\n{content}")
    ...
```

**YAML LOAD PATTERN (mirror this for project_facts loader):**

```python
# SOURCE: src/config_loader.py:67-88
import yaml
with open(self.config_path, "r") as f:
    base_config = yaml.safe_load(f) or {}
# NEVER yaml.load() — safe_load only.
```

**PROFILE WRITE PATTERN (where facts file also gets written):**

```python
# SOURCE: src/agents/plan_generator.py:369-371
context_path.parent.mkdir(parents=True, exist_ok=True)
context_path.write_text(markdown)
# Alongside this, we'll write facts_path.write_text(yaml.safe_dump(facts))
```

**EVALUATOR EVAL-PROMPT INJECTION POINT:**

```python
# SOURCE: src/agents/confidence_evaluator.py:77-140
eval_prompt = f"""Evaluate this implementation plan against its source Jira ticket.
...
## Jira Ticket
...
## Analysis Results
...
## Implementation Plan
...
## Your Task
..."""
# Insert facts block AFTER "Evaluate this..." preamble, BEFORE "## Jira Ticket"
# Use self._append_known_facts(eval_prompt, known_facts) OR template splice
```

**TEST STRUCTURE (pytest + Mock):**

```python
# SOURCE: tests/test_confidence_evaluator.py:11-28
@pytest.fixture
def mock_config():
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {"model": "...", "temperature": 0.1, "allowed_tools": []}
        config.get_llm_config.return_value = {"mode": "custom_proxy", "api_key": "test-api-key", "base_url": "https://test.api.com/v1"}
        config.get.return_value = []
        mock.return_value = config
        yield config
```

**PROFILER TEST REPO FIXTURE:**

```python
# SOURCE: tests/test_stack_profiler.py:13-105 (approx)
@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    (tmp_path / "composer.json").write_text('{"require": {"drupal/core": "^10.2"}}')
    return tmp_path
# Mirror this shape for project_facts loader tests.
```

---

## Files to Change

| File                                      | Action | Justification                                                                                   |
| ----------------------------------------- | ------ | ----------------------------------------------------------------------------------------------- |
| `src/project_facts.py`                    | CREATE | New module: loader, TypedDict shape, prompt-formatter, guardrail fact-match helper              |
| `src/stack_profiler.py`                   | UPDATE | Add `generate_profile_facts()` that returns the structured dict; preserve existing markdown API |
| `src/agents/base_agent.py`                | UPDATE | Add `_append_known_facts()` helper (mirrors `_append_operator_prompt()`)                        |
| `src/agents/plan_generator.py`            | UPDATE | Write facts file in `_auto_profile_if_needed()`; load once in `run()`; thread to downstream calls; add guardrail filter in `_post_confidence_report()` |
| `src/agents/confidence_evaluator.py`      | UPDATE | `evaluate()` accepts `known_facts: dict \| None`; inject into eval_prompt; add "do not ask about these" directive |
| `src/agents/functional_debrief.py`        | UPDATE | `_generate_debrief()` accepts `known_facts`; inject into prompt                                 |
| `tests/test_project_facts.py`             | CREATE | Unit tests for loader, formatter, guardrail                                                     |
| `tests/test_stack_profiler.py`            | UPDATE | Assert `generate_profile_facts()` output shape for Drupal fixture                               |
| `tests/test_confidence_evaluator.py`      | UPDATE | Add test: given `known_facts`, evaluator's prompt contains the facts block; given fact-matching question in LLM response, post-filter (covered in plan_generator test) |
| `tests/test_plan_generator.py`            | UPDATE | Add test for guardrail: questions mentioning known facts are filtered before posting             |

---

## NOT Building (Scope Limits)

- **Ticket-level override file** (`.sentinel/facts/{TICKET-ID}.yml`) — deferred. Design leaves a documented hook (`load_project_facts()` accepts `ticket_id` param, returns None for now) so v2 can add it without API churn.
- **Schema migration infrastructure** for future `schema_version` bumps — v1 uses `schema_version: 1`, loader warns-but-accepts unknown versions. Migration tooling is follow-up work.
- **Pydantic model for facts** — TypedDict is enough for type hints and keeps parity with profiler's existing dict output. Adding a pydantic `BaseModel` is extra surface for no concrete v1 benefit.
- **NLP-based question matching** in the guardrail — simple case-insensitive substring match on fact keys/values is the v1 filter. Semantic matching is follow-up if we see false negatives in practice.
- **Auto-detecting ticket/project language** — the `language:` key in facts must be set manually (via the profile or config). Auto-detection from Jira project metadata is out of scope.
- **Non-Drupal stacks (React, NextJS, etc.)** — schema is stack-agnostic, but the profiler's `generate_profile_facts()` only populates the Drupal branch in v1. Other stacks get a minimal `{schema_version, stack: null}` file.
- **`--force-profile` CLI flag to regenerate facts** — if `project-facts.yml` is missing but `project-context.md` exists, the next `_auto_profile_if_needed()` call regenerates both. No manual command needed for v1.
- **Injection into `SecurityReviewerAgent` / `DrupalReviewerAgent`** — out of scope. These are internal reviewers, not client-facing. If they start surfacing user-visible questions, revisit.
- **Injection into `PlanGeneratorAgent.analyze_ticket()`** — analysis is internal (its output is only consumed by later agents, never shown to the client), and its prompt explicitly forbids tool use, so "don't ask the client" isn't relevant. Injecting anyway would cost context tokens with no user-visible benefit. Revisit only if analysis starts hallucinating stack facts.

---

## Step-by-Step Tasks

Execute top-to-bottom. Each task validates independently before the next begins.

### Task 1: CREATE `src/project_facts.py`

- **ACTION**: Create new module providing the facts contract, loader, formatter, and guardrail.
- **IMPLEMENT**:
  ```python
  """Project facts loader and prompt formatter.

  The facts file is a structured contract of what Sentinel already KNOWS
  about a project. Injecting it into client-facing agent prompts prevents
  them from asking the client about already-profiled information.
  """
  from __future__ import annotations
  import logging
  from pathlib import Path
  from typing import Any, TypedDict

  import yaml

  logger = logging.getLogger(__name__)

  FACTS_FILENAME = "project-facts.yml"
  FACTS_DIR = ".sentinel"
  CURRENT_SCHEMA_VERSION = 1


  class ProjectFacts(TypedDict, total=False):
      schema_version: int
      stack: str | None          # "drupal" | None (v1)
      drupal: dict[str, Any]     # {major_version, modules: {custom, contrib}, ...}
      php: dict[str, Any]        # {version}
      language: dict[str, Any]   # {code, name}
      build: dict[str, Any]      # {tool}
      tests: dict[str, Any]      # {phpunit}


  def facts_path(worktree_path: Path) -> Path:
      return worktree_path / FACTS_DIR / FACTS_FILENAME


  def load_project_facts(
      worktree_path: Path,
      ticket_id: str | None = None,  # reserved for v2 ticket-level overrides
  ) -> ProjectFacts | None:
      """Load facts file. Returns None if missing/unreadable/malformed."""
      path = facts_path(worktree_path)
      if not path.exists():
          return None
      try:
          raw = yaml.safe_load(path.read_text()) or {}
      except (OSError, yaml.YAMLError) as e:
          logger.warning(f"Failed to load facts file {path}: {e}")
          return None
      if not isinstance(raw, dict):
          logger.warning(f"Facts file {path} is not a mapping, ignoring")
          return None
      version = raw.get("schema_version")
      if version != CURRENT_SCHEMA_VERSION:
          logger.warning(
              f"Facts file {path} schema_version={version!r}, expected "
              f"{CURRENT_SCHEMA_VERSION}. Loading best-effort."
          )
      return raw  # type: ignore[return-value]


  def write_project_facts(worktree_path: Path, facts: ProjectFacts) -> Path:
      """Persist facts as YAML. Caller ensures directory exists."""
      path = facts_path(worktree_path)
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(yaml.safe_dump(dict(facts), sort_keys=False, default_flow_style=False))
      logger.info(f"Wrote facts file: {path}")
      return path


  def format_facts_for_prompt(facts: ProjectFacts | None) -> str:
      """Render facts as a prompt block. Returns '' if facts is None/empty."""
      if not facts:
          return ""
      body = yaml.safe_dump(dict(facts), sort_keys=False, default_flow_style=False).rstrip()
      return (
          "---\n"
          "## Known Project Facts (VERIFIED)\n\n"
          "The following facts were resolved by Sentinel's stack profiler and are authoritative.\n"
          "Do NOT ask the client to confirm or clarify anything listed here. Do NOT treat the\n"
          "absence of these facts from the ticket text as a gap — they are known.\n\n"
          "```yaml\n"
          f"{body}\n"
          "```\n"
      )


  def fact_tokens(facts: ProjectFacts | None) -> set[str]:
      """Flatten facts to a set of lowercase tokens for guardrail substring matching.

      Used by the confidence-report post-filter to drop questions that touch
      any known fact. Conservative on purpose — matches on keys AND values.
      """
      if not facts:
          return set()
      tokens: set[str] = set()

      def _walk(obj: Any) -> None:
          if isinstance(obj, dict):
              for k, v in obj.items():
                  if isinstance(k, str) and k != "schema_version":
                      tokens.add(k.lower())
                  _walk(v)
          elif isinstance(obj, list):
              for item in obj:
                  _walk(item)
          elif isinstance(obj, (str, int, float)):
              tokens.add(str(obj).lower())

      _walk(facts)
      # Add high-signal synonyms for common facts to catch natural-language
      # questions (e.g. "Drupal version" when facts contain {drupal: {major_version: "10"}}).
      if facts.get("drupal"):
          tokens.update({"drupal version", "drupal versie"})  # en + nl
      if facts.get("php"):
          tokens.update({"php version", "php versie"})
      return tokens


  def question_mentions_known_fact(question: str, tokens: set[str]) -> bool:
      """True if the question contains any known fact token (case-insensitive)."""
      if not tokens:
          return False
      q = question.lower()
      return any(tok in q for tok in tokens if len(tok) >= 3)
  ```
- **MIRROR**: `src/config_loader.py:67-88` for YAML load style (`yaml.safe_load`, never `yaml.load`).
- **GOTCHA**: `yaml.safe_load("") or {}` — empty file returns `None`; guard with `or {}`.
- **GOTCHA**: `fact_tokens()` deliberately walks values, not just keys — so `"10"` (Drupal major version value) becomes a match token. Keeps v1 filter simple but loose; tune later if false-positives appear.
- **GOTCHA**: Threshold `len(tok) >= 3` in `question_mentions_known_fact()` prevents trivial tokens like `"10"` alone from matching every question containing a digit. Tokens like `"drupal"` (6) and `"drupal 10"` (8) still match; bare `"10"` (2) does not.
- **VALIDATE**: `python -c "from src.project_facts import load_project_facts, format_facts_for_prompt, fact_tokens, question_mentions_known_fact; print('OK')"`

### Task 2: EXTEND `src/stack_profiler.py` — add `generate_profile_facts()`

- **ACTION**: Add a sibling function to `generate_profile_markdown()` that returns the structured dict, then call it from `_auto_profile_if_needed()` to persist the facts file.
- **WHERE IN FILE**: Near `generate_profile_markdown()` (the existing module-level entry point consumed by `plan_generator._auto_profile_if_needed`). Add as a top-level function returning `(ProjectFacts, stack_type)`.
- **IMPLEMENT**:
  ```python
  from src.project_facts import CURRENT_SCHEMA_VERSION, ProjectFacts

  def generate_profile_facts(repo_path: Path, project_key: str) -> tuple[ProjectFacts, str | None]:
      """Produce structured project facts for .sentinel/project-facts.yml.

      Returns (facts_dict, stack_type). facts_dict always has schema_version
      and stack set, even when stack is None.
      """
      profiler = StackProfiler(repo_path, project_key)  # reuse existing class
      profile = profiler.profile()
      stack_type = profile.get("stack_type")
      facts: ProjectFacts = {
          "schema_version": CURRENT_SCHEMA_VERSION,
          "stack": _stack_family(stack_type),  # e.g. "drupal10" -> "drupal"
      }
      if stack_type and stack_type.startswith("drupal"):
          drupal = profile.get("drupal", {})
          facts["drupal"] = {
              "major_version": drupal.get("version"),   # "9" | "10" | "11"
          }
          composer = drupal.get("composer", {})
          if composer.get("php_version"):
              facts["php"] = {"version": composer["php_version"]}
          if composer.get("contrib_modules") or drupal.get("modules"):
              facts["modules"] = {
                  "contrib": composer.get("contrib_modules", []),
                  "custom": [m.get("machine_name") for m in drupal.get("modules", []) if m.get("machine_name")],
              }
          build = drupal.get("build_tools", {})
          if build:
              facts["build"] = {"tool": _detect_build_tool(build)}
          tests = drupal.get("tests", {})
          if tests:
              facts["tests"] = {"phpunit": bool(tests.get("has_phpunit"))}
      return facts, stack_type


  def _stack_family(stack_type: str | None) -> str | None:
      if not stack_type:
          return None
      if stack_type.startswith("drupal"):
          return "drupal"
      return stack_type


  def _detect_build_tool(build: dict) -> str | None:
      if build.get("lando"):
          return "lando"
      if build.get("ddev"):
          return "ddev"
      if build.get("docker_compose"):
          return "docker-compose"
      return None
  ```
- **MIRROR**: Existing `generate_profile_markdown(repo_path, project_key) -> tuple[str, str | None]` — same signature shape, different return payload.
- **GOTCHA**: Do NOT change `generate_profile_markdown()` — `_auto_profile_if_needed()` still calls it. Facts generation is ADDITIVE, never breaking.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -c "
  from pathlib import Path
  import tempfile, json
  from src.stack_profiler import generate_profile_facts
  with tempfile.TemporaryDirectory() as d:
      p = Path(d)
      (p / 'composer.json').write_text(json.dumps({'require': {'drupal/core': '^10.2'}}))
      facts, stack = generate_profile_facts(p, 'TEST')
      assert facts['schema_version'] == 1
      assert facts['stack'] == 'drupal'
      assert facts['drupal']['major_version'] == '10'
      print('OK', facts)
  "
  ```

### Task 3: UPDATE `src/agents/plan_generator.py` — `_auto_profile_if_needed()` writes facts file

- **ACTION**: After the profiler writes `project-context.md`, also call `generate_profile_facts()` and `write_project_facts()` to persist the structured facts. Regenerate both if either is missing.
- **WHERE**: `plan_generator.py:332-371` (`_auto_profile_if_needed`).
- **IMPLEMENT** (add after the existing `context_path.write_text(markdown)` at line 370):
  ```python
  from src.stack_profiler import generate_profile_markdown, generate_profile_facts
  from src.project_facts import write_project_facts, facts_path as _facts_path

  # Replace the existing early-return check so it triggers regeneration when
  # EITHER the markdown OR the facts file is missing.
  def _auto_profile_if_needed(self, worktree_path: Path, project_key: str) -> None:
      context_path = worktree_path / ".sentinel" / "project-context.md"
      facts_file = _facts_path(worktree_path)

      context_ok = context_path.exists() and len(context_path.read_text()) > 100
      facts_ok = facts_file.exists()
      if context_ok and facts_ok:
          return
      if context_ok and not facts_ok:
          logger.info("project-context.md exists but project-facts.yml missing — regenerating facts")

      # ... existing env-save/restore block unchanged ...

      markdown, stack_type = generate_profile_markdown(worktree_path, project_key)
      facts, _ = generate_profile_facts(worktree_path, project_key)

      # ... existing env-restore block unchanged ...

      if not stack_type:
          logger.info("Could not detect stack type, skipping auto-profile")
          # Still write a minimal facts file so downstream loads don't misfire
          write_project_facts(worktree_path, facts)
          return

      context_path.parent.mkdir(parents=True, exist_ok=True)
      context_path.write_text(markdown)
      write_project_facts(worktree_path, facts)

      # ... existing git-add/commit block, but include BOTH files ...
      subprocess.run(
          ["git", "add", ".sentinel/project-context.md", ".sentinel/project-facts.yml"],
          cwd=worktree_path, check=True, capture_output=True,
      )
      # ... existing commit + metadata update ...
  ```
- **MIRROR**: The existing early-return guard at line 340-344 — extend its condition rather than duplicating the block.
- **GOTCHA**: The env-save/restore dance around profiler invocation (lines 351-363) protects API credentials. Call `generate_profile_facts()` INSIDE the saved-env block too — it instantiates `StackProfiler` which may touch the same env.
- **GOTCHA**: `git add` now adds two files. If either doesn't exist the command succeeds (git ignores missing paths by default with `-A`, but explicit paths error). Guard with `if facts_file.exists()`.
- **VALIDATE**: Unit test in `test_plan_generator.py` (Task 9) — `_auto_profile_if_needed()` produces both files in a tmp repo.

### Task 4: UPDATE `src/agents/base_agent.py` — add `_append_known_facts()`

- **ACTION**: Add helper mirroring `_append_operator_prompt()`.
- **WHERE**: Immediately after `_append_operator_prompt()` (line 236).
- **IMPLEMENT**:
  ```python
  def _append_known_facts(
      self,
      prompt: str,
      facts: "ProjectFacts | None" = None,
      formatted: str | None = None,
  ) -> str:
      """Append a 'Known Project Facts (VERIFIED)' block to a prompt.

      Accepts either a raw facts dict (formatted inline) or a pre-formatted
      block (to avoid re-rendering when injecting into multiple prompts).
      Returns the prompt unchanged if both inputs are empty.
      """
      if formatted:
          block = formatted
      elif facts:
          from src.project_facts import format_facts_for_prompt
          block = format_facts_for_prompt(facts)
      else:
          return prompt
      if not block:
          return prompt
      return f"{prompt}\n\n{block}"
  ```
- **MIRROR**: `base_agent.py:232-236` — same signature shape, same separator style.
- **GOTCHA**: Import `format_facts_for_prompt` inside the method, not at module top. `base_agent.py` is imported everywhere; `project_facts.py` importing PyYAML at import-time is fine, but lazy import keeps the dependency graph clean and avoids a circular-import risk if `project_facts` ever needs config.
- **GOTCHA**: The `formatted` parameter exists so `PlanGenerator.run()` can render once and reuse across N agent calls without N YAML dumps.
- **VALIDATE**: `python -c "from src.agents.base_agent import BaseAgent; import inspect; assert '_append_known_facts' in BaseAgent.__dict__; print('OK')"`

### Task 5: UPDATE `src/agents/confidence_evaluator.py` — accept and inject `known_facts`

- **ACTION**: `evaluate()` and `run()` accept `known_facts`; eval prompt gets the facts block and an explicit directive.
- **IMPLEMENT**:
  1. Change `evaluate()` signature (line 30):
     ```python
     def evaluate(
         self,
         plan_content: str,
         ticket_data: Dict[str, Any],
         analysis: Dict[str, Any],
         known_facts: Dict[str, Any] | None = None,
     ) -> Dict[str, Any]:
     ```
  2. Build facts block at the top of `evaluate()` (near line 52 after the `logger.info`):
     ```python
     facts_block = ""
     if known_facts:
         from src.project_facts import format_facts_for_prompt
         facts_block = format_facts_for_prompt(known_facts)
     ```
  3. Change eval_prompt template (line 77-140): insert `{facts_block}` **after** the `**IMPORTANT**: Return ONLY a JSON object...` preamble and BEFORE `## Jira Ticket`. Also add an explicit scoring rule.
     ```python
     eval_prompt = f"""Evaluate this implementation plan against its source Jira ticket.

     **IMPORTANT**: Return ONLY a JSON object. Do NOT use any tools. Do NOT explore any codebase.
     {facts_block}
     ---

     ## Jira Ticket
     ...
     """
     ```
  4. In the **Your Task** section near line 115, add a new rule:
     ```
     Rules:
     - Items listed under "Known Project Facts (VERIFIED)" above are authoritative.
       Do NOT include them in `gaps`. Do NOT include questions about them in
       `questions`. Do NOT subtract from the score for their absence in the ticket.
     - Match the tone of any questions to the ticket's technical level.
     ```
  5. Change `run()` signature (line 177-189):
     ```python
     def run(self, **kwargs: Any) -> Dict[str, Any]:
         return self.evaluate(
             plan_content=kwargs["plan_content"],
             ticket_data=kwargs["ticket_data"],
             analysis=kwargs["analysis"],
             known_facts=kwargs.get("known_facts"),
         )
     ```
- **MIRROR**: `confidence_evaluator.py:77-140` existing eval_prompt format.
- **GOTCHA**: `known_facts` is optional and defaults to `None`, preserving all existing callers. Tests constructing the agent directly still work.
- **GOTCHA**: Do NOT inject facts via `self._append_known_facts()` here — the prompt is structured and the facts block must land before the ticket/plan sections, not at the end. Inline template splicing is correct.
- **VALIDATE**: Unit test (Task 9): given `known_facts={"drupal": {"major_version": "10"}}`, the built `eval_prompt` contains `"Known Project Facts (VERIFIED)"` and `"major_version: '10'"`.

### Task 6: UPDATE `src/agents/functional_debrief.py` — accept and inject `known_facts`

- **ACTION**: `_generate_debrief()` accepts `known_facts`; prompt gets the facts block via `_append_known_facts()`.
- **IMPLEMENT**:
  1. In `_generate_debrief()` (line 297), add parameter:
     ```python
     def _generate_debrief(
         self,
         ticket_id: str,
         ticket_data: Dict[str, Any],
         ...,
         user_prompt: str | None = None,
         known_facts: Dict[str, Any] | None = None,
     ) -> str:
     ```
  2. Before the existing `self._append_operator_prompt()` call (line 351), append the facts block:
     ```python
     prompt = self._append_known_facts(prompt, facts=known_facts)
     prompt = self._append_operator_prompt(prompt, user_prompt)
     response = self.send_message(prompt, cwd=self._cwd)
     ```
  3. In `run()` (line 40), load facts once and forward:
     ```python
     from src.project_facts import load_project_facts
     facts: Dict[str, Any] | None = None
     if worktree_path:
         facts = load_project_facts(Path(worktree_path))
     ```
     Pass `known_facts=facts` to every `_generate_debrief()` call inside `run()`. Same treatment for `_generate_followup()` if it calls send_message (check existing code: it does at line 417/448; inject there too).
- **MIRROR**: Operator-prompt precedent — the existing `_append_operator_prompt()` injection sites at lines 351, 417.
- **GOTCHA**: `worktree_path` can be `None` in debrief when running before a worktree exists. Skip facts load in that case.
- **VALIDATE**: `python -c "from src.agents.functional_debrief import FunctionalDebriefAgent; print('OK')"`

### Task 7: UPDATE `src/agents/plan_generator.py` — load facts once, pass to evaluator and debrief

- **ACTION**: In `run()` load facts once after Step 0 (auto-profile); thread them to `_evaluate_confidence()`. Also add `known_facts` param to `_evaluate_confidence()` and forward into the evaluator.
- **IMPLEMENT**:
  1. In `run()` (line 1419) after `_auto_profile_if_needed()` (line 1465):
     ```python
     from src.project_facts import load_project_facts
     known_facts = load_project_facts(worktree_path)
     logger.info(f"[RUN] Facts loaded: {bool(known_facts)}")
     ```
  2. Change `_evaluate_confidence()` signature (line 1628):
     ```python
     def _evaluate_confidence(
         self,
         plan_content: str,
         analysis: Dict[str, Any],
         ticket_id: str,
         project_key: str,
         known_facts: Dict[str, Any] | None = None,
     ) -> Dict[str, Any]:
         ...
         result = evaluator.evaluate(
             plan_content, analysis["ticket_data"], analysis, known_facts=known_facts,
         )
     ```
  3. Update the `_evaluate_confidence()` call site in `run()` (line 1555):
     ```python
     evaluation = self._evaluate_confidence(
         plan_content, analysis, ticket_id, project_key, known_facts=known_facts,
     )
     ```
  4. Keep `generate_plan()`/`_load_stack_context()` as-is for v1. Rationale: the existing narrative-markdown injection is functional there; adding a duplicate facts block would double the context cost. Once we confirm the facts approach is stable across client-facing agents, a follow-up can migrate `generate_plan()` to the structured facts block and retire `_load_stack_context()` (flagged in Notes).
- **MIRROR**: The operator-prompt threading in this same file (e.g. `user_prompt` forwarded through `run()` → call sites).
- **GOTCHA**: `known_facts` can be `None` if the project has never been profiled OR if the profiler failed silently. All downstream code must handle `None` as "inject nothing, fall back to current behavior."
- **VALIDATE**: Import check + Task 9 end-to-end test.

### Task 8: UPDATE `src/agents/plan_generator.py` — guardrail post-filter in `_post_confidence_report()`

- **ACTION**: Before posting to Jira, drop any question whose text matches a known fact token. Log each drop.
- **WHERE**: `_post_confidence_report()` at line 1281. Filter runs just after `questions = evaluation.get("questions", [])` (line 1297).
- **IMPLEMENT**:
  ```python
  from src.project_facts import load_project_facts, fact_tokens, question_mentions_known_fact

  def _post_confidence_report(
      self,
      ticket_id: str,
      evaluation: Dict[str, Any],
      worktree_path: Path | None = None,  # add this param
  ) -> None:
      ...
      questions = evaluation.get("questions", [])

      # Guardrail: drop questions about facts Sentinel already knows.
      if worktree_path:
          facts = load_project_facts(worktree_path)
          tokens = fact_tokens(facts)
          if tokens:
              filtered: list[str] = []
              for q in questions:
                  if question_mentions_known_fact(q, tokens):
                      logger.info(f"Guardrail dropped question (matches known fact): {q!r}")
                      continue
                  filtered.append(q)
              questions = filtered
      ...
  ```
  Update the caller at line 1578 to pass `worktree_path`:
  ```python
  self._post_confidence_report(ticket_id, evaluation, worktree_path=worktree_path)
  ```
- **MIRROR**: Defensive-filter style (e.g. check-empty-then-iterate).
- **GOTCHA**: Do NOT re-order questions list mutably — build a new `filtered` list. The original `evaluation` dict must stay intact (other callers may read it).
- **GOTCHA**: Log at INFO so drops are visible in session logs when diagnosing unexpected question omissions.
- **VALIDATE**: Task 9 test — given `questions=["Welke Drupal versie?", "Real ambiguous question"]` and facts containing `drupal`, only the second question remains.

### Task 9: CREATE `tests/test_project_facts.py` + UPDATE `tests/test_plan_generator.py`, `tests/test_confidence_evaluator.py`, `tests/test_stack_profiler.py`

- **ACTION**: Unit tests covering the new loader, formatter, guardrail helpers, profiler output shape, evaluator prompt change, and plan-generator filter.
- **IMPLEMENT — new file `tests/test_project_facts.py`**:
  ```python
  from pathlib import Path
  import pytest
  import yaml
  from src.project_facts import (
      load_project_facts, write_project_facts, format_facts_for_prompt,
      fact_tokens, question_mentions_known_fact, CURRENT_SCHEMA_VERSION,
  )


  def test_load_missing_returns_none(tmp_path: Path):
      assert load_project_facts(tmp_path) is None


  def test_load_roundtrip(tmp_path: Path):
      facts = {"schema_version": CURRENT_SCHEMA_VERSION, "stack": "drupal",
               "drupal": {"major_version": "10"}}
      write_project_facts(tmp_path, facts)
      loaded = load_project_facts(tmp_path)
      assert loaded == facts


  def test_load_malformed_yaml_returns_none(tmp_path: Path):
      (tmp_path / ".sentinel").mkdir()
      (tmp_path / ".sentinel" / "project-facts.yml").write_text(": : :")
      assert load_project_facts(tmp_path) is None


  def test_load_non_mapping_returns_none(tmp_path: Path):
      (tmp_path / ".sentinel").mkdir()
      (tmp_path / ".sentinel" / "project-facts.yml").write_text("- a\n- b\n")
      assert load_project_facts(tmp_path) is None


  def test_format_for_prompt_includes_header_and_yaml():
      block = format_facts_for_prompt({"schema_version": 1, "stack": "drupal",
                                        "drupal": {"major_version": "10"}})
      assert "Known Project Facts (VERIFIED)" in block
      assert "Do NOT ask the client" in block
      assert "major_version: '10'" in block or 'major_version: "10"' in block


  def test_format_none_returns_empty():
      assert format_facts_for_prompt(None) == ""
      assert format_facts_for_prompt({}) == ""


  def test_fact_tokens_flattens():
      tokens = fact_tokens({"schema_version": 1, "drupal": {"major_version": "10"}})
      assert "drupal" in tokens
      assert "major_version" in tokens
      assert "drupal version" in tokens  # synonym injection
      assert "schema_version" not in tokens  # meta key excluded


  def test_question_match_case_insensitive():
      tokens = fact_tokens({"drupal": {"major_version": "10"}})
      assert question_mentions_known_fact("Welke Drupal versie draait er?", tokens)
      assert question_mentions_known_fact("What DRUPAL VERSION?", tokens)
      assert not question_mentions_known_fact("Should we add caching?", tokens)


  def test_question_match_short_token_threshold():
      # Bare "10" (length 2) must not match every question containing "10".
      # Synonyms like "drupal version" (>= 3) still match.
      tokens = fact_tokens({"drupal": {"major_version": "10"}})
      assert not question_mentions_known_fact("Do we need 10 retries?", tokens - {"drupal", "drupal version", "drupal versie", "major_version"})
  ```
- **IMPLEMENT — update `tests/test_stack_profiler.py`**: add one test mirroring existing Drupal fixture shape.
  ```python
  def test_generate_profile_facts_drupal(tmp_repo: Path):
      from src.stack_profiler import generate_profile_facts
      facts, stack = generate_profile_facts(tmp_repo, "TEST")
      assert facts["schema_version"] == 1
      assert facts["stack"] == "drupal"
      assert facts["drupal"]["major_version"] in {"9", "10", "11"}
  ```
- **IMPLEMENT — update `tests/test_confidence_evaluator.py`**: add one test asserting `known_facts` injection.
  ```python
  def test_evaluator_prompt_includes_known_facts(mock_config, mock_agent_sdk, mock_prompt):
      agent = ConfidenceEvaluatorAgent()
      captured = {}
      def fake_send(prompt, cwd=None, **kw):
          captured["prompt"] = prompt
          return HIGH_CONFIDENCE_RESPONSE
      with patch.object(agent, "send_message", side_effect=fake_send):
          agent.evaluate(
              plan_content="plan",
              ticket_data=SAMPLE_TICKET_DATA,
              analysis=SAMPLE_ANALYSIS,
              known_facts={"schema_version": 1, "stack": "drupal",
                           "drupal": {"major_version": "10"}},
          )
      assert "Known Project Facts (VERIFIED)" in captured["prompt"]
      assert "major_version" in captured["prompt"]
  ```
- **IMPLEMENT — update `tests/test_plan_generator.py`**: add one test for the guardrail.
  ```python
  def test_post_confidence_report_filters_fact_questions(tmp_path, ...):
      # Arrange: write a facts file, build evaluation with a fact-matching question.
      from src.project_facts import write_project_facts
      write_project_facts(tmp_path, {"schema_version": 1, "stack": "drupal",
                                      "drupal": {"major_version": "10"}})
      agent = PlanGeneratorAgent()
      agent.jira = Mock()
      evaluation = {
          "confidence_score": 80, "threshold": 95, "gaps": [], "assumptions": [],
          "questions": ["Welke Drupal versie draait er?", "Should we cache results?"],
          "invest_evaluation": {}, "summary": "test",
      }
      agent._post_confidence_report("ACME-1", evaluation, worktree_path=tmp_path)
      posted = agent.jira.add_comment.call_args[0][1]
      assert "Welke Drupal versie" not in posted
      assert "Should we cache results?" in posted
  ```
- **MIRROR**: `tests/test_confidence_evaluator.py:11-154` for fixture/patch style; `tests/test_stack_profiler.py:13-200` for tmp-repo style.
- **GOTCHA**: The guardrail test requires a partially-mocked `PlanGeneratorAgent`. Patch `get_config`/`get_jira_client` via existing project fixtures, or construct via the same Mock pattern used by `test_confidence_evaluator`.
- **VALIDATE**: `cd /workspace/sentinel && python -m pytest tests/test_project_facts.py tests/test_stack_profiler.py tests/test_confidence_evaluator.py tests/test_plan_generator.py -v`

### Task 10: Manual verification against a real profiled project

- **ACTION**: On a worktree with a Drupal project, rerun `sentinel plan <TICKET>` and inspect the posted Jira comment.
- **VALIDATE**:
  1. `.sentinel/project-facts.yml` exists and has `schema_version: 1`, `stack: drupal`, `drupal.major_version: "<n>"`.
  2. `.sentinel/project-context.md` is still present and unchanged in format.
  3. Session logs contain `[RUN] Facts loaded: True`.
  4. Jira confidence report no longer contains any question matching the Drupal/PHP version pattern.
  5. Session log contains at least one `Guardrail dropped question (matches known fact)` entry when the evaluator would have asked a fact-question (optional; only if the LLM still emits one).

---

## Testing Strategy

### Unit Tests to Write

| Test File                              | Test Cases                                                                      | Validates                          |
| -------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------- |
| `tests/test_project_facts.py`          | load missing/valid/malformed/non-mapping; roundtrip write/read; format block content; fact_tokens flattening; question match case-insensitive; short-token threshold | Loader, formatter, guardrail       |
| `tests/test_stack_profiler.py` (add 1) | `generate_profile_facts()` returns `schema_version=1`, `stack=drupal`, `drupal.major_version` in {9,10,11} for Drupal fixture | Profiler produces correct shape    |
| `tests/test_confidence_evaluator.py` (add 1) | `evaluate(..., known_facts=...)` builds prompt containing "Known Project Facts (VERIFIED)" + key tokens | Evaluator prompt injection        |
| `tests/test_plan_generator.py` (add 1) | `_post_confidence_report()` with facts file + fact-matching question → dropped from posted comment | Guardrail end-to-end               |

### Edge Cases Checklist

- [x] Facts file missing → agents get `None` → prompts unchanged (no injection) → existing behavior preserved.
- [x] Facts file malformed YAML → loader returns `None` with WARNING log → no injection.
- [x] Facts file valid but schema_version unknown → WARNING log, best-effort load continues.
- [x] `known_facts = {}` (empty dict) → `format_facts_for_prompt` returns `""` → no injection.
- [x] Non-Drupal stack → facts file contains only `{schema_version, stack: null}` → `fact_tokens` returns empty → guardrail no-op.
- [x] Question matches only short token (e.g. bare "10") → NOT dropped (threshold ≥ 3 chars).
- [x] Multiple Drupal fact synonyms ("drupal version", "drupal versie") → either matches → question dropped.
- [x] `worktree_path = None` in debrief → skip facts load → no injection, no crash.
- [x] Existing callers of `evaluate()` without `known_facts` → default `None` → backward compatible.

---

## Validation Commands

### Level 1: STATIC ANALYSIS

```bash
cd /workspace/sentinel && python -c "from src.project_facts import load_project_facts, format_facts_for_prompt, fact_tokens, question_mentions_known_fact, write_project_facts, CURRENT_SCHEMA_VERSION; print('OK')"
cd /workspace/sentinel && python -c "from src.stack_profiler import generate_profile_facts; print('OK')"
cd /workspace/sentinel && python -c "from src.agents.base_agent import BaseAgent; assert '_append_known_facts' in BaseAgent.__dict__; print('OK')"
cd /workspace/sentinel && python -c "from src.agents.confidence_evaluator import ConfidenceEvaluatorAgent; import inspect; sig = inspect.signature(ConfidenceEvaluatorAgent.evaluate); assert 'known_facts' in sig.parameters; print('OK')"
cd /workspace/sentinel && python -c "from src.agents.functional_debrief import FunctionalDebriefAgent; print('OK')"
cd /workspace/sentinel && python -c "from src.agents.plan_generator import PlanGeneratorAgent; print('OK')"
cd /workspace/sentinel && python -m ruff check src/project_facts.py src/stack_profiler.py src/agents/
cd /workspace/sentinel && python -m mypy src/project_facts.py
```
**EXPECT**: All print `OK`; ruff and mypy exit 0.

### Level 2: UNIT TESTS (new + changed)

```bash
cd /workspace/sentinel && python -m pytest tests/test_project_facts.py tests/test_stack_profiler.py tests/test_confidence_evaluator.py tests/test_plan_generator.py -v --timeout=60
```
**EXPECT**: All tests pass.

### Level 3: FULL SUITE

```bash
cd /workspace/sentinel && python -m pytest tests/ -v -k "not integration" --timeout=120
```
**EXPECT**: All tests pass. No regressions — `known_facts`/`worktree_path` params default to `None`, preserving existing callers.

### Level 4: END-TO-END SMOKE (manual, on real project)

1. Check in to sentinel-dev: `docker compose exec sentinel-dev bash`
2. Run `sentinel plan <DRUPAL-TICKET-ID>` against a known-Drupal project.
3. Verify `.sentinel/project-facts.yml` exists and contains `drupal.major_version`.
4. Inspect the Jira "Sentinel Confidence Report" comment — no "Drupal version" / "PHP version" questions.
5. Grep session log for `Facts loaded: True` and any `Guardrail dropped question` entries.

### Level 5 & 6: Not Applicable

No database schema, no UI.

---

## Acceptance Criteria

- [ ] `.sentinel/project-facts.yml` is written whenever `.sentinel/project-context.md` is written; both committed together to the worktree branch.
- [ ] `ConfidenceEvaluatorAgent.evaluate()` accepts `known_facts` and injects a "Known Project Facts (VERIFIED)" block into its eval prompt with an explicit "do not ask, do not count as gap" directive.
- [ ] `FunctionalDebriefAgent._generate_debrief()` accepts `known_facts` and injects the same block via `_append_known_facts()`.
- [ ] `PlanGeneratorAgent.run()` loads facts once via `load_project_facts()` and forwards to `_evaluate_confidence()` (and downstream to the evaluator).
- [ ] `_post_confidence_report()` drops questions whose text matches any known fact token before posting to Jira, with INFO log per drop.
- [ ] All unit tests pass (new + existing). No regressions in the full suite.
- [ ] Manual verification on a real Drupal project: the Drupal-version question no longer appears in the Jira report.
- [ ] No new dependencies added to `pyproject.toml`.

---

## Completion Checklist

- [ ] Task 1: `src/project_facts.py` created; loader/formatter/guardrail implemented.
- [ ] Task 2: `generate_profile_facts()` added to `stack_profiler.py`; existing markdown path untouched.
- [ ] Task 3: `_auto_profile_if_needed()` writes both markdown and YAML; git commit covers both.
- [ ] Task 4: `BaseAgent._append_known_facts()` added.
- [ ] Task 5: `ConfidenceEvaluatorAgent.evaluate()` accepts `known_facts`; prompt injection + directive added.
- [ ] Task 6: `FunctionalDebriefAgent._generate_debrief()` accepts `known_facts`; `run()` loads and forwards.
- [ ] Task 7: `PlanGeneratorAgent.run()` loads facts once; `_evaluate_confidence()` forwards.
- [ ] Task 8: `_post_confidence_report()` guardrail filter implemented with `worktree_path` param.
- [ ] Task 9: Unit tests written and passing.
- [ ] Task 10: Manual verification against real Drupal project passes.
- [ ] Level 1-3 validation commands pass.

---

## Risks and Mitigations

| Risk                                                                                                     | Likelihood | Impact | Mitigation                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM ignores the "Known Facts" directive and still asks about them.                                       | MED        | MED    | Guardrail post-filter in `_post_confidence_report()` is the safety net. INFO-log each drop so we can tune the directive or add evaluator-side retries.              |
| Guardrail false-positives (drops a genuine question that happens to contain a fact token).               | MED        | MED    | Token-length threshold (≥3) + conservative synonym set. Log drops so reviewers can spot misfires. Follow-up: semantic matching if we see real misses in production. |
| Facts file drifts from `project-context.md` (profiler updates one, not the other).                        | LOW        | MED    | Both files are generated in the same `_auto_profile_if_needed()` call from the same `StackProfiler.profile()` result. Guarded by the "regenerate if either missing" check. |
| Existing projects have `project-context.md` but no facts file — silent bypass of guardrail on next plan. | HIGH (at rollout) | LOW | `_auto_profile_if_needed()` explicitly regenerates when `project-facts.yml` is missing even if markdown exists. Rollout self-heals on next run.                    |
| Non-Drupal projects get an empty facts file — is the guardrail meaningful?                               | LOW        | LOW    | Empty `fact_tokens()` → no drops → guardrail is a no-op. Prompt directive also no-op. Correct by construction.                                                     |
| `ProjectFacts` TypedDict drifts from profiler output over time.                                          | MED (over time) | LOW | `schema_version` field + WARNING on mismatch. Future: migration helper. Tracked in NOT Building.                                                                    |
| Evaluator prompt bloat (every eval now carries facts).                                                   | LOW        | LOW    | YAML block is ~10-30 lines for Drupal. Claude 4.5 Sonnet input context is not a constraint here.                                                                    |

---

## Notes

**Why not extend `_load_stack_context()` instead of a new module?**
`_load_stack_context()` is specific to plan generation and reads narrative markdown. The facts file is a different shape (structured) with different consumers (many agents). Keeping them separate lets the markdown stay prose-for-humans and the YAML stay contract-for-machines. Follow-up: once the facts approach proves out, `_load_stack_context()` can be replaced entirely by `format_facts_for_prompt()` and the markdown retired or reduced to narrative-only content (tracked as a follow-up, not in this plan).

**Why TypedDict instead of pydantic `BaseModel`?**
`pydantic ^2.5.0` is available (`pyproject.toml`), but the profiler already emits plain dicts and nothing in the pipeline validates them today. A TypedDict gives mypy coverage at zero runtime cost and zero API change for the profiler. If we later grow rich validation needs (required fields, version migrations), revisit.

**Why skip `analyze_ticket()`?**
Its output isn't user-facing (consumed by subsequent agents only), its prompt explicitly forbids tool use, and it already runs with `cwd=None`. Adding facts would cost tokens with no client-visible benefit. Revisit only if analysis starts fabricating stack facts.

**Task-tracking tooling unavailable in this session**
Per CLAUDE.md this project uses `bd` (beads) + Archon MCP. Memory note `project_beads_dolt_issue.md` confirms `bd` is unreachable from worktree paths in the current sandbox, and no Archon MCP tool is exposed to this session. **Follow-up**: when working from a path where `bd` is reachable (or from sentinel-dev), file:
- A `bd` issue tracking this plan for long-term visibility.
- A follow-up issue for ticket-level override support (`.sentinel/facts/{TICKET-ID}.yml`).
- A follow-up issue for migrating `_load_stack_context()` to use `format_facts_for_prompt()`.

**Infrastructure note (from CLAUDE.md)**
Plan targets files under `/workspace/sentinel/` (the Claude Code sandbox source mount). These are bind-mounted into `sentinel-dev` at `/app`, so edits here are immediately executable via `docker compose exec sentinel-dev`. `git push` must be done from the host or from `sentinel-dev` (this sandbox has no SSH keys).
