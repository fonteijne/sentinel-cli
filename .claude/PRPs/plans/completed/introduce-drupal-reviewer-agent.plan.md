# Feature: Drupal Reviewer Agent

## Summary

Introduce a **DrupalReviewerAgent** — an LLM-based code reviewer that evaluates Drupal merge requests against 11 review dimensions (correctness, DI, caching, security, config management, performance, testing, standards, a11y, docs, Drupal idiomatic correctness). The agent extends `ReviewAgent`, loads a structured overlay from `prompts/overlays/drupal_reviewer.md`, injects per-project environment context, and produces machine-parseable handover JSON that downstream agents can consume. The execute workflow in `src/cli.py` gains a new **Drupal review step** inserted after the security review, conditional on `stack_type.startswith("drupal")`.

## User Story

As a **Sentinel operator running a Drupal project ticket**
I want an **automated Drupal-specialist code review** after the developer and security agents complete
So that **Drupal-specific quality issues (missing DI, cache metadata gaps, deprecated APIs, missing access checks) are caught before merge** without manual principal-engineer review.

## Problem Statement

The current execute loop runs Developer → Config Validation → Security Review. Security review catches generic vulnerabilities (SQLi, XSS, secrets) via pattern-matching but has no awareness of Drupal idioms: dependency injection violations, missing `#cache` metadata, deprecated API usage, incorrect hook/event selection, missing `->accessCheck()`, or config export gaps. These quality issues currently pass undetected and require manual review.

## Solution Statement

Create a `DrupalReviewerAgent` that:
1. Extends `ReviewAgent` (like `SecurityReviewerAgent`)
2. Gets changed files via `git diff` (reuse the same pattern from SecurityReviewerAgent)
3. Reads file contents and sends them to Claude with a structured Drupal review system prompt
4. Parses the LLM response into findings with severity, category, file:line, and fix directives
5. Applies verdict logic: any BLOCKER → `REQUEST_CHANGES`; else `APPROVE`
6. Returns a result dict compatible with the existing `sec_result` shape so the CLI loop can act on it

The CLI integration inserts the Drupal review after the security review passes (line 706 of `src/cli.py`), gated on `stack_type.startswith("drupal")`.

## Metadata

| Field            | Value                                                        |
| ---------------- | ------------------------------------------------------------ |
| Type             | NEW_CAPABILITY                                               |
| Complexity       | MEDIUM                                                       |
| Systems Affected | agents, cli, config, prompts, tests                         |
| Dependencies     | None (uses existing ReviewAgent base, Agent SDK, git)        |
| Estimated Tasks  | 8                                                            |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════╗
║                 EXECUTE WORKFLOW (Drupal ticket)                  ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                   ║
║   ┌──────────┐     ┌────────────┐     ┌─────────────────┐        ║
║   │ Developer │────►│  Config    │────►│ Security Review │        ║
║   │ (Drupal)  │     │ Validation │     │ (pattern-match) │        ║
║   └──────────┘     └────────────┘     └────────┬────────┘        ║
║                                                 │                 ║
║                                          ┌──────▼──────┐         ║
║                                          │  Approved?   │         ║
║                                          │ → push + MR  │         ║
║                                          └─────────────┘         ║
║                                                                   ║
║   PAIN: No Drupal-specific quality review. DI violations,        ║
║   missing cache metadata, deprecated APIs, missing accessCheck   ║
║   all pass undetected.                                           ║
╚═══════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════╗
║                 EXECUTE WORKFLOW (Drupal ticket)                  ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                   ║
║   ┌──────────┐     ┌────────────┐     ┌─────────────────┐        ║
║   │ Developer │────►│  Config    │────►│ Security Review │        ║
║   │ (Drupal)  │     │ Validation │     │ (pattern-match) │        ║
║   └──────────┘     └────────────┘     └────────┬────────┘        ║
║                                                 │                 ║
║                                          ┌──────▼──────┐         ║
║                                          │  Sec OK?    │         ║
║                                          └──────┬──────┘         ║
║                                                 │ yes             ║
║                                          ┌──────▼──────┐         ║
║                                          │ Drupal Code │ ◄── NEW ║
║                                          │   Review    │         ║
║                                          │ (LLM-based) │         ║
║                                          └──────┬──────┘         ║
║                                                 │                 ║
║                                          ┌──────▼──────┐         ║
║                                          │ Drupal OK?  │         ║
║                                          │ → push + MR │         ║
║                                          └─────────────┘         ║
║                                                                   ║
║   VALUE: Drupal idiom violations caught automatically.           ║
║   Structured handover JSON feeds back to Developer agent.        ║
╚═══════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| `src/cli.py` execute loop | Security approval → break | Security approval → Drupal review → break (if Drupal stack) | Extra review gate catches Drupal-specific issues |
| CLI output | No Drupal review step | `🔍 Drupal Review: Reviewing code...` with APPROVE/REQUEST_CHANGES | Visibility into Drupal quality status |
| Developer iteration | Only security feedback | Security + Drupal reviewer feedback | Developer agent receives precise Drupal-specific fix directives |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `src/agents/security_reviewer.py` | 1-60 | Agent structure, `__init__`, extends ReviewAgent — MIRROR this |
| P0 | `src/agents/security_reviewer.py` | 53-124 | `_get_changed_files()` — REUSE this pattern |
| P0 | `src/agents/security_reviewer.py` | 664-719 | `run()` method — MIRROR return shape |
| P0 | `src/agents/drupal_developer.py` | 38-56 | `_load_stack_overlay()` + `_inject_environment_context()` — COPY these |
| P0 | `src/agents/base_agent.py` | 277-296 | ReviewAgent base class — parent to extend |
| P1 | `prompts/drupal_reviewer_perplexity.md` | all | Source prompt — adapt for overlay |
| P1 | `src/cli.py` | 640-716 | Execute loop — integration point |
| P1 | `config/config.yaml` | 33-37 | SecurityReview config pattern — mirror for drupal_reviewer |
| P2 | `tests/test_security_reviewer.py` | all | Test patterns for review agents |
| P2 | `tests/test_drupal_developer.py` | 457-516 | Environment context injection tests — mirror for reviewer |

