# Drupal Config Sync Validation (CI Pipeline Blind Spot)

## Problem

Sentinel's execute flow validates code with PHPUnit only. The target project's CI pipeline runs `drush site-install` which performs **Drupal configuration import validation**. Sentinel-generated code passed unit tests but broke config sync, failing the pipeline.

## Actual failure (2026-04-18)

GitLab CI for DHL Express project failed during `drush site-install`:

```
Configuration field.field.node.content_page.field_ref_content depends on
paragraphs.paragraphs_type.pricing_block configuration that will not exist after import.

Same for: pricing_country, pricing_service_type
```

The developer agent created field config YAML files referencing paragraph types (`pricing_block`, `pricing_country`, `pricing_service_type`) but never created the corresponding `paragraphs.paragraphs_type.*.yml` config files. This is a **config dependency gap** — only caught by config import, not by unit tests.

## Root cause

- Drupal config entities have dependency declarations (`dependencies.config` in YAML)
- When config is imported, Drupal validates all dependencies exist
- Sentinel's developer agent doesn't understand or validate these dependency chains
- PHPUnit tests don't exercise config import

## Proposed fix: Add config validation step to execute flow

### Where it fits

In `BaseDeveloperAgent`, alongside `run_tests()`. The validation sequence should be:

1. Implement task (existing)
2. Run unit tests (existing)
3. **Run config validation (new)** — stack-specific
4. Security review (existing)

### Implementation

**`BaseDeveloperAgent`** — add abstract method:
```python
@abstractmethod
def validate_config(self, worktree_path: Path) -> dict:
    """Validate project configuration. Returns {success: bool, output: str}."""
```

**`DrupalDeveloperAgent`** — implement:
```python
def validate_config(self, worktree_path: Path) -> dict:
    # Run inside appserver container
    result = self._run_in_container(
        worktree_path,
        ["drush", "config:import", "--dry-run"],
    )
    return {"success": result.returncode == 0, "output": result.stdout}
```

**`PythonDeveloperAgent`** — no-op (or run type checking):
```python
def validate_config(self, worktree_path: Path) -> dict:
    return {"success": True, "output": "No config validation for Python stack"}
```

### Feed failures back to developer agent

If config validation fails, the error output should be included in the developer agent's prompt for the next iteration — same pattern as security findings feedback. The agent needs to see:
- Which config entities have broken dependencies
- What's missing (the `paragraphs.paragraphs_type.*` files)

### What NOT to do

- Don't run a full `drush site-install` — too slow and requires full DB setup
- `drush config:import --dry-run` is the right command — validates without applying
- Don't add this to security review — it's a build validation, not a security concern

## Related files

- `src/agents/base_developer.py` — `run()` method, task loop, `run_tests()`
- `src/agents/drupal_developer.py` — Drupal-specific implementation, `_run_in_container()`
- `src/cli.py` — execute loop, where test results are checked
- `.agents/plans/ralph-integration.md` — related plan for adding validation gates (lint/typecheck), this would be a third gate

## Context: related work on this branch

The `feature/execute-plan-2` branch already has:
- Output file filtering (prevents junk `.md`/`.txt` files and cross-stack contamination)
- Agent identity logging (disambiguates which agent is active in logs)
- Post-execution decision log comment on GitLab MR
- Container-aware test execution (`_run_in_container`)

The `_run_in_container()` method in `DrupalDeveloperAgent` already handles running commands inside the appserver — `drush config:import --dry-run` would use the same mechanism.

## Priority

High — this is a live, observed failure. Without this validation, every Sentinel execution for Drupal projects risks generating code that passes tests but breaks the CI pipeline.
