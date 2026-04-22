# Feature: Integrate Perplexity Drupal Developer Prompt into Sentinel

## Summary

Enrich the Drupal developer overlay (`prompts/overlays/drupal_developer.md`) with production-grade Drupal knowledge extracted from the collaboratively-built Perplexity prompt. Add environment context injection so the system prompt is populated with project-specific values (Drupal version, PHP version, etc.) from config at runtime.

## User Story

As the Sentinel Drupal developer agent
I want deep Drupal domain knowledge (security checklist, caching mandate, anti-patterns, self-review gates) baked into my system prompt
So that I produce production-grade, secure, standards-compliant Drupal code without relying on the plan to specify these constraints every time.

## Problem Statement

The current Drupal overlay (175 lines) covers file structure, module anatomy, TDD cycle, and 7 terse critical rules. It lacks:
- No security checklist (10 items in Perplexity prompt)
- No anti-pattern guards (explicit refusal list)
- No detailed caching mandate (only a one-liner)
- No accessibility standards (WCAG 2.2 AA)
- No code style standards (PSR-12, strict_types, typed properties)
- No architecture mandate beyond DI (plugins, entities, SDC, events)
- No environment context (Drupal version, PHP version affect API availability)
- No self-review quality gate

## Solution Statement

1. **Rewrite the overlay** incorporating Perplexity's high-value sections while preserving existing good content (file structure, module anatomy, TDD cycle, test structure, common patterns)
2. **Add environment context injection** to `drupal_developer.py` — template-substitute `{{ }}` placeholders with values from config
3. **Add environment fields** to `config.yaml` under `agents.drupal_developer.environment`

## Metadata

| Field            | Value                                                    |
| ---------------- | -------------------------------------------------------- |
| Type             | ENHANCEMENT                                              |
| Complexity       | MEDIUM                                                   |
| Systems Affected | Prompt overlay, DrupalDeveloperAgent, config.yaml        |
| Dependencies     | None (uses existing ConfigLoader and overlay load chain)  |
| Estimated Tasks  | 5                                                        |

---

## UX Design

### Before State
```
╔════════════════════════════════════════════════════════════════════════╗
║                    CURRENT PROMPT COMPOSITION                         ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  base_instructions.md   developer.md       overlay/drupal_dev.md     ║
║  ┌──────────────────┐  ┌──────────────┐   ┌──────────────────────┐   ║
║  │ Communication     │  │ Workflow     │   │ File structure       │   ║
║  │ Error handling    │──│ phases       │──▶│ Module anatomy       │   ║
║  │ Git operations    │  │ Code quality │   │ TDD cycle            │   ║
║  │ Escalation        │  │ Security     │   │ 7 critical rules     │   ║
║  │                   │  │ (generic)    │   │ Common patterns      │   ║
║  └──────────────────┘  └──────────────┘   └──────────────────────┘   ║
║                                                                       ║
║  GAPS: No security checklist, no anti-patterns, no caching detail,   ║
║        no a11y, no code style mandate, no env context injection      ║
╚════════════════════════════════════════════════════════════════════════╝
```