---

## Patterns to Mirror

**AGENT_INIT (SecurityReviewerAgent):**
```python
# SOURCE: src/agents/security_reviewer.py:23-37
class SecurityReviewerAgent(ReviewAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="security_reviewer",
            model="claude-4-5-haiku",
            temperature=0.1,
            veto_power=True,
        )
```

**OVERLAY_LOADING (DrupalDeveloperAgent):**
```python
# SOURCE: src/agents/drupal_developer.py:58-68
def _load_stack_overlay(self) -> None:
    overlays_dir = Path(__file__).parent.parent.parent / "prompts" / "overlays"
    overlay_path = overlays_dir / "drupal_developer.md"
    if overlay_path.exists():
        try:
            content = overlay_path.read_text()
            self.system_prompt += "\n\n" + content
            logger.info(f"Loaded Drupal developer overlay ({len(content)} chars)")
        except OSError as e:
            logger.warning(f"Failed to read Drupal developer overlay: {e}")
```

**ENVIRONMENT_CONTEXT_INJECTION (DrupalDeveloperAgent):**
```python
# SOURCE: src/agents/drupal_developer.py:48-56
def _inject_environment_context(self) -> None:
    env = self.config.get("agents.drupal_developer.environment", {})
    if not env or not isinstance(env, dict):
        return
    def replace_placeholder(match: re.Match) -> str:
        key = match.group(1).strip()
        return str(env.get(key, "Not specified"))
    self.system_prompt = re.sub(r"\{\{\s*(\w+)\s*\}\}", replace_placeholder, self.system_prompt)
```

**GIT_CHANGED_FILES (SecurityReviewerAgent):**
```python
# SOURCE: src/agents/security_reviewer.py:53-124
def _get_changed_files(self, worktree_path, default_branch="main"):
    # git merge-base + git diff --name-only --diff-filter=ACMR
    # Returns List[Path] or None (caller falls back to full scan)
```

**RUN_RETURN_SHAPE (SecurityReviewerAgent):**
```python
# SOURCE: src/agents/security_reviewer.py:708-719
return {
    "approved": approved,
    "findings": findings,
    "feedback": feedback,
    "veto": not approved,
    "critical_count": ...,
    "high_count": ...,
}
```

