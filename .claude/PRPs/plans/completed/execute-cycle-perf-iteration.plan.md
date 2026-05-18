# Feature: Performance Iteration on `sentinel execute` (Data-Driven, Profile-First)

## Summary

User-observed problem: a small/easy ticket takes ~90 min via `sentinel execute` vs. ~20 min if a human did the equivalent code + local-tests work. A Stage-0 autopsy of `logs/agent_diagnostics.jsonl` (15 execute sessions across 3 DHLEXC tickets, April 20 – May 15, 2026) **already confirms** that the gap is not orchestration overhead — it is concentrated inside the developer agent's invocations: 10–18 calls per ticket × 5–7 min each, with 50–75 Claude API tool round-trips per call. This plan delivers (a) a permanent opt-in perf-instrumentation harness, (b) a baseline report folding in the autopsy data we already have, (c) targeted instrumentation for the four blind spots the autopsy can't see (per-tool wallclock, prompt-cache hit rate, container/test ops, per-API-call prefill vs. stream split), (d) a single fresh-run baseline against a representative small ticket, and (e) one follow-on plan file per data-confirmed hot path. Constraints honored: multi-agent (planner / dev / reviewer) stays, verifier-retry stays, per-ticket appserver container stays — fixes target overhead *inside* those structural pieces.

## User Story

As a Sentinel maintainer who sees `sentinel execute` take ~90 min on a small ticket vs. ~20 min done manually
I want a profile-first iteration that ranks the time-sinks inside the kept structural pieces (multi-agent, retry, container)
So that we attack the real overhead — developer per-call latency, tool roundtrip count, reviewer prompt bloat, plan regenerations — instead of guessed culprits, the next maintainer can re-measure trivially, and 3B/3C inherit a baseline-able foundation.

## Problem Statement

Concrete, data-driven evidence — autopsy of `/workspace/sentinel/logs/agent_diagnostics.jsonl` (15 sessions, 3 tickets, April–May 2026):

| Ticket-Session         | Wall (min) | Agent (min) | Gap (min) | Dev calls | Avg dev call | Tools / dev call |
| ---------------------- | ---------- | ----------- | --------- | --------- | ------------ | ---------------- |
| DHLEXC-384-S0          | 82         | 73          | 9         | 11        | 6.1 min      | ~45              |
| DHLEXC-384-S1          | 60         | 58          | 2         | 11        | 5.3 min      | ~44              |
| DHLEXC-384-S3          | 128        | 99          | 29        | 18        | 5.5 min      | 51               |
| DHLEXC-311-S3          | 41         | 41          | 0         | 5         | 8.2 min      | 74               |

**Confirmed by data**:

1. **Orchestration is NOT the bottleneck.** Median session "gap" (everything outside agent invocations: container start, composer install, test runs, file I/O, git ops) is 0–9 min — vs. 50–100 min spent inside agent invocations. *Source: `agent_diagnostics.jsonl`, sessions DHLEXC-384-S0/S1.*
2. **`drupal_developer` is 95–99% of agent wallclock.** Across 4 representative sessions: dev = 67–99 min, all other agents combined = 0–5 min.
3. **Each developer invocation makes 50–75 tool round-trips, 66% Bash.** From 8,728 tool_use records overall (8,019 from drupal_developer): Bash 5,263, Read 1,294, TodoWrite 676, Write 648, Edit 417. *Source: same JSONL.*
4. **Reviewer prompts are 600–820k characters per call**, vs. 27k for developer. 30× prefill cost per reviewer call. Even though reviewer wallclock is small (~3–5s for the response), the **token prefill cost is large** and possibly uncached.
5. **Plan generator runs 1–7× per ticket at 218–299s per call.** DHLEXC-311 had 7 plan_generator invocations (~28 min agent time).
6. **Existing telemetry has four blind spots.** `agent_diagnostics.jsonl` records `exec_start`, `exec_complete`, `tool_use` per agent — but it does NOT record:
   - Per-tool actual wallclock (the existing `elapsed_s` field on `tool_use` records is a timeline offset, not duration — verified: total tool elapsed exceeds total agent wallclock by 24×, an impossible ratio).
   - Prompt-cache hit/miss (model side). `cli_stderr.log` (84MB Anthropic SDK debug output) does NOT log `cache_creation_input_tokens` / `cache_read_input_tokens`.
   - Container start, `composer install`, test execution wallclock.
   - Per-API-request prefill (time-to-first-chunk) vs. streaming time.
   
   These four gaps are precisely what Stage A must instrument — without them, we cannot tell whether each developer call's 5–7 minutes is dominated by roundtrip latency, uncached prefill (every roundtrip pays full prefill if caching isn't working), model thinking, or tool-execution wallclock.

**Reduction**: the user's "90 min vs 20 min" reduces to a precise, testable hypothesis — *"each developer SDK invocation is doing ~50 round-trips at ~6s each (~5 min/call) and we don't know whether the dominant cost is uncached prefill, network latency, model thinking, or tool wallclock."* This plan exists to answer that question with data, then file targeted fix plans.

## Solution Statement

A four-stage iteration:

1. **Stage 0 — Autopsy (already complete).** The data above was extracted in this planning session. Task 0 below is to commit the autopsy script + baseline report so future iterations can reproduce it.
2. **Stage A — Instrument the four blind spots** (Tasks 1–6). Land an opt-in `SENTINEL_PERF=1` perf harness with `with timed("…"):` wrappers planted at the highest-leverage observation points across the *full* execute pipeline. Specifically: (i) extend `agent_sdk_wrapper.py` to record per-API-request token usage including `cache_read_input_tokens` / `cache_creation_input_tokens`, time-to-first-chunk, and per-tool actual wallclock as a separate JSONL event type; (ii) instrument `compose_runner.py` / `environment_manager.py` for container & composer ops; (iii) instrument the test-execution path; (iv) instrument the reviewer prompt assembler to surface what's in the 600–820k chars. Zero behavior change when `SENTINEL_PERF` is unset.
3. **Stage B — Fresh baseline on a representative small ticket** (Task 7). Run `SENTINEL_PERF=1 sentinel execute <small-ticket>`. Capture `logs/perf.jsonl`. Re-run the autopsy script against the new structured data. Append a **before** column to the baseline report. Outcome: ranked top-N hot paths *with the four blind spots filled in* and per-call cache hit rate proven (or disproven).
4. **Stage C — File one follow-on plan per confirmed hot path** (Task 8). Likely candidates the autopsy already strongly suggests (each to be confirmed/rejected by Stage B): reviewer-prompt slimming, prompt-cache wiring (if Stage B shows misses), reduce dev tool count via more-decisive-prompts, plan-generator caching across revise loops, parallelize reviewer with next dev iteration. **Each fix is a separate `/prp-plan` cycle**; this plan does NOT implement them.

The instrumentation persists past this iteration as a permanent, opt-in tool — useful for 3B/3C and future regression checks. No production cost when `SENTINEL_PERF` is unset.

## Metadata