### After State
```
╔════════════════════════════════════════════════════════════════════════╗
║                    ENRICHED PROMPT COMPOSITION                        ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  base_instructions.md   developer.md       overlay/drupal_dev.md     ║
║  ┌──────────────────┐  ┌──────────────┐   ┌──────────────────────┐   ║
║  │ Communication     │  │ Workflow     │   │ DrupalForge identity │   ║
║  │ Error handling    │──│ phases       │──▶│ 8 operating princip. │   ║
║  │ Git operations    │  │ Code quality │   │ File structure  ✓    │   ║
║  │ Escalation        │  │ Security     │   │ Module anatomy  ✓    │   ║
║  │                   │  │ (generic)    │   │ Code style (PSR-12)  │   ║
║  └──────────────────┘  └──────────────┘   │ Architecture mandate │   ║
║                                            │ Caching (detailed)   │   ║
║                                ┌────────┐  │ Security checklist   │   ║
║                                │ config │  │ Accessibility (WCAG) │   ║
║                                │ .yaml  │  │ TDD cycle       ✓   │   ║
║                                │        │──│ Anti-patterns        │   ║
║                                │environ.│  │ Self-review gate     │   ║
║                                └────────┘  │ Env context (filled) │   ║
║                                            └──────────────────────┘   ║
║                                                                       ║
║  NEW: Security, anti-patterns, caching, a11y, code style,           ║
║       self-review gate, environment context from config               ║
╚════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes
| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| Overlay system prompt | 175 lines, basic rules | ~350 lines, comprehensive | Agent has deeper Drupal knowledge |
| Config | No environment fields | `agents.drupal_developer.environment` | Project-specific context available |
| Agent init | Load overlay only | Load overlay + inject env context | Prompt tailored to project |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `prompts/overlays/drupal_developer.md` | all (175) | File being REWRITTEN — preserve structure of kept sections |
| P0 | `prompts/drupal_developer_perplexity.md` | all (278) | SOURCE material — extract sections from here |
| P1 | `src/agents/drupal_developer.py` | 46-56 | `_load_stack_overlay()` — method to extend |
| P1 | `src/agents/drupal_developer.py` | 37-43 | `__init__` — understand init chain |
| P1 | `src/config_loader.py` | 90-109 | `get()` dot-notation — pattern to USE for config access |
| P1 | `src/config_loader.py` | 213-223 | `get_agent_config()` — how agent config is retrieved |
| P2 | `tests/test_drupal_developer.py` | 103-110 | Overlay init test — will need UPDATING |
| P2 | `config/config.yaml` | 18-24 | Current drupal_developer config — where to add `environment` |
| P2 | `prompts/developer.md` | all (196) | Understand what overlay supplements (DO NOT modify) |

---

## Patterns to Mirror

**CONFIG_ACCESS:**
```python
# SOURCE: src/config_loader.py:90-109
# COPY THIS PATTERN for dot-notation config access:
def get(self, key: str, default: Any = None) -> Any:
    keys = key.split(".")
    value = self._config
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default
    return value
```

**AGENT_CONFIG_ACCESS:**
```python
# SOURCE: src/agents/base_agent.py:42-46
# COPY THIS PATTERN for accessing config inside agents:
self.config = get_config()
agent_config = self.config.get_agent_config(agent_name)
```

**OVERLAY_LOADING:**
```python
# SOURCE: src/agents/drupal_developer.py:46-56
# MIRROR THIS PATTERN for overlay load + extend:
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

**TEST_FIXTURE_PATTERN:**
```python
# SOURCE: tests/test_drupal_developer.py:14-29
# MIRROR THIS PATTERN for mock setup:
@pytest.fixture
def mock_config():
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.2,
        }
        # ...
        mock.return_value = config
        yield config
```

---

## Files to Change

| File | Action | Justification |
|------|--------|--------------|
| `prompts/overlays/drupal_developer.md` | REWRITE | Enrich with Perplexity knowledge |
| `src/agents/drupal_developer.py` | UPDATE | Add `_inject_environment_context()` |
| `config/config.yaml` | UPDATE | Add `environment` sub-key |
| `tests/test_drupal_developer.py` | UPDATE | Fix overlay header check, add env context tests |

---

## NOT Building (Scope Limits)

- **No changes to `developer.md`** — workflow phases stay stack-agnostic
- **No changes to `base_instructions.md`** — generic instructions unchanged
- **No changes to `prompt_loader.py`** — existing loading mechanism works
- **No changes to `base_developer.py`** — base class unchanged
- **No changes to `_build_tdd_prompt()`** — runtime TDD prompt stays the same
- **No `/clarify` cross-agent endpoint** — existing escalation in base_instructions covers it
- **No output format templates** — Sentinel orchestrator handles result structure
- **No `hosting` field in config** — user explicitly rejected it; project-agnostic

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: REWRITE `prompts/overlays/drupal_developer.md`