**CLI_IMPORT_PATTERN:**
```python
# SOURCE: src/cli.py:19-23
from src.agents.security_reviewer import SecurityReviewerAgent
```

**CONFIG_PATTERN:**
```yaml
# SOURCE: config/config.yaml:33-37
security_review:
  model: claude-4-5-sonnet
  temperature: 0.1
  strictness: 5
  veto_power: true
```

**TEST_FIXTURE_PATTERN (DrupalDeveloper):**
```python
# SOURCE: tests/test_drupal_developer.py:14-29
@pytest.fixture
def mock_config():
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.2,
        }
        config.get_llm_config.return_value = { ... }
        config.get.return_value = ["Read", "Grep", "Glob"]
        mock.return_value = config
        yield config
```

---

## Files to Change

| File | Action | Justification |
|------|--------|---------------|
| `prompts/overlays/drupal_reviewer.md` | CREATE | Overlay with 11 review dimensions, severity taxonomy, output format |
| `src/agents/drupal_reviewer.py` | CREATE | DrupalReviewerAgent class extending ReviewAgent |
| `tests/test_drupal_reviewer.py` | CREATE | Test suite for agent init, review logic, verdict, CLI integration |
| `config/config.yaml` | UPDATE | Add `drupal_reviewer` agent config section |
| `src/cli.py` | UPDATE | Import DrupalReviewerAgent; insert Drupal review step after security |

---

## NOT Building (Scope Limits)

- **Not modifying SecurityReviewerAgent** — it continues to handle generic security scanning unchanged.
- **Not adding MCP/tool-based code analysis** — the reviewer uses the LLM with diff context, not AST parsing or phpcs/phpstan execution. Tool-based analysis is a future enhancement.
- **Not implementing the `/clarify` endpoint** — the handover JSON references it but the BA/PM agent integration is out of scope.
- **Not implementing automatic fix application** — findings go back to the developer agent via the existing iteration loop.
- **Not adding a Python reviewer counterpart** — this is Drupal-specific, gated on `stack_type`.
- **Not modifying the handover JSON consumer** — we produce the JSON; consuming it in the developer agent iteration is future work.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: CREATE `prompts/overlays/drupal_reviewer.md`