| Field            | Value                                                                                                                                                                                                                                                                            |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Type             | ENHANCEMENT (perf — instrumentation + autopsy + baseline + decide-from-data follow-ons)                                                                                                                                                                                          |
| Complexity       | MEDIUM                                                                                                                                                                                                                                                                           |
| Systems Affected | `src/utils/`, `src/agent_sdk_wrapper.py`, `src/agents/{base_agent,base_developer,plan_generator,drupal_reviewer}.py`, `src/compose_runner.py`, `src/environment_manager.py`, `src/command_executor.py`, `src/cli.py` (execute command only), `tests/perf/`, `pyproject.toml` |
| Dependencies     | `pytest-benchmark ^4.0`, `py-spy ^0.3` (dev only). No new runtime deps.                                                                                                                                                                                                          |
| Estimated Tasks  | 9                                                                                                                                                                                                                                                                                |
| Hard order       | autopsy commit → utils/perf module → SDK wrapper extension → orchestrator/container/test seeds → fresh baseline run → report → fix-plan files                                                                                                                                    |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║              BEFORE — observed 90 min, root cause unmeasured                          ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌──────────────────┐                                                                ║
║   │  Maintainer      │  "small ticket takes 90 min, ~20 min manually — why?"          ║
║   └────────┬─────────┘                                                                ║
║            │                                                                          ║
║            ▼                                                                          ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐     ║
║   │  sentinel execute <ticket>                                                  │     ║
║   │     plan_generator (1–7×, ~4 min each)                                      │     ║
║   │     ↓                                                                       │     ║
║   │     drupal_developer (10–18×, ~5–7 min each)  ← 95-99% of agent time        │     ║
║   │       └─ each call: 50–75 tool roundtrips (66% Bash)                        │     ║
║   │     ↓                                                                       │     ║
║   │     drupal_reviewer (1–3×, prompt 600–820k chars)                           │     ║
║   │     ↓                                                                       │     ║
║   │     revise loop ↺                                                           │     ║
║   │                                                                             │     ║
║   │     Visible telemetry: agent_diagnostics.jsonl (start/complete/tool_use)    │     ║
║   │     INVISIBLE: per-tool wallclock, cache hits, container time, test time    │     ║
║   │                per-API prefill vs stream                                    │     ║
║   └─────────────────────────────────────────────────────────────────────────────┘     ║
║                                                                                       ║
║   PAIN_POINT: Cannot tell whether each developer call's 5–7 min is dominated by:      ║
║              (a) uncached prefill on every tool roundtrip                             ║
║              (b) network latency to Claude API                                        ║
║              (c) model thinking time                                                  ║
║              (d) tool wallclock (Bash/Edit/etc execution)                             ║
║   DATA_FLOW: 15k+ Claude API stream-starts logged, but no token-usage stats kept;     ║
║              orchestration emits events but doesn't time the gaps between agents.     ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                AFTER — opt-in perf harness + complete blind-spot coverage             ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌──────────────────┐                                                                ║
║   │  Maintainer      │  SENTINEL_PERF=1 sentinel execute <ticket>                     ║
║   └────────┬─────────┘                                                                ║
║            │                                                                          ║
║            ▼                                                                          ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐     ║
║   │  src/utils/perf.py  (NEW)                                                   │     ║
║   │     timed() ctx mgr → PerfRecorder → logs/perf.jsonl                        │     ║
║   └─────────────────────────────────────────────────────────────────────────────┘     ║
║                                                                                       ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐     ║
║   │  Instrumentation seeds (zero cost when disabled)                            │     ║
║   │   • SDK wrapper: per-API-request token usage (input/output/cache_read/      │     ║
║   │     cache_creation), time-to-first-chunk, full stream time, per-tool        │     ║
║   │     start_ts/end_ts as separate spans                                       │     ║
║   │   • Per-agent: prompt-load, set_project, postmortem injection, postmortem   │     ║
║   │     capture, structured-output parse                                        │     ║
║   │   • Reviewer prompt assembler: byte breakdown by section                    │     ║
║   │   • compose_runner / environment_manager: container start, image pull,      │     ║
║   │     composer install, drush operations                                      │     ║
║   │   • command_executor: test invocations + stdout capture                     │     ║
║   │   • Top-level execute span around the full orchestration                    │     ║
║   └─────────────────────────────────────────────────────────────────────────────┘     ║
║                                                                                       ║
║                                  │                                                    ║
║                                  ▼                                                    ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐     ║
║   │  tests/perf/autopsy.py  (NEW — permanent autopsy script)                    │     ║
║   │     Reads agent_diagnostics.jsonl AND logs/perf.jsonl                       │     ║
║   │     → bucket by execute-session (>30min gap = new session)                  │     ║
║   │     → rank top-N spans by total time + call count                           │     ║
║   │     → cache-hit-rate per agent                                              │     ║
║   │     → per-tool wallclock distribution                                       │     ║
║   │     → reviewer prompt section breakdown                                     │     ║
║   └─────────────────────────────────────────────────────────────────────────────┘     ║
║                                                                                       ║
║                                  │                                                    ║
║                                  ▼                                                    ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐     ║
║   │  .claude/PRPs/reports/execute-cycle-perf-baseline.md  (NEW)                 │     ║
║   │     • Stage 0 autopsy table (already populated this session)                │     ║
║   │     • Stage B fresh-run table (filled by Task 7)                            │     ║
║   │     • Cache hit rate per agent (was: unknown; now: %)                       │     ║
║   │     • Per-tool wallclock distribution (was: unknown; now: histogram)        │     ║
║   │     • Container/test/composer breakdown (was: unknown; now: per-span time)  │     ║
║   │     • Confirmed vs rejected suspects with numbers                           │     ║
║   │     • Recommended follow-on plans (≤5 entries)                              │     ║
║   │     • Per follow-on: predicted Δ + actual Δ (filled when fix lands)         │     ║
║   └─────────────────────────────────────────────────────────────────────────────┘     ║
║                                                                                       ║
║   USER_FLOW: maintainer reads baseline.md → sees ranked hot paths with hard            ║
║              numbers → opens 1–3 follow-on plan files → after each fix runs           ║
║              the same harness → "After" column populates with measured Δ.             ║
║   VALUE_ADD: optimization decisions data-driven; the four blind spots eliminated;     ║
║              regressions detectable; instrumentation pays for itself permanently.      ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                                | Before                                              | After                                                                                              | User Impact                                                       |
| ------------------------------------------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `sentinel execute <ticket>`                             | No perf data; existing JSONL has 4 blind spots      | `SENTINEL_PERF=1 sentinel execute <ticket>` writes structured spans covering all blind spots       | Operator can profile a real run on demand; default unchanged      |
| `tests/perf/autopsy.py`                                 | Did not exist (autopsy was ad-hoc python)            | Permanent reusable script: `python tests/perf/autopsy.py [path-to-jsonl]`                          | Re-running autopsy after any change is a single command           |
| `.claude/PRPs/reports/execute-cycle-perf-baseline.md`   | Did not exist                                       | Living before/after report; Stage 0 autopsy committed; Stage B baseline added by Task 7            | One place to read the system's perf state                         |
| `agent_diagnostics.jsonl`                               | exec_start / exec_complete / tool_use only          | Same events + new fields on exec_complete: `cache_read_input_tokens`, `cache_creation_input_tokens`, `input_tokens`, `output_tokens`, `time_to_first_chunk_s` | Existing autopsy script grows new columns; no breaking change     |
| Read/Edit/etc. tool boundaries                          | No per-tool wallclock                                | New `tool_complete` event type with `start_ts`, `end_ts`, `actual_elapsed_s`, `output_size_chars`  | Per-tool histogram becomes computable                             |