- **ACTION**: Rewrite the overlay, incorporating Perplexity's high-value sections while preserving existing good content
- **SOURCE MATERIAL**: `prompts/drupal_developer_perplexity.md` — extract from `<role>`, `<operating_principles>`, `<technical_standards>`, `<anti_patterns>`, `<workflow>` (self-review only)
- **KEEP FROM EXISTING**: File structure (lines 5-18), Module anatomy table (lines 23-43), TDD cycle (lines 44-63), Test structure (lines 65-98), Validation commands (lines 100-113), Hook implementation (lines 125-140), Common patterns (lines 142-175)
- **ADD NEW SECTIONS** (from Perplexity):
  - **Identity**: Adapted DrupalForge persona (1 paragraph, from `<role>` lines 2-6)
  - **Operating Principles**: 8 principles (from `<operating_principles>` lines 18-45)
  - **Code Style**: PSR-12, strict_types, typed props (from `<technical_standards>` lines 48-56)
  - **Architecture Mandate**: DI, services, plugins, entities, SDC, events (from lines 58-81)
  - **Caching Requirements**: Detailed #cache mandate (from lines 83-89)
  - **Security Checklist**: 10 items (from lines 91-101)
  - **Accessibility**: WCAG 2.2 AA (from lines 103-108)
  - **Tooling**: Assumed tools list (from lines 110-121)
  - **Anti-Patterns**: Explicit refusal/warning list (from `<anti_patterns>` lines 248-261)
  - **Self-Review Checklist**: 9-item silent quality gate (from `<workflow>` lines 159-170)
  - **Environment Context**: Template with `{{ }}` placeholders for runtime injection
- **HEADER**: Change from `# Drupal Developer Overlay` to `# Drupal Developer Overlay — DrupalForge`
- **GOTCHA**: The test at `tests/test_drupal_developer.py:110` checks for `"Drupal Developer Overlay"` in the system prompt — the new header MUST still contain this substring. `"Drupal Developer Overlay — DrupalForge"` satisfies this.
- **VALIDATE**: Read the file, confirm all sections present, confirm header contains "Drupal Developer Overlay"

### Task 2: UPDATE `config/config.yaml` — add environment fields

- **ACTION**: Add `environment` sub-key under `agents.drupal_developer`
- **LOCATION**: `config/config.yaml` lines 18-24
- **IMPLEMENT**: Add after `specializations` list:
  ```yaml
  agents:
    drupal_developer:
      model: claude-4-5-sonnet
      temperature: 0.2
      specializations:
      - drupal
      - php
      - phpunit
      environment:
        core_version: "11.1.3"
        php_version: "8.3"
        local_dev: "Lando"
        key_contrib: "paragraphs, webform, search_api"
        theme: "custom Starterkit-based"
        ci_pipeline: "GitLab CI"
        compliance: "GDPR, WCAG 2.2 AA"
  ```
- **MIRROR**: Follows existing nested config pattern (e.g., `environment:` at lines 68-76 for container runtime)
- **GOTCHA**: No `hosting` field — user explicitly rejected it. Values are defaults; can be overridden in `config.local.yaml` per project.
- **VALIDATE**: `python -c "import yaml; yaml.safe_load(open('config/config.yaml'))"`

### Task 3: UPDATE `src/agents/drupal_developer.py` — add environment context injection

- **ACTION**: Add `_inject_environment_context()` method, call it after `_load_stack_overlay()`
- **LOCATION**: After line 56 (end of `_load_stack_overlay`)
- **IMPLEMENT**:
  1. Add `_inject_environment_context()` method that:
     - Reads `agents.drupal_developer.environment` dict from `self.config` (already available via `BaseAgent.__init__` at line 42)
     - Iterates over key-value pairs
     - Replaces `{{ key }}` in `self.system_prompt` with the config value
     - Falls back to "Not specified" for missing keys
     - Handles case where `environment` key doesn't exist in config (returns early)
  2. Call `self._inject_environment_context()` in `__init__` after `self._load_stack_overlay()`
- **MIRROR**: Config access via `self.config.get("agents.drupal_developer.environment", {})` — matches `config_loader.py:90-109` dot-notation pattern
- **GOTCHA**: `self.config` is already set by `BaseAgent.__init__()` (line 42) before `DrupalDeveloperAgent.__init__()` calls `_load_stack_overlay()`, so config is available. Also handle the `{{ }}` delimiters — use double-brace matching like `{{ core_version }}` with spaces for readability.
- **VALIDATE**: `cd /workspace/sentinel && python -c "from src.agents.drupal_developer import DrupalDeveloperAgent"` (import check only — full init requires mocking)

### Task 4: UPDATE `tests/test_drupal_developer.py` — fix overlay test, add env context tests