- **ACTION**: Create the Drupal reviewer overlay prompt
- **IMPLEMENT**: Adapt `prompts/drupal_reviewer_perplexity.md` into an overlay format matching `prompts/overlays/drupal_developer.md`. Include ALL of these sections — each maps to a section in the Perplexity source:

  **Section 1: Identity & Role** (from `<role>`)
  - DrupalSentinel identity, fluencies (Drupal 10.3+/11.x, Symfony 6/7, PHP 8.3+, Twig 3, etc.)
  - Principal engineer reviewer persona

  **Section 2: Operating Principles** (from `<operating_principles>`, all 9)
  1. Evidence over opinion — cite file:line, API docs, change records
  2. Enforce Developer agent standards — match DrupalForge prompt rules
  3. Block only what must block — reserve BLOCKER for specific triggers
  4. Praise what's done well — 1-3 PRAISE findings per review
  5. Prefer diffs over prose — suggested fixes as unified diff
  6. Never fabricate — no invented APIs, service IDs, module names
  7. Escalate ambiguity — QUESTION severity when context missing
  8. Review the change, not the codebase — pre-existing issues → tech_debt only
  9. Be token-efficient — targeted directives, not file rewrites

  **Section 3: Review Scope — 11 Dimensions** (from `<review_scope>`)
  Preserve the full detail for each, especially the BLOCKER triggers:
  1. Correctness
  2. Drupal Idiomatic Correctness — include the explicit deprecated API list: `hook_menu`, `drupal_get_path`, `drupal_set_message`, `db_query`, `entity_load`, `variable_get/set`, `hook_boot/init`, legacy annotations
  3. Dependency Injection — **BLOCKER if `\Drupal::service()`, `\Drupal::entityTypeManager()` in OO code**
  4. Cache Metadata — **BLOCKER if render array with dynamic content lacks `#cache`**
  5. Security — **BLOCKER on: SQLi, XSS, CSRF gaps, missing `->accessCheck()`, hardcoded secrets, `eval()`, `unserialize()` on user input**
  6. Configuration Management
  7. Performance
  8. Testing
  9. Coding Standards
  10. Accessibility (WCAG 2.2 AA)
  11. Documentation & Maintainability

  **Section 4: Severity Taxonomy** (from `<severity_taxonomy>`)
  - Table with all 6 levels: BLOCKER, MAJOR, MINOR, NIT, QUESTION, PRAISE
  - Include "Developer Action" column

  **Section 5: Review Workflow** (from `<workflow>`, ALL 6 steps — do NOT skip steps 1-5)
  1. Intake — read MR title, description, linked ticket, full diff
  2. Context Gathering — identify Drupal version, affected modules, existing tests
  3. Systematic Review — walk diff file-by-file against all 11 dimensions
  4. Holistic Review — MR achieves goal? Missing pieces?
  5. Verdict — APPROVE / REQUEST_CHANGES / COMMENT_ONLY
  6. Self-Review (silent) — 8-point checklist before responding

  **Section 6: Output Format** (from `<output_format>`, all 8 sections)
  Preserve the exact per-finding schema:
  - `### [SEVERITY] <title>` with ID (F-NNN), File (path:line), Category, Problem, Evidence, Suggested Fix (diff), Directive for Developer Agent
  - Section 8: Handover JSON with exact schema including: `mr_id`, `verdict`, `reviewed_at`, `reviewer` ("DrupalSentinel"), `target_agent` ("drupal_developer"), `summary`, `metrics`, `findings[]`, `non_issues[]`, `missing_artifacts[]`, `tech_debt[]`, `praise[]`, `verification_commands[]`, `next_actions[]`, `acceptance_criteria_for_resubmission[]`

  **Section 7: Anti-Patterns** (from `<anti_patterns>`)
  - Refuse/warn list: approve unseen code, lower BLOCKER severity, secrets/PII in diff, modifying web/core/ or contrib, disabling security modules, TODO comments for security/caching
  - Do-NOT list: rewrite entire files, flag pre-existing issues as blockers, block for style preference

  **Section 8: Interaction Style** (from `<interaction_style>`)
  - Rigorous, direct, unambiguous
  - Cite evidence for every claim
  - Never approve to be polite, never request changes to seem thorough
  - If can't form verdict → COMMENT_ONLY with QUESTION findings

  **Section 9: Environment Context** (from `<environment_context>`)
  - Placeholders: `{{ core_version }}`, `{{ php_version }}`, `{{ hosting }}`, `{{ shell }}`, `{{ key_contrib }}`, `{{ ci_pipeline }}`, `{{ compliance }}`
- **MIRROR**: `prompts/overlays/drupal_developer.md` for overlay header format ("# Drupal Reviewer Overlay — DrupalSentinel")
- **SOURCE**: `prompts/drupal_reviewer_perplexity.md` for content
- **GOTCHA**: Keep the output format specification precise — the agent parses the handover JSON. Use the exact JSON schema from the perplexity prompt. Include `{{ }}` placeholders that match the config keys.
- **VALIDATE**: File exists, contains "Drupal Reviewer Overlay", contains all 11 review dimensions, contains `{{ core_version }}`

### Task 2: UPDATE `config/config.yaml` — Add drupal_reviewer config

- **ACTION**: Add `drupal_reviewer` agent configuration section
- **IMPLEMENT**: Add after `security_review` block (after line 37):
  ```yaml
  drupal_reviewer:
    model: claude-4-5-sonnet
    temperature: 0.1
    veto_power: true
    environment:
      core_version: "11.1.3"
      php_version: "8.3"
      hosting: "Lando"
      shell: "fish"
      key_contrib: "paragraphs, webform, search_api"
      ci_pipeline: "GitLab CI"
      compliance: "GDPR, WCAG 2.2 AA"
  ```
- **MIRROR**: `config/config.yaml:33-37` (security_review pattern) + `config/config.yaml:25-32` (drupal_developer environment pattern)
- **GOTCHA**: Environment keys must match the `{{ }}` placeholders in the overlay (including `shell`). Keep values identical to drupal_developer environment since they describe the same project.
- **VALIDATE**: `python3 -c "import yaml; yaml.safe_load(open('config/config.yaml'))"`

