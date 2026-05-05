# Sentinel Performance Analysis — 2026-04-30

## Context

Reported symptom: running `sentinel execute` on a ticket whose plan amounts to "enable a Drupal module" takes roughly **30 minutes**. The figure is unmeasured — no instrumentation currently attributes wall-clock time to phases — so this document is a static-analysis hypothesis, not a profiled result.

Scope of the investigation: the `execute` flow end-to-end, from CLI entry through developer/reviewer loops to post-execute cleanup. Read-only review of `/workspace/sentinel/src/`; no code changes.

## Headline finding: nothing is instrumented

There is no phase-level timing, no per-task duration log, and no end-of-run summary. `src/agent_sdk_wrapper.py:432-503` records some LLM-call timing internally, but it is not aggregated or surfaced. Every optimization hypothesis below is unverified until basic instrumentation exists — that gap is itself the first thing to fix.

## Flow walkthrough

1. `sentinel execute TICKET-ID` — `src/cli.py:774` — dispatches to `run_execute()`.
2. Workflow orchestration — `src/core/execution/workflows.py:220` — performs:
   - Environment bootstrap via `EnvironmentManager.setup()` (`workflows.py:284-290`) — `docker compose up`, image pulls, initial `composer install`.
   - Iteration loop, up to `max_iterations` (default 5):
     - `DrupalDeveloperAgent.run()` (`workflows.py:329`) → breaks plan into tasks (LLM call) → for each task, calls `implement_feature()` (`src/agents/base_developer.py:775`), which sends a TDD prompt, runs `composer install`, and runs `vendor/bin/phpunit` via `docker compose exec`.
     - `SecurityReviewerAgent.run()` (`workflows.py:361`) — one LLM call.
     - Drupal-specific reviewer loop (`workflows.py:389-427`) — up to `drupal_attempts` iterations; can trigger a developer self-fix pass.

## Top 5 suspect hotspots

Ranked by likely wall-clock impact on a module-enable ticket.

### 1. `composer install` rerun on every task

**Location**: `src/agents/base_developer.py:480-494` (`_ensure_composer_deps`)
**Called from**: `_run_tests_in_container()` at `src/agents/base_developer.py:558`
**Estimated cost**: 8-15 min per ticket

```python
def _ensure_composer_deps(self) -> None:
    logger.info("Running composer install in container")
    result = self._env_manager.exec(
        ticket_id=self._env_ticket_id,
        service="appserver",
        command=["composer", "install", "--no-interaction", "--no-progress"],
        workdir="/app",
    )
```

Triggered per task execution. Drupal's ~300+ dependencies get a full resolve each call even though `composer.lock` is stable across tasks. No in-ticket reuse; no cross-ticket cache.

**Verify**: wrap the call in `time.perf_counter()` and count invocations in one run.

### 2. Serial LLM calls per task, no prompt caching

**Location**: `src/agents/base_developer.py:772-775` (task loop); `src/agent_sdk_wrapper.py:332-344` (SDK call site)
**Estimated cost**: 5-10 min per ticket

Static search for `prompt_caching` / `cache_control` against the codebase returns zero hits. System prompt (drupal_developer overlay + base prompt, ~8-15 KB) is sent in full on every task's LLM call. Tasks iterate with a plain `for task in tasks:` — no batching, no concurrency, no cache reuse.

**Verify**: count `[LLM] drupal_developer: sending request` lines in one run; measure per-call latency.

### 3. `docker compose exec` overhead

**Location**: `src/agents/base_developer.py:571-576`
**Estimated cost**: 2-5 min per ticket

Each exec call pays a 2-5 s handshake (Docker socket, overlay FS). 10+ invocations per ticket (tests, composer, drush) add up.

**Verify**: time a single `docker compose exec appserver true` in isolation, multiply by invocation count.

### 4. `drush site:install` treated as "config validation"

**Location**: `src/agents/drupal_developer.py:173-213`
**Estimated cost**: 2-4 min per ticket

`drush site:install minimal --config-dir=…` is a full database reinstall, not a cheap validation. It runs per iteration (`workflows.py:349-359`). If early iterations fail config validation, this alone can consume several minutes.

**Verify**: grep logs for `site:install` runs and time each.

### 5. Fresh environment bootstrap per ticket

**Location**: `src/core/execution/workflows.py:284-290`
**Estimated cost**: 1-2 min per ticket

New compose environment per ticket via DooD. First-run image pulls, DB init, and initial composer install happen before the first useful exec. No container reuse across tickets.

**Verify**: time from `env_mgr.setup()` return to first developer exec.

## Secondary observations

- **Plan breakdown is itself an LLM call** — `src/agents/base_developer.py:234-293`. A one-line "enable module X" plan still triggers an LLM extraction pass; a regex fallback exists at line 295 but only on LLM failure.
- **Drupal reviewer loop can re-run the developer** — `src/core/execution/workflows.py:388-427`. A second approval cycle means another developer pass through the task loop, with composer and tests again.
- **Tasks are strictly serial** — `src/agents/base_developer.py:772`. No async dispatch even for independent tasks.
- **Agent SDK internal timing is not reported** — `src/agent_sdk_wrapper.py:432-503` records durations but does not emit a summary.

## Hypothesis for the ~30-minute figure

Rough reconstruction for a 5-10 task module-enable run:

| Phase | Estimate |
|---|---|
| Env setup (compose up + first composer install) | 3-5 min |
| Developer iteration (plan breakdown + per-task TDD LLM + composer + phpunit) | 8-12 min |
| Config validation via `drush site:install` | 1-2 min |
| Security review + Drupal review + optional self-fix pass | 6-9 min |
| Misc (retries, rate limits, push/MR) | ~2 min |
| **Total** | **~20-30 min** |

Dominated by repeated `composer install` and serial uncached LLM roundtrips. The exact split is unverifiable until instrumentation lands.

## Recommended next step

Before optimizing, instrument. The cheapest useful intervention:

1. Wrap `_ensure_composer_deps`, `_run_tests_in_container`, `validate_config`, and each `send_message` LLM call with `time.perf_counter()` logs that emit a structured line (`phase=…, duration_s=…, ticket=…`).
2. Run one `sentinel execute` on a module-enable ticket with logs captured.
3. Aggregate durations by phase. That single run will confirm or falsify the ranking above and tell you whether the first fix is composer caching, prompt caching, drush avoidance, or something else entirely.

## Confidence and caveats

- Task counts, per-call durations, and composer runtime are estimates from static reading. No profiler was run.
- The ranking reflects likely impact, not certainty. Iteration counts in practice may be lower than the `max_iterations=5` ceiling, which would shrink the drush and reviewer-loop contributions.
- Items flagged as "secondary" (plan breakdown LLM call, serial task loop) are real but smaller levers than the top 5.