---

## Mandatory Reading

| Priority | File                                                            | Lines              | Why Read This                                                                                                                  |
| -------- | --------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| P0       | `src/agent_sdk_wrapper.py`                                      | 1–500              | The dominant timing surface; existing JSONL diagnostic pattern. **All Stage-A SDK extensions land here.** Read top-to-bottom.   |
| P0       | `src/agents/base_agent.py`                                      | 73–250             | `set_project()` (~9× per execute), per-agent prompt-load + SDK-call sequence; instrumentation seed                              |
| P0       | `src/agents/base_developer.py`                                  | all                | Verifier-retry loop (kept), postmortem capture, test invocation hand-off; instrumentation seeds for retry decision points       |
| P0       | `src/agents/drupal_reviewer.py`                                 | all                | Reviewer prompt assembly — needs `with timed("reviewer.assemble_prompt"):` plus per-section byte counters to explain 600–820k  |
| P0       | `src/agents/plan_generator.py`                                  | all                | Plan generation + revision triggers; instrument the regenerate decision                                                         |
| P0       | `src/compose_runner.py`                                         | all                | Container ops; instrument `up`, `down`, `exec`. **Cite line numbers in Task 4.**                                                |
| P0       | `src/environment_manager.py`                                    | all                | Image pulls, composer install, drush bootstrap                                                                                  |
| P0       | `src/command_executor.py`                                       | all                | Test/cmd execution path                                                                                                         |
| P0       | `src/cli.py`                                                    | (execute command)  | Top-level execute orchestration; outermost `with timed("execute.full"):` span goes here                                         |
| P1       | `logs/agent_diagnostics.jsonl`                                  | sample             | Real existing data; understand the existing schema before extending it                                                          |
| P1       | `tests/conftest.py`                                             | 101–131            | Existing `sqlite_mem_conn` fixture style                                                                                        |
| P1       | `.claude/PRPs/plans/completed/m5-preflight-time-budget.plan.md` | all                | Cooperative-deadline pattern; structured-warning convention to mirror                                                            |
| P1       | `.claude/PRPs/plans/completed/h5-revert-mr-detection-n-squared.plan.md` | all         | Plan-structure convention this plan mirrors                                                                                     |
| P2       | `pyproject.toml`                                                | 23–52              | Dev-deps + pytest config — where `pytest-benchmark` and `py-spy` go                                                             |
| P2       | `.claude/PRPs/plans/completed/execute-initial-flow-review-revise-loop.plan.md` | summary | Confirms revise-loop topology (planner → dev → reviewer → revise)                                                              |

**External Documentation:**