### Task 3: CREATE `src/agents/drupal_reviewer.py`

- **ACTION**: Create the DrupalReviewerAgent class
- **IMPLEMENT**:
  - Extend `ReviewAgent` with `agent_name="drupal_reviewer"`, `model="claude-4-5-sonnet"`, `temperature=0.1`, `veto_power=True`
  - Copy `_load_stack_overlay()` from DrupalDeveloperAgent, pointing to `prompts/overlays/drupal_reviewer.md`
  - Copy `_inject_environment_context()` from DrupalDeveloperAgent, reading from `agents.drupal_reviewer.environment`
  - Implement `_get_changed_files(worktree_path, default_branch)` — copy from SecurityReviewerAgent
  - Implement `_get_diff_content(worktree_path, default_branch)` — git diff producing unified diff string for LLM context
  - Implement `_read_changed_file_contents(changed_files)` — read file contents for LLM context (size-capped)
  - Implement `_build_review_prompt(diff_content, file_contents, ticket_context)` — construct the user message with diff and context
  - Implement `_parse_review_response(response)` — the LLM returns a multi-section markdown response (Sections 1-8); extract the JSON from Section 8 ("Handover") by finding the ```json fenced block. Parse it for `verdict`, `findings[]`, `metrics`, and other fields. If JSON extraction fails (no fenced block, malformed JSON), fall back to regex scanning for `[BLOCKER]`/`[MAJOR]` patterns in the markdown body.
  - Implement `provide_feedback(findings)` — generate human-readable summary grouped by severity (BLOCKER → MAJOR → MINOR → NIT → QUESTION → PRAISE), mirroring `SecurityReviewerAgent.provide_feedback()`
  - Implement `approve_or_veto(findings)` — per the Perplexity approach: any BLOCKER → REQUEST_CHANGES (veto); any MAJOR → REQUEST_CHANGES (veto); only MINOR/NIT/QUESTION/PRAISE → APPROVE. This is stricter than SecurityReviewerAgent's threshold model, matching the Perplexity reviewer's stance: "No blockers, no majors. Minor/nit issues only." → APPROVE.
  - Handle COMMENT_ONLY verdict — if the LLM response contains `COMMENT_ONLY` verdict (used for WIP/draft MRs or when context is insufficient), treat as informational: set `approved=True` (non-blocking) but include findings for developer awareness. Log that the review is informational only.
  - Implement `run(worktree_path, **kwargs)` — orchestrate: get diff → build prompt → call LLM → parse response → verdict → return result dict matching SecurityReviewerAgent shape
- **MIRROR**: `src/agents/security_reviewer.py:23-37` for `__init__`; `src/agents/drupal_developer.py:48-68` for overlay+env; `src/agents/security_reviewer.py:53-124` for git file scoping; `src/agents/security_reviewer.py:664-719` for `run()` return shape
- **IMPORTS**: `ReviewAgent` from `base_agent`, `logging`, `subprocess`, `re`, `json`, `Path` from `pathlib`, `typing`
- **GOTCHA**: The `run()` return dict MUST have `approved`, `findings`, `feedback`, `veto` keys for CLI compatibility. The LLM call uses `self.sdk.execute_with_tools()` inherited from BaseAgent. Response parsing must handle malformed JSON gracefully (the LLM may not always produce perfect JSON). The handover JSON `target_agent` field should be `"drupal_developer"` (our agent name), not `"DrupalForge"` (the Perplexity prompt's name for the developer agent). Adapt all Perplexity references to DrupalForge → drupal_developer in the overlay.
- **VALIDATE**: `python3 -c "from src.agents.drupal_reviewer import DrupalReviewerAgent"` — import must succeed

### Task 4: CREATE `tests/test_drupal_reviewer.py`

- **ACTION**: Create comprehensive test suite
- **IMPLEMENT**:
  - **TestDrupalReviewerAgent class:**
    - `test_init` — verify agent_name, model, temperature, veto_power
    - `test_init_loads_overlay` — verify overlay content in system_prompt
    - `test_init_injects_environment_context` — verify `{{ }}` placeholders replaced (mirror `tests/test_drupal_developer.py:460-504`)
    - `test_init_handles_missing_environment_config` — no crash when env config absent
  - **TestReviewLogic class:**
    - `test_approve_with_no_blockers_no_majors` — findings with only MINOR/NIT/PRAISE → approved
    - `test_veto_with_blocker` — findings with BLOCKER → not approved
    - `test_veto_with_major` — findings with any MAJOR → not approved (Perplexity: "No blockers, no majors → APPROVE")
    - `test_comment_only_is_non_blocking` — COMMENT_ONLY verdict (WIP/draft) → approved=True, findings present for awareness
  - **TestGetChangedFiles class:**
    - `test_get_changed_files_returns_paths` — mock git subprocess, verify file list
    - `test_get_changed_files_returns_none_on_failure` — git failure → None
  - **TestParseReviewResponse class:**
    - `test_parse_valid_json_response` — well-formed response → findings list
    - `test_parse_malformed_response` — graceful fallback on bad JSON
  - **TestRunWorkflow class:**
    - `test_run_full_approve_workflow` — mock LLM, verify approved result
    - `test_run_full_veto_workflow` — mock LLM with blockers, verify vetoed result
  - **TestProvideFeedback class:**
    - `test_feedback_groups_by_severity` — verify output ordering and content
- **MIRROR**: `tests/test_drupal_developer.py:14-29` for fixtures; `tests/test_security_reviewer.py` for review agent test patterns
- **GOTCHA**: Mock `self.sdk.execute_with_tools` for LLM calls. Use `patch("src.agents.base_agent.get_config")` pattern for config mocking with `side_effect` for environment context tests.
- **VALIDATE**: `python3 -m pytest tests/test_drupal_reviewer.py -v`

### Task 5: UPDATE `src/cli.py` — Import DrupalReviewerAgent

- **ACTION**: Add import for the new agent
- **IMPLEMENT**: Add to the import block (after line 23):
  ```python
  from src.agents.drupal_reviewer import DrupalReviewerAgent
  ```
- **MIRROR**: `src/cli.py:23` — follows existing import pattern
- **VALIDATE**: `python3 -c "import src.cli"`

### Task 6: UPDATE `src/cli.py` — Insert Drupal review step

- **ACTION**: Add Drupal review step after security review passes
- **IMPLEMENT**: After line 706 (`break` after security approved), replace the `break` with:
  ```python
  if sec_result["approved"]:
      click.echo("      ✅ Security review PASSED")

      # Drupal-specific code review (only for Drupal stacks)
      if stack_type and stack_type.startswith("drupal"):
          drupal_reviewer = DrupalReviewerAgent()
          click.echo("   🔍 Drupal Review: Reviewing code...")
          drupal_result = drupal_reviewer.run(
              worktree_path=worktree_path,
              ticket_id=ticket_id,
          )

          if drupal_result["approved"]:
              click.echo("      ✅ Drupal code review PASSED")
              break
          else:
              issues_count = len(drupal_result.get("findings", []))
              blocker_count = sum(
                  1 for f in drupal_result.get("findings", [])
                  if f.get("severity") == "BLOCKER"
              )
              click.echo(
                  f"      ⚠️  Found {issues_count} issues "
                  f"({blocker_count} blockers)"
              )
              if iteration < max_iterations:
                  click.echo("      ↻  Developer will address Drupal review feedback...")
              else:
                  click.echo(
                      "\n❌ Max iterations reached without Drupal review approval",
                      err=True,
                  )
                  click.echo("   Manual review required. Check Drupal review findings.")
                  sys.exit(1)
      else:
          break
  ```
- **MIRROR**: `src/cli.py:700-716` — existing security review block structure
- **GOTCHA**: The `break` on line 706 must be replaced, not duplicated. The non-Drupal path (`else: break`) preserves existing behavior for Python stacks. The `DrupalReviewerAgent()` is instantiated inside the conditional to avoid unnecessary construction for non-Drupal stacks.
- **VALIDATE**: `python3 -c "import src.cli"` — no import errors; review the diff to confirm the control flow is correct

### Task 7: UPDATE `config/config.yaml` — Verify environment keys match overlay placeholders

- **ACTION**: Cross-check that config environment keys match the `{{ }}` placeholders in the overlay
- **IMPLEMENT**: Read `prompts/overlays/drupal_reviewer.md`, extract all `{{ key }}` patterns, verify each has a matching key in `config/config.yaml` under `drupal_reviewer.environment`
- **VALIDATE**: Manual verification — all placeholders have corresponding config keys

### Task 8: End-to-end validation

- **ACTION**: Run full test suite and static checks
- **IMPLEMENT**: Run all validation commands
- **VALIDATE**: See Validation Commands section below

---

## Testing Strategy

### Unit Tests to Write

| Test File | Test Cases | Validates |
|-----------|------------|-----------|
| `tests/test_drupal_reviewer.py` | `test_init`, `test_init_loads_overlay`, `test_init_injects_environment_context`, `test_init_handles_missing_environment_config` | Agent initialization and prompt assembly |
| `tests/test_drupal_reviewer.py` | `test_approve_with_no_blockers_no_majors`, `test_veto_with_blocker`, `test_veto_with_major`, `test_comment_only_is_non_blocking` | Verdict logic |
| `tests/test_drupal_reviewer.py` | `test_get_changed_files_returns_paths`, `test_get_changed_files_returns_none_on_failure` | Git integration |
| `tests/test_drupal_reviewer.py` | `test_parse_valid_json_response`, `test_parse_malformed_response` | LLM response parsing |
| `tests/test_drupal_reviewer.py` | `test_run_full_approve_workflow`, `test_run_full_veto_workflow` | Full run() workflow |
| `tests/test_drupal_reviewer.py` | `test_feedback_groups_by_severity` | Feedback generation |

### Edge Cases Checklist

- [ ] Empty diff (no changed files) — should approve with note
- [ ] Git not available (worktree not a repo) — graceful fallback
- [ ] LLM returns malformed JSON — parse failure handled, findings empty, default to COMMENT_ONLY (non-blocking)
- [ ] LLM returns COMMENT_ONLY for WIP/draft MR — treated as informational, non-blocking
- [ ] Very large diff exceeding token limits — truncation strategy
- [ ] Mixed findings (BLOCKERs + PRAISE) — verdict correctly REQUEST_CHANGES
- [ ] Non-Drupal stack — review step entirely skipped
- [ ] Missing overlay file — agent initializes with base prompt only
- [ ] Missing environment config — placeholders remain or get "Not specified"

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
python3 -c "import py_compile; py_compile.compile('src/agents/drupal_reviewer.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('tests/test_drupal_reviewer.py', doraise=True)"
python3 -c "import yaml; yaml.safe_load(open('config/config.yaml'))"
```