- **ACTION**: Update existing overlay test and add new tests for environment context injection
- **LOCATION**: `tests/test_drupal_developer.py`
- **CHANGES**:
  1. **Line 110** — `test_init_loads_overlay`: The check `"Drupal Developer Overlay"` still passes since the new header is `"Drupal Developer Overlay — DrupalForge"`. No change needed IF header kept as planned. Verify this.
  2. **Add test** `test_init_injects_environment_context`: Mock config to include `environment` dict, verify that `{{ core_version }}` is replaced with the configured value in `agent.system_prompt`
  3. **Add test** `test_init_handles_missing_environment_config`: Mock config without `environment` key, verify agent initializes without error and `{{ }}` placeholders remain (or are replaced with "Not specified")
- **MIRROR**: Follow existing test fixture pattern (`mock_config`, `mock_agent_sdk`, `mock_prompt`) from lines 14-58
- **GOTCHA**: The `mock_config` fixture returns a Mock where `config.get_agent_config.return_value` returns a dict — need to also mock `config.get()` for the dot-notation access used by `_inject_environment_context()`
- **VALIDATE**: `cd /workspace/sentinel && python -m pytest tests/test_drupal_developer.py -v`

### Task 5: VERIFY — End-to-end prompt composition check

- **ACTION**: Verify the full prompt composition chain works correctly
- **IMPLEMENT**:
  1. Run all existing tests: `cd /workspace/sentinel && python -m pytest tests/test_drupal_developer.py -v`
  2. Verify overlay file contains all expected sections (manual read)
  3. Verify config.yaml parses correctly
  4. Estimate token count of enriched overlay (~350 lines vs 175 original)
- **VALIDATE**: All tests pass, no regressions

---

## Testing Strategy

### Unit Tests to Write

| Test File | Test Cases | Validates |
|-----------|------------|-----------|
| `tests/test_drupal_developer.py` | `test_init_injects_environment_context` | Config values replace `{{ }}` placeholders |
| `tests/test_drupal_developer.py` | `test_init_handles_missing_environment_config` | Graceful fallback when no env config |

### Existing Tests to Verify (No Changes Expected)

| Test | What It Checks | Risk |
|------|----------------|------|
| `test_init_loads_overlay` | "Drupal Developer Overlay" in system_prompt | LOW — new header contains this substring |
| `test_get_test_command` | Returns phpunit command | NONE — no change |
| `test_get_test_stub` | Returns PHP test content | NONE — no change |
| `test_build_tdd_prompt_*` | TDD prompt content | NONE — `_build_tdd_prompt` unchanged |
| `test_run_tests_*` | Test execution | NONE — test infrastructure unchanged |
| `test_filter_output_files_*` | File filtering | NONE — `_VALID_EXTENSIONS` unchanged |

### Edge Cases Checklist

- [ ] Config has no `environment` key → agent starts without error
- [ ] Config has partial `environment` (e.g., only `core_version`) → present keys injected, others show "Not specified"
- [ ] Overlay file missing → existing warning log, agent continues (existing behavior)
- [ ] `config.local.yaml` overrides environment values → deep merge works (existing `_deep_merge`)

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && python -m py_compile src/agents/drupal_developer.py
```

**EXPECT**: Exit 0, no syntax errors

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel && python -m pytest tests/test_drupal_developer.py -v
```

**EXPECT**: All tests pass including new env context tests

### Level 3: FULL_SUITE

```bash
cd /workspace/sentinel && python -m pytest tests/ -v --tb=short 2>&1 | head -100
```

**EXPECT**: No regressions in other test files

### Level 4: CONFIG_VALIDATION

```bash
cd /workspace/sentinel && python -c "import yaml; c = yaml.safe_load(open('config/config.yaml')); print(c['agents']['drupal_developer']['environment'])"
```

**EXPECT**: Prints environment dict with all fields

---

## Rollback Strategy

Each file change is independent and reversible:
- **Overlay**: `git checkout prompts/overlays/drupal_developer.md`
- **Config**: Remove `environment:` block from `config.yaml`
- **Agent**: Remove `_inject_environment_context()` and its call in `__init__`
- **Tests**: Remove new test methods

No database changes, no schema migrations, no dependency additions.