| Source                                                                                                                       | Section                                                                       | Why Needed                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| [Anthropic Messages API: usage object](https://docs.claude.com/en/api/messages#response-usage)                              | `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`      | Field names to capture from each API response in `agent_sdk_wrapper.py`                          |
| [Anthropic prompt caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching)                                 | "Cache hit detection"                                                         | How to compute hit rate from the usage fields (cache_read > 0 → hit; cache_creation > 0 → write)  |
| [claude-agent-sdk Python](https://github.com/anthropics/claude-agent-sdk-python)                                             | `query()` / streaming events                                                  | Where the per-message usage object surfaces in the SDK's stream                                  |
| [pytest-benchmark v4](https://pytest-benchmark.readthedocs.io/en/stable/usage.html)                                          | "Comparing past runs"                                                         | `--benchmark-save` / `--benchmark-compare` for before/after diffing                              |
| [py-spy README](https://github.com/benfred/py-spy)                                                                           | `record` / `top` modes                                                        | Ad-hoc flamegraphs when JSONL spans aren't fine-grained enough                                   |

**GOTCHA #1**: The Anthropic SDK's stream event for usage is the *final* `message_delta` (or `message_stop`) event — not the first chunk. To capture both time-to-first-chunk AND token counts, instrument the stream loop to record the timestamp of the first text-chunk and the usage object on the final event. Both must be recorded on the same `exec_complete` JSONL line.

**GOTCHA #2**: In `claude-agent-sdk`, the system prompt is sent on every `client.query()` call. Whether it gets cached depends on (a) the SDK passing `cache_control: {type: "ephemeral"}` markers, (b) the prompt being ≥1024 tokens at the marker, and (c) the same prompt being sent within the cache TTL (5 min). Stage B will reveal if any of those is broken — **do not assume caching is working**.

**GOTCHA #3**: `pyproject.toml` lists `claude-agent-sdk = "^0.1.20"`. Verify the streaming usage object is exposed in that version before instrumenting; if not, document the gap and use an alternative (e.g., parse from `cli_stderr.log` if the SDK debug output exposes it).

**GOTCHA #4**: `time.monotonic()` everywhere — never `time.time()`. Mirror M5 convention.

**GOTCHA #5**: The `agent_diagnostics.jsonl` `tool_use` records have an `elapsed_s` field that is **not per-tool wallclock** — it is timeline offset from `exec_start`. Verified by data: total tool elapsed exceeds total agent wallclock by 24×. Stage A introduces a new `tool_complete` event type with explicit `start_ts` and `end_ts` so per-tool wallclock becomes computable. Do NOT change the existing `tool_use` schema (back-compat).

**GOTCHA #6**: `SENTINEL_PERF` must be checked once at PerfRecorder initialization — not on every `timed()` call. Cache the boolean.

**GOTCHA #7**: Some agents emit hundreds of tool_use events per invocation. The `with timed("tool.<name>"):` wrappers must use atomic O_APPEND writes (one `f.write(json.dumps(...) + "\n")` per span) — NOT a buffered/batched recorder. Append writes <PIPE_BUF on Linux are atomic.

---

## Patterns to Mirror

**TIMING_AND_JSONL_LOGGING** — already the local convention:

```python
# SOURCE: src/agent_sdk_wrapper.py:358-380
sdk_start = time.monotonic()
logger.info(f"[{self.agent_name}] Client opened ({time.monotonic() - sdk_start:.1f}s), sending query ({len(prompt)} chars)...")
query_start = time.monotonic()
await client.query(prompt)
logger.info(f"[{self.agent_name}] Query sent ({time.monotonic() - query_start:.1f}s), waiting for response stream...")
total_elapsed = time.monotonic() - sdk_start
self._write_diagnostic("exec_complete", {
    "msg_count": msg_count,
    "tool_count": len(tool_uses),
    "total_elapsed_s": round(total_elapsed, 1),
    "session_id": final_session_id,
}, cwd=cwd)
```

**EXTENSION** — add to the SAME `_write_diagnostic("exec_complete", {...})` payload (back-compat: never remove fields, only add):

```python
# NEW FIELDS to add to exec_complete payload:
"input_tokens": usage.input_tokens,
"output_tokens": usage.output_tokens,
"cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
"cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
"time_to_first_chunk_s": round(first_chunk_t - query_start, 3),
"stream_duration_s": round(total_elapsed - (first_chunk_t - sdk_start), 3),
```

**NEW EVENT TYPE** — `tool_complete` (replaces the meaning of `elapsed_s` on the existing `tool_use`):

```python
# NEW EVENT — written immediately after each tool returns:
self._write_diagnostic("tool_complete", {
    "tool": block.name,
    "tool_index": tool_index,
    "start_ts": tool_start_iso,
    "end_ts": tool_end_iso,
    "actual_elapsed_s": round(tool_end - tool_start, 3),
    "output_size_chars": len(tool_result_text),
}, cwd=cwd)
```

**COOPERATIVE_DEADLINE_AND_STRUCTURED_WARNING** (M5 convention — for any perf budget added):

```python
# SOURCE: src/core/learning/outcome_sync.py:284-318
deadline = time.monotonic() + budget_s if budget_s is not None else None
for item in items:
    if deadline is not None and time.monotonic() >= deadline:
        logger.warning(
            "perf deadline reached: processed=%d/%d elapsed=%.1fs budget=%.1fs",
            processed, len(items), time.monotonic() - start, budget_s,
        )
        break
```

**LOGGER_NAMING**:

```python
# SOURCE: every module
import logging
logger = logging.getLogger(__name__)
```

---

## Files to Change

| File                                                           | Action  | Justification                                                                                                                |
| -------------------------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `src/utils/perf.py`                                            | CREATE  | `timed()` ctx mgr + `PerfRecorder` writing `logs/perf.jsonl`                                                                  |
| `src/utils/__init__.py`                                        | UPDATE  | Re-export `timed` only                                                                                                       |
| `src/agent_sdk_wrapper.py`                                     | UPDATE  | Capture per-API-request token usage (input/output/cache_read/cache_creation), time-to-first-chunk; emit new `tool_complete` |
| `src/agents/base_agent.py`                                     | UPDATE  | Wrap `set_project()`; record per-agent total wallclock split (prompt-load / SDK / parse)                                     |
| `src/agents/base_developer.py`                                 | UPDATE  | Wrap each verifier-retry iteration; record retry-trigger reason + cumulative retry count                                     |
| `src/agents/plan_generator.py`                                 | UPDATE  | Wrap plan generation + revise-trigger event so we can count regenerations                                                    |
| `src/agents/drupal_reviewer.py`                                | UPDATE  | Wrap prompt assembly with **per-section byte counter** (diff / plan / agent-outputs / system-prompt / dev-summary / etc.)    |
| `src/compose_runner.py`                                        | UPDATE  | Wrap `up` / `down` / `exec`; capture image-pull time separately                                                              |
| `src/environment_manager.py`                                   | UPDATE  | Wrap `composer install`, drush bootstrap, project clone                                                                       |
| `src/command_executor.py`                                      | UPDATE  | Wrap test invocations and other long-running commands; capture stdout/stderr size                                             |
| `src/cli.py` (execute command only)                            | UPDATE  | Outermost `with timed("execute.full", meta={"ticket": ticket_id}):` span around the whole orchestration                       |
| `tests/perf/__init__.py`                                       | CREATE  | New perf-test package                                                                                                        |
| `tests/perf/autopsy.py`                                        | CREATE  | Permanent autopsy script — reads `agent_diagnostics.jsonl` + `logs/perf.jsonl`; emits ranked report sections                  |
| `tests/perf/conftest.py`                                       | CREATE  | Fixtures: `enable_perf`, `mock_sdk_client`, `tmp_log_dir`                                                                     |
| `tests/perf/test_perf_module.py`                               | CREATE  | Unit tests for `src/utils/perf.py` itself                                                                                    |
| `tests/perf/test_autopsy_script.py`                            | CREATE  | Snapshot test: feed a fixed JSONL fixture, assert autopsy output matches a committed `.expected.txt`                          |
| `.claude/PRPs/reports/execute-cycle-perf-baseline.md`          | CREATE  | Living before/after report — Stage 0 autopsy already populated; Stage B baseline added by Task 7                              |
| `.claude/PRPs/reports/perf-data/autopsy-stage0.json`           | CREATE  | Frozen Stage-0 autopsy output (the data we already have); future runs compare against this                                    |
| `pyproject.toml`                                               | UPDATE  | Add `pytest-benchmark ^4.0`, `py-spy ^0.3` to dev deps; register `perf_baseline` marker                                       |
| `docs/agent-learning-from-feedback-HANDOVER.md`                | UPDATE  | One paragraph: "perf harness landed; baseline at .../execute-cycle-perf-baseline.md"                                          |

**No production behavior changes. No CLI surface changes. No migrations. All instrumentation opt-in via `SENTINEL_PERF=1`.**

---

## NOT Building (Scope Limits)

- **No optimizations committed in this plan.** This plan delivers (a) instrumentation, (b) baseline report, (c) Stage-0 autopsy commit, (d) follow-on plan files. Specific fixes — reviewer-prompt slim, prompt-cache wiring, dev-tool-count reduction, plan-cache, parallelize-reviewer-with-next-dev — are filed as separate plans by Task 8 only after Stage B confirms the hot path.
- **No structural changes to multi-agent architecture.** Planner, drupal_developer, drupal_reviewer all stay. No collapsing agents, no skipping reviewer for "small" tickets.
- **No changes to verifier-retry policy.** Phase 1's retry cap, postmortem-on-cap-out path, and structured retry behavior all stay. We instrument it; we don't tune it.
- **No changes to per-ticket appserver model.** No container reuse across tickets; no out-of-container test runs.
- **No always-on instrumentation.** Production runs pay zero cost; spans only record when `SENTINEL_PERF=1`.
- **No replacement of `agent_sdk_wrapper.py`'s existing diagnostic JSONL.** We *extend* its `exec_complete` payload (additive only) and add a new `tool_complete` event type. The existing `tool_use` event keeps its current schema for back-compat.
- **No CI integration of perf gates.** Adding regression detection in CI is a follow-on. This plan ensures the harness exists and runs locally.
- **No tracing inside Claude/SDK internals.** Top-of-call spans plus the API-response usage object only; we do not patch SDK internals.
- **No Phase 3B/3C work.** Out of scope.
- **No changes to event-bus persistence model.** The previous (now-discarded) plan focused on the learning subsystem. The autopsy proved that path is not the bottleneck for execute. Skip it here.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 0: COMMIT the Stage-0 autopsy as a permanent artifact

- **ACTION**:
  1. Save the Stage-0 autopsy data — a JSON file at `.claude/PRPs/reports/perf-data/autopsy-stage0.json` containing the per-session breakdown table (15 sessions × {ticket, sid, wall_s, agent_s, gap_total_s, n_invs, by_agent}) plus the global tool-distribution counts.
  2. Create `.claude/PRPs/reports/execute-cycle-perf-baseline.md` with:
     - **Methodology**: data source = `logs/agent_diagnostics.jsonl`, date window = 2026-04-20 → 2026-05-15, segmentation = >30 min gap → new session.
     - **Stage 0 — Autopsy findings (frozen 2026-05-18)**: the table from this plan's Problem Statement; the per-agent breakdown; the four blind spots.
     - **Stage A — Instrumentation seeds**: list (filled by Tasks 3–6).
     - **Stage B — Fresh baseline**: empty placeholder section (filled by Task 7).
     - **Stage C — Follow-on plans**: empty placeholder section (filled by Task 8).
- **MIRROR**: `.claude/PRPs/plans/completed/h5-revert-mr-detection-n-squared.plan.md` for report tone.
- **GOTCHA**: Use the autopsy data already extracted in this planning session — do NOT re-run the autopsy from scratch. The numbers in this plan's Problem Statement *are* the Stage-0 baseline.
- **VALIDATE**: `ls .claude/PRPs/reports/execute-cycle-perf-baseline.md .claude/PRPs/reports/perf-data/autopsy-stage0.json && jq '. | length' .claude/PRPs/reports/perf-data/autopsy-stage0.json`

### Task 1: CREATE `src/utils/perf.py` — instrumentation primitive

- **ACTION**: Create the module with these public surfaces:
  - `def is_enabled() -> bool` — caches `os.environ.get("SENTINEL_PERF") == "1"` on first call.
  - `@contextmanager def timed(span_name: str, *, meta: dict | None = None) -> Iterator[Span]` — yields a no-op when disabled. When enabled: captures `time.monotonic()` start/end; supports `span.add_meta(key, value)`; writes one JSONL record on `__exit__`. Always reraise exceptions, recording `meta["error"] = exc.__class__.__name__` first.
  - `def perf_log_path() -> Path` — `LOG_DIR / "perf.jsonl"` (use `/app/logs` if it exists, else `Path.cwd() / "logs"`, mirroring `agent_sdk_wrapper.py` resolution).
  - `def reset_for_tests() -> None` — clear the cached `is_enabled` flag (test-only).
- **IMPLEMENT**: JSONL record schema `{"ts", "span", "elapsed_s", "thread", "pid", "meta"}`. Atomic append writes — `f.write(json.dumps(record) + "\n")` per span; lazy directory creation on first enabled span.
- **MIRROR**: `src/agent_sdk_wrapper.py` — `_write_diagnostic` JSONL pattern + path resolution.
- **GOTCHA**: When disabled, the `with` body must add NO measurable overhead beyond the env-var-cached boolean check. Verified by Task 6's `test_perf_disabled_zero_overhead`.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/utils/perf.py && poetry run ruff check src/utils/perf.py`

### Task 2: UPDATE `src/utils/__init__.py` — re-export `timed`

- **ACTION**: Add `from src.utils.perf import timed` and `__all__ = [..., "timed"]`. Do NOT export `PerfRecorder` or other internals.
- **VALIDATE**: `poetry run python -c "from src.utils import timed; print(timed)"`

### Task 3: EXTEND `src/agent_sdk_wrapper.py` — usage stats + per-tool wallclock + first-chunk timing

This is the highest-value task. Split into three sub-edits.

- **3a) Capture per-message usage on the streaming loop.**
  - In the existing `async for msg in client.receive_response()` loop (around `agent_sdk_wrapper.py:358-429`), capture the final `message_delta` event's `usage` object. Most SDK versions surface this on `Message.usage` at stream end.
  - Add to the `exec_complete` payload (additive only): `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`. Default to `0` when absent (back-compat with older SDK versions).
- **3b) Capture time-to-first-chunk.**
  - Already a debug log line `Stream started - received first chunk` in cli_stderr.log. The wrapper code path that emits it is the right hook — set `first_chunk_t = time.monotonic()` on the first text-chunk event. Add `time_to_first_chunk_s` to `exec_complete` payload.
- **3c) Add a new `tool_complete` event with actual per-tool wallclock.**
  - The existing tool_use loop logs the *intent* of a tool call. Add a sibling diagnostic emit when the tool *result* is received — record `start_ts`, `end_ts`, `actual_elapsed_s = end - start`, `output_size_chars = len(result_text)`, `tool`, `tool_index`. Emit as `event="tool_complete"`.
  - Do NOT change the existing `tool_use` event schema. The new event lives alongside.
- **MIRROR**: existing `_write_diagnostic` calls in the same file; the tool-use logging already there at lines 380–400 of agent_sdk_wrapper.py.
- **GOTCHA #1**: The SDK's exact field name for cached tokens varies (`cache_read_input_tokens` vs `cache_read_tokens` in some versions). Use `getattr(usage, "cache_read_input_tokens", 0)` to be safe; document the SDK version checked.
- **GOTCHA #2**: `usage` may be `None` on streaming-error paths. Guard with `if usage is not None`.
- **GOTCHA #3**: The wrapper currently logs to a single `agent_diagnostics.jsonl`. Keep that file as the SOLE destination for these new fields; do NOT split into a second file.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && \
    poetry run mypy src/agent_sdk_wrapper.py && \
    poetry run ruff check src/agent_sdk_wrapper.py && \
    poetry run pytest tests/ -x --ignore=tests/perf
  ```
  EXPECT: existing tests pass unchanged. The wrapper extensions are additive; no test should observe a schema break.

### Task 4: PLANT `with timed():` seeds across the orchestrator + container + test paths

NO behavior changes. Each sub-edit is a wrapper around an existing block.

- **4a) `src/cli.py` execute command (outermost span)** — `with timed("execute.full", meta={"ticket": ticket_id}):` around the orchestration body of the `execute` Click command. This becomes the ground-truth wallclock anchor for every other span.
- **4b) `src/agents/base_agent.py:73-113`** — `with timed("base_agent.set_project", meta={"agent": self.agent_name}):` around the `set_project()` body.
- **4c) `src/agents/base_developer.py`** — wrap each verifier-retry iteration: `with timed("base_developer.verifier_iteration", meta={"attempt": attempt_n, "trigger": trigger_reason}):`. Read the existing retry loop carefully; do NOT widen its `try/except`.
- **4d) `src/agents/plan_generator.py`** — wrap `generate_plan()` body in `with timed("plan_generator.generate"):` and any `revise_plan()` entry-point in `with timed("plan_generator.revise", meta={"reason": reason}):`. The autopsy showed plan_generator runs 1–7× per ticket — counting per-reason gives us the regenerate-driver breakdown.
- **4e) `src/agents/drupal_reviewer.py`** — instrument prompt assembly. Wrap the prompt-builder in `with timed("drupal_reviewer.assemble_prompt") as span:` and inside, after each section is added, call `span.add_meta(f"section_{name}_chars", len(section_text))`. This is the **load-bearing** instrumentation that explains the 600–820k char prompts.
- **4f) `src/compose_runner.py`** — wrap each top-level method (`up`, `down`, `exec`, image-pull). Use span names `compose.up`, `compose.down`, `compose.exec`, `compose.pull_image`. Add `meta={"image": ...}` on pull.
- **4g) `src/environment_manager.py`** — wrap `composer install`, drush bootstrap, project clone in `env.composer_install`, `env.drush_bootstrap`, `env.clone`.
- **4h) `src/command_executor.py`** — wrap test invocations + any other long-running command runner in `cmd.exec`, with `meta={"cmd_kind": "phpunit"|"behat"|"drush"|...}`. Capture `stdout_size_chars` on completion.

- **MIRROR**: M5 cooperative-deadline pattern; existing logger init.
- **GOTCHA #1**: Do NOT plant `with timed():` inside high-cardinality (>1000 iterations) loops unless the body is non-trivial (>1ms).
- **GOTCHA #2**: Each `import` of `timed` goes at module top — never inside a function (defeats CPython import cache).
- **GOTCHA #3**: For 4e (reviewer prompt sections), if the prompt-builder is a single string concatenation, refactor minimally — extract sections into local variables FIRST, then add `span.add_meta()` calls. Do not change the resulting prompt's content; verify by capturing `len(final_prompt)` before and after the refactor.
- **VALIDATE** (one combined check):
  ```bash
  cd /workspace/sentinel && \
    poetry run mypy src/ && \
    poetry run ruff check src/ && \
    poetry run pytest tests/ -x --ignore=tests/perf
  ```
  EXPECT: all existing tests pass; no schema changes; no behavior changes.

### Task 5: UPDATE `pyproject.toml` — dev deps + perf marker

- **ACTION**:
  - `[tool.poetry.group.dev.dependencies]` += `pytest-benchmark = "^4.0"`, `py-spy = "^0.3"`.
  - `[tool.pytest.ini_options]` += `markers = ["perf_baseline: opt-in baseline run (slow; not part of default CI suite)"]`.
- **GOTCHA**: After this edit, `poetry lock --no-update && poetry install --with dev` is required. If sandbox PyPI is blocked, document and run from `sentinel-dev` per CLAUDE.md container topology.
- **VALIDATE**: `poetry lock --no-update && poetry install --with dev && poetry run pytest --markers | grep perf_baseline`

### Task 6: CREATE the perf test package + autopsy script

- **ACTION**:
  - `tests/perf/__init__.py`: empty.
  - `tests/perf/conftest.py`:
    - `enable_perf` fixture — `monkeypatch.setenv("SENTINEL_PERF","1")` + `perf.reset_for_tests()`.
    - `tmp_log_dir(tmp_path, monkeypatch)` — set perf log path to `tmp_path/perf.jsonl`.
  - `tests/perf/autopsy.py` — **the permanent autopsy script.** Reads `logs/agent_diagnostics.jsonl` and (if present) `logs/perf.jsonl`. Produces:
    - `--sessions` flag: list execute sessions (the table from the Problem Statement).
    - `--rank` flag: top-N spans by total time + call count, across both files.
    - `--cache-rate` flag: per-agent cache_read / (cache_read + cache_creation + input) ratio.
    - `--tool-hist` flag: per-tool wallclock histogram (from the new `tool_complete` events).
    - `--reviewer-prompt` flag: per-section byte breakdown (from `drupal_reviewer.assemble_prompt` span meta).
    - Default (no flags): all of the above.
    - Write output to stdout AND append a Markdown section to a `--report-out PATH` file if given.
  - `tests/perf/test_perf_module.py`:
    - `test_timed_disabled_is_noop` — no JSONL written.
    - `test_timed_writes_jsonl_when_enabled` — JSONL contains a record with the expected fields.
    - `test_timed_handles_concurrent_threads` — 4 threads × 100 spans → 400 records, all parseable, no truncation.
    - `test_timed_records_exception_meta` — `with timed("foo"): raise ValueError("x")` writes a record with `meta.error == "ValueError"` and re-raises.
  - `tests/perf/test_autopsy_script.py` — snapshot test: feed `tests/perf/fixtures/sample_diagnostics.jsonl` (a 50-line redacted slice of the real file) to autopsy.py, capture stdout, compare against `tests/perf/fixtures/sample_autopsy.expected.txt`. On mismatch, fail with diff and instructions to regenerate.
- **MIRROR**: `tests/conftest.py:101-131`; the existing autopsy logic written in this planning session (it is the seed for `autopsy.py`).
- **GOTCHA**: Mark all baseline-running tests `@pytest.mark.perf_baseline`. Default `pytest tests/` must NOT include them; opt-in via `pytest tests/perf/ -m perf_baseline`.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && \
    poetry run pytest tests/perf/test_perf_module.py tests/perf/test_autopsy_script.py -v && \
    poetry run python tests/perf/autopsy.py --sessions
  ```

### Task 7: CAPTURE Stage-B fresh baseline on a representative small ticket

This task IS the measurement; it is intentionally a manual + scripted hybrid because it depends on a real `sentinel execute` run.

- **ACTION**:
  1. Pick a small ticket (operator's choice; ideally one the user characterizes as "should be ~20 min manually"). Document the ticket ID in the report.
  2. Run: `SENTINEL_PERF=1 sentinel execute <TICKET-ID> 2>&1 | tee logs/autopsy/exec-stage-b-$(date +%Y%m%d-%H%M%S).log`. This is best done in `sentinel-dev` per CLAUDE.md container topology.
  3. After the run completes (or caps out), snapshot `logs/agent_diagnostics.jsonl` and `logs/perf.jsonl` to `.claude/PRPs/reports/perf-data/stage-b-<ticket>-<timestamp>/`.
  4. Run `python tests/perf/autopsy.py --report-out .claude/PRPs/reports/execute-cycle-perf-baseline.md` against the snapshot. The report's "Stage B" section is now populated with:
     - Wall vs agent vs gap (compared against Stage 0 averages).
     - Top-N spans across the **full execute pipeline** including container ops, tests, reviewer prompt sections.
     - Cache hit rate per agent (NEW data — not visible in Stage 0).
     - Per-tool actual wallclock histogram (NEW).
     - Reviewer prompt section breakdown (NEW).
  5. Manually edit the report's "Confirmed vs Rejected Suspects" section based on the data. For each *a priori* suspect (uncached prefill on every dev tool call, reviewer prompt bloat, plan-generator regenerations, container/composer overhead) — state whether the data confirms or rejects, with numbers.
- **GOTCHA #1**: A small ticket may still take ~90 min. Block out the time. Run with `nohup` or in a screen session.
- **GOTCHA #2**: If the ticket caps out (verifier-retry exhausted), that is **valid data** — capture it. The cap-out path is part of what we're measuring. Do not re-run hoping for a clean exit.
- **GOTCHA #3**: If `SENTINEL_PERF=1` reveals a bug in the instrumentation (e.g., a wrapper raises), document and fix in a fast-follow before re-running. Instrumentation bugs do NOT count as data.
- **VALIDATE** (review the report manually):
  ```bash
  ls -la .claude/PRPs/reports/execute-cycle-perf-baseline.md \
         .claude/PRPs/reports/perf-data/stage-b-*/ \
         logs/perf.jsonl
  ```

### Task 8: FILE one follow-on plan per data-confirmed hot path

- **ACTION**: For each row in the report's "Confirmed Hot Paths" table, create `.claude/PRPs/plans/perf-fix-{slug}.plan.md` with:
  - **Problem statement**: the measured number from Stage B (e.g., "85% of dev SDK roundtrips have `cache_read_input_tokens=0`, paying full prefill on each — measured 4.1 min/call attributed to repeated prefill").
  - **Proposed fix**: one specific change, with predicted Δ.
  - **Acceptance**: re-run the harness; measured Δ within ±25% of prediction.
  - **Reference**: this plan + the relevant baseline section.
- **GOTCHA**: This task does NOT implement fixes. It files plans. Each fix is a separate `/prp-implement` cycle.
- **GOTCHA**: If the report's Confirmed Hot Paths list is empty or the slowness sits in agent-internal model thinking (which we can't optimize from outside the SDK), Task 8 produces zero files and the task is satisfied by documenting that finding in the report's "No actionable fix recommended" section.
- **EXPECTED PROBABLE FOLLOW-ONS** (each conditional on Stage B data confirming it):
  - `perf-fix-prompt-cache-wiring.plan.md` — wire `cache_control: {type: "ephemeral"}` markers into the developer's system prompt + tool definitions, IF Stage B shows cache_read_input_tokens is zero or near-zero.
  - `perf-fix-reviewer-prompt-slim.plan.md` — drop the largest section(s) of the reviewer prompt, IF Stage B's per-section breakdown shows one section >300k chars and is mostly redundant context.
  - `perf-fix-plan-cache.plan.md` — cache plan_generator output across revise loops by content hash, IF Stage B shows ≥3 plan_generator regenerations per ticket and the regenerate triggers are deterministic.
  - `perf-fix-dev-tool-decisiveness.plan.md` — prompt-engineer the developer to batch reads / chain bash commands, IF Stage B's per-tool histogram shows >50 Bash calls/invocation with high redundancy (e.g. many `ls` / `cat`).
- **VALIDATE**: `ls .claude/PRPs/plans/perf-fix-*.plan.md` matches the report's list (or is empty + documented).

### Task 9: UPDATE HANDOVER + final acceptance

- **ACTION**:
  - Append a "Performance Iteration" section to `docs/agent-learning-from-feedback-HANDOVER.md` referencing the baseline report and the follow-on plan list.
  - Run the full quality gate:
    ```bash
    cd /workspace/sentinel && \
      poetry run ruff check src/ tests/ && \
      poetry run mypy src/ && \
      poetry run pytest tests/ -x --ignore=tests/perf
    ```
- **VALIDATE**: All commands exit 0. HANDOVER reflects current state.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                      | New Test Cases                                                                                         | Validates                                                                            |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `tests/perf/test_perf_module.py`               | disabled-is-noop, jsonl-when-enabled, concurrent-threads, exception-meta                               | `src/utils/perf.py` correctness                                                      |
| `tests/perf/test_autopsy_script.py`            | snapshot test against fixed JSONL fixture                                                              | autopsy script stable across invocations                                             |
| `tests/test_agent_sdk_wrapper_perf.py` (root)  | usage-fields-recorded-when-present, usage-fields-default-zero-when-absent, tool_complete-event-emitted | SDK wrapper extension is back-compat additive                                        |

### Edge Cases Checklist

- [ ] `SENTINEL_PERF` unset → no `logs/perf.jsonl` is created; existing tests pass unchanged.
- [ ] `SENTINEL_PERF=0` → also disabled (only `=1` enables).
- [ ] Log directory does not exist → created lazily on first enabled span.
- [ ] Concurrent threads → atomic O_APPEND writes prevent JSONL corruption.
- [ ] Exception inside `with timed(...)` → span recorded with `meta.error`; exception re-raised unchanged.
- [ ] Older `claude-agent-sdk` lacking `cache_read_input_tokens` field → wrapper records `0` via `getattr(..., "cache_read_input_tokens", 0)`.
- [ ] `usage` is `None` on stream error → wrapper does not crash; defaults all token fields to `0`.
- [ ] Tool returns very large output (> 1MB) → `output_size_chars` recorded honestly; no truncation in the perf record.
- [ ] `pytest tests/ --ignore=tests/perf` still works — perf tests isolated.
- [ ] Stage-B run caps out via verifier-retry → autopsy script still produces a coherent report.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && \
  poetry run ruff check src/ tests/ && \
  poetry run mypy src/
```

**EXPECT**: Exit 0. Note: `mypy` runs only on `src/` per project convention (`pyproject.toml`).

### Level 2: UNIT_TESTS (default suite — must NOT regress)

```bash
cd /workspace/sentinel && \
  poetry run pytest tests/ -x --ignore=tests/perf
```

**EXPECT**: All pre-existing tests pass without modification.

### Level 3: PERF_HARNESS_SMOKE

```bash
cd /workspace/sentinel && \
  poetry run pytest tests/perf/test_perf_module.py tests/perf/test_autopsy_script.py -v && \
  poetry run python tests/perf/autopsy.py --sessions
```

**EXPECT**: Module unit tests green; snapshot test green; autopsy script lists the 15 historical sessions from existing data.

### Level 4: STAGE_B_FRESH_RUN (manual / one-shot)

```bash
SENTINEL_PERF=1 sentinel execute <TICKET-ID> 2>&1 | tee logs/autopsy/exec-stage-b-$(date +%Y%m%d-%H%M%S).log
```

**EXPECT**: `logs/perf.jsonl` populated; `logs/agent_diagnostics.jsonl` updated with new `tool_complete` events and extended `exec_complete` payload (cache fields). Run from `sentinel-dev` per CLAUDE.md.

### Level 5: BROWSER_VALIDATION

N/A — no UI surface.

### Level 6: MANUAL_VALIDATION

1. From a clean tree: `unset SENTINEL_PERF && poetry run sentinel --help` exits 0; no `logs/perf.jsonl` created.
2. Open `.claude/PRPs/reports/execute-cycle-perf-baseline.md` — confirm Stage 0 numbers match the Problem Statement of this plan; confirm Stage B section is populated after Task 7 with cache-hit %, per-tool histogram, reviewer prompt section breakdown.
3. Confirm Task 8's follow-on plan files (or "no fix needed" outcome) reference specific numbers from Stage B, not generic suspects.

---

## Acceptance Criteria

- [ ] `src/utils/perf.py` implements `is_enabled`, `timed`, `perf_log_path`, `reset_for_tests`.
- [ ] `src/agent_sdk_wrapper.py` extended: `exec_complete` records `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `time_to_first_chunk_s`. New `tool_complete` event records `start_ts`, `end_ts`, `actual_elapsed_s`, `output_size_chars`. **Existing `tool_use` schema unchanged (back-compat).**
- [ ] All Task 4 sub-edits planted (orchestrator outermost span, base_agent, base_developer retry, plan_generator, drupal_reviewer with per-section meta, compose_runner, environment_manager, command_executor).
- [ ] `SENTINEL_PERF` unset → zero JSONL output, zero behavior change, default test suite passes.
- [ ] `tests/perf/` package runnable; module unit tests + snapshot test green.
- [ ] `tests/perf/autopsy.py` reads existing `logs/agent_diagnostics.jsonl` and reproduces the Stage-0 table from the Problem Statement.
- [ ] Stage-B fresh run completed; `.claude/PRPs/reports/execute-cycle-perf-baseline.md` populated with Stage A + Stage B sections including cache hit rate, per-tool histogram, reviewer prompt section breakdown.
- [ ] One follow-on `perf-fix-*.plan.md` filed per confirmed hot path (or explicit "none warranted" documented).
- [ ] HANDOVER updated. Levels 1, 2, 3 validation commands exit 0.

---

## Completion Checklist

- [ ] Task 0: Stage-0 autopsy committed; baseline report scaffolded.
- [ ] Task 1: `src/utils/perf.py` created.
- [ ] Task 2: `timed` re-exported via `src/utils/__init__.py`.
- [ ] Task 3: `agent_sdk_wrapper.py` extensions land (3a + 3b + 3c).
- [ ] Task 4: All 8 instrumentation seeds planted; existing tests still pass.
- [ ] Task 5: dev deps + `perf_baseline` marker.
- [ ] Task 6: `tests/perf/` package + autopsy script + unit + snapshot tests.
- [ ] Task 7: Stage-B fresh run captured; baseline report populated.
- [ ] Task 8: Follow-on plan files filed (or "none needed" documented).
- [ ] Task 9: HANDOVER updated; full quality gate green.

---

## Risks and Mitigations

| Risk                                                                                                                                                              | Likelihood | Impact | Mitigation                                                                                                                                                                                  |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The `claude-agent-sdk ^0.1.20` version doesn't expose the `usage` object on stream events                                                                          | LOW–MED    | HIGH   | Task 3 GOTCHA #1: use `getattr(usage, "cache_read_input_tokens", 0)`; if usage is missing entirely, document the gap and fall back to parsing `cli_stderr.log` for cache stats              |
| Cache hit rate turns out to be high already → no `perf-fix-prompt-cache-wiring` plan; user expected this would be the lever                                       | MED        | LOW    | Built into the plan — "Confirmed vs Rejected Suspects" section addresses this directly. The valid outcome includes "caching is fine; the cost is somewhere else." Do not invent fixes.    |
| Stage-B run on a "small ticket" still takes ~90 min and consumes the agent's API budget                                                                          | HIGH       | MED    | One run is enough for Stage B. Pick the smallest available ticket. The data from one well-instrumented run is more valuable than three uninstrumented attempts.                              |
| Implementer treats Task 4f/4g/4h as license to rewrite container/test code                                                                                       | LOW        | HIGH   | Task 4 explicitly says NO behavior changes. Each sub-edit is `with timed():` around the existing block. Level 2 default-suite pass is the regression gate.                                  |
| Instrumentation itself adds measurable overhead even when disabled                                                                                                | LOW        | MED    | `is_enabled()` cached; `timed()` early-return is one boolean check; `test_timed_disabled_is_noop` enforces it.                                                                              |
| Reviewer prompt-section refactor (Task 4e) accidentally changes the resulting prompt content                                                                      | LOW–MED    | HIGH   | GOTCHA #3 on Task 4: capture `len(final_prompt)` before/after and assert byte-equality of the prompt body in a unit test before merging.                                                     |
| Implementer interprets the plan as "go optimize the bottleneck" mid-instrumentation (whack-a-mole)                                                                | LOW        | HIGH   | NOT-Building section is explicit: NO optimizations in this plan. Task 8 *files* fix plans; it does not implement them. The baseline report drives decisions.                                |
| Sandbox can't `poetry install` the new dev deps                                                                                                                   | MED        | MED    | Documented: implementer falls back to `sentinel-dev` per CLAUDE.md. Task 5 is the only network-dependent step.                                                                              |

**Confidence Score**: 8.5/10 for one-pass implementation

The plan is data-grounded — every Stage-A seed is justified by a Stage-0 finding, and the follow-on plans Task 8 will produce are anticipated rather than invented. The held-back 1.5 points: (a) `claude-agent-sdk ^0.1.20`'s `usage` object surface is not yet verified for token-cache fields (Task 3 GOTCHA #1 mitigates with `getattr` defaults), and (b) Task 7's fresh run is genuinely time-expensive — a small ticket may still take ~90 min and the operator must commit a real time budget. Once those two are accepted, the plan is mechanical.

---

## Notes

- **Why "profile first" remains non-negotiable.** The Stage-0 autopsy already disqualified the most-obvious-but-wrong suspects (orchestration overhead, learning-system internals, container time). The remaining four blind spots are the genuinely-load-bearing measurements; without them, every "fix" is a guess. The plan refuses to land an optimization until Stage B confirms its hot path with a number.
- **Why opt-in instrumentation rather than always-on telemetry.** Production runs must pay zero cost. `SENTINEL_PERF=1` is a one-line operator opt-in; default behavior unchanged.
- **Why extend `agent_diagnostics.jsonl` rather than create a new file.** The existing autopsy script (and the Stage-0 baseline) already work against that file; making the new fields additive on `exec_complete` and the new `tool_complete` event a sibling means the same script reads more without breaking.
- **What this plan deliberately does NOT decide.** Whether to wire prompt-cache markers. Whether to slim the reviewer prompt. Whether to cache plan-generator output. Whether to rewrite the developer's tool-use prompt for decisiveness. Whether to parallelize reviewer with the next dev iteration. Each is a candidate; each lives or dies by Stage B's data; each gets its own follow-on plan file. The plan's *only* opinionated commitment is that the four blind spots must be measured before any of those is decided.
- **The Stage-0 autopsy was extracted in this planning session and is reproducible.** Future maintainers run `python tests/perf/autopsy.py --sessions` against the same `logs/agent_diagnostics.jsonl` and get the same table. This is the foundation the next iteration inherits.
- **Container-topology reminder (CLAUDE.md).** Task 5 (`poetry install`) and Task 7 (real `sentinel execute` run) require `sentinel-dev`. The Claude Code sandbox cannot do either.