**EXPECT**: Exit 0, no errors

### Level 2: UNIT_TESTS

```bash
python3 -m pytest tests/test_drupal_reviewer.py -v
```

**EXPECT**: All tests pass

### Level 3: REGRESSION

```bash
python3 -m pytest tests/test_security_reviewer.py tests/test_drupal_developer.py -v
```

**EXPECT**: Existing tests still pass (no regressions)

### Level 4: IMPORT_CHECK

```bash
python3 -c "from src.agents.drupal_reviewer import DrupalReviewerAgent; print('OK')"
python3 -c "import src.cli; print('OK')"
```

**EXPECT**: Both print "OK"

---

## Acceptance Criteria

- [ ] `DrupalReviewerAgent` extends `ReviewAgent` with `veto_power=True`
- [ ] Overlay loaded from `prompts/overlays/drupal_reviewer.md` with all 11 review dimensions
- [ ] Environment context injected from `config.yaml` → `{{ }}` placeholders replaced
- [ ] `run()` returns `{"approved", "findings", "feedback", "veto"}` matching SecurityReviewerAgent shape
- [ ] Verdict: any BLOCKER or any MAJOR → `REQUEST_CHANGES` (veto); only MINOR/NIT/QUESTION/PRAISE → `APPROVE`; COMMENT_ONLY (WIP/draft) → non-blocking pass with findings
- [ ] CLI execute loop runs Drupal review after security passes (Drupal stacks only)
- [ ] Non-Drupal stacks unaffected (existing `break` path preserved)
- [ ] All new tests pass
- [ ] All existing tests pass (no regressions)
- [ ] Config YAML valid
- [ ] Overlay file contains structured output format with handover JSON schema
