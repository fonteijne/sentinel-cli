# `sentinel execute` Performance Baseline — Living Report

**Plan**: `.claude/PRPs/plans/execute-cycle-perf-iteration.plan.md`
**Status**: Stage 0 frozen; Stage A in progress; Stage B pending.

---

## Methodology

| Field                  | Value                                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------------ |
| Data source            | `logs/agent_diagnostics.jsonl` (existing); `logs/perf.jsonl` (added by Stage A)                        |
| Date window (Stage 0)  | 2026-04-20 → 2026-05-15                                                                                |
| Session segmentation   | gap > 30 min between adjacent events ⇒ new session                                                     |
| Tooling                | `tests/perf/autopsy.py` (added by Task 6); `SENTINEL_PERF=1 sentinel execute` (added by Tasks 1 + 4)   |
| Frozen Stage 0 dataset | `.claude/PRPs/reports/perf-data/autopsy-stage0.json`                                                   |

To reproduce Stage 0 (after Task 6 lands):

```bash
python tests/perf/autopsy.py --sessions
```

To capture Stage B (after the harness lands):

```bash
SENTINEL_PERF=1 sentinel execute <TICKET-ID>
python tests/perf/autopsy.py --report-out .claude/PRPs/reports/execute-cycle-perf-baseline.md
```

---

## Stage 0 — Autopsy findings (frozen 2026-05-18)

15 execute sessions across 3 DHLEXC tickets.

### Representative sessions

| Ticket-Session  | Wall (min) | Agent (min) | Gap (min) | Dev calls | Avg dev call | Tools / dev call |
| --------------- | ---------- | ----------- | --------- | --------- | ------------ | ---------------- |
| DHLEXC-384-S0   | 82         | 73          | 9         | 11        | 6.1 min      | ~45              |
| DHLEXC-384-S1   | 60         | 58          | 2         | 11        | 5.3 min      | ~44              |
| DHLEXC-384-S3   | 128        | 99          | 29        | 18        | 5.5 min      | 51               |
| DHLEXC-311-S3   | 41         | 41          | 0         | 5         | 8.2 min      | 74               |

### Confirmed by data

1. **Orchestration is NOT the bottleneck.** Median session "gap" (everything outside agent invocations) is 0–9 min — vs. 50–100 min spent inside agent invocations.
2. **`drupal_developer` is 95–99% of agent wallclock.** Across 4 representative sessions: dev = 67–99 min, all other agents combined = 0–5 min.
3. **Each developer invocation makes 50–75 tool round-trips, 66% Bash.** From 8,728 tool_use records overall (8,019 from drupal_developer): Bash 5,263, Read 1,294, TodoWrite 676, Write 648, Edit 417.
4. **Reviewer prompts are 600–820k characters per call**, vs. 27k for developer. ~30× prefill cost per reviewer call.
5. **Plan generator runs 1–7× per ticket at 218–299s per call.** DHLEXC-311 had 7 plan_generator invocations (~28 min agent time).

### Four blind spots (Stage A target)

| # | Blind spot                                          | Evidence                                                                                                | Stage A fix                                                                          |
| - | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| 1 | Per-tool actual wallclock                           | `tool_use.elapsed_s` is timeline offset, not duration. Sum exceeds agent wallclock by 24×.              | New `tool_complete` event with explicit `start_ts` / `end_ts`.                       |
| 2 | Prompt-cache hit / miss                             | `cli_stderr.log` (84 MB) does not include `cache_creation_input_tokens` / `cache_read_input_tokens`.    | Capture usage object on the final stream message inside `agent_sdk_wrapper.py`.      |
| 3 | Container, test, composer wallclock                 | `compose_runner`, `environment_manager`, `command_executor` have no perf timing.                        | Plant `with timed("compose.up"):` etc. at top-level methods.                         |
| 4 | Per-API prefill vs. stream                          | No time-to-first-chunk recorded; stream loop logs total elapsed only.                                   | Capture first-chunk timestamp in `receive_response` loop.                            |

---

## Stage A — Instrumentation seeds

| Seed                                              | File                                                                  | Span name                                      | Status         |
| ------------------------------------------------- | --------------------------------------------------------------------- | ---------------------------------------------- | -------------- |
| Outermost execute span                            | `src/cli.py` (execute command)                                        | `execute.full`                                 | _pending T4a_  |
| Per-API request usage stats + first-chunk timing  | `src/agent_sdk_wrapper.py` (additive on `exec_complete`)               | (existing event extended)                      | _pending T3_   |
| Per-tool wallclock                                | `src/agent_sdk_wrapper.py` (new `tool_complete` event)                 | `tool.<name>` via diagnostic event             | _pending T3_   |
| `set_project()` per agent                         | `src/agents/base_agent.py:73-113`                                      | `base_agent.set_project`                       | _pending T4b_  |
| Verifier-retry iteration                          | `src/agents/base_developer.py`                                         | `base_developer.verifier_iteration`            | _pending T4c_  |
| Plan generation + revise                          | `src/agents/plan_generator.py`                                         | `plan_generator.generate`, `plan_generator.revise` | _pending T4d_ |
| Reviewer prompt assembly + per-section bytes      | `src/agents/drupal_reviewer.py`                                        | `drupal_reviewer.assemble_prompt`              | _pending T4e_  |
| Container ops                                     | `src/compose_runner.py`                                                | `compose.up`, `compose.down`, `compose.exec`, `compose.pull_image` | _pending T4f_ |
| Composer install + drush + clone                  | `src/environment_manager.py`                                           | `env.composer_install`, `env.drush_bootstrap`, `env.clone` | _pending T4g_ |
| Test invocation + cmd exec                        | `src/command_executor.py`                                              | `cmd.exec` (with `meta.cmd_kind`)              | _pending T4h_  |

---

## Stage B — Fresh baseline run

**Status**: pending — requires a real `sentinel execute` invocation from `sentinel-dev`. The Claude Code sandbox cannot launch per-ticket containers (see CLAUDE.md, "Container Topology"). The operator must run:

```bash
# From the host or sentinel-dev (the appserver per-ticket containers spawn via DooD).
SENTINEL_PERF=1 sentinel execute <SMALL-TICKET-ID> 2>&1 \
  | tee logs/autopsy/exec-stage-b-$(date +%Y%m%d-%H%M%S).log
```

After the run completes (or caps out — both are valid data; do **not** re-run hoping for a clean exit):

```bash
mkdir -p .claude/PRPs/reports/perf-data/stage-b-<TICKET>-$(date +%Y%m%d-%H%M%S)/
cp logs/agent_diagnostics.jsonl logs/perf.jsonl .claude/PRPs/reports/perf-data/stage-b-<TICKET>-<TS>/
python tests/perf/autopsy.py \
    --diagnostics .claude/PRPs/reports/perf-data/stage-b-<TICKET>-<TS>/agent_diagnostics.jsonl \
    --perf .claude/PRPs/reports/perf-data/stage-b-<TICKET>-<TS>/perf.jsonl \
    --report-out .claude/PRPs/reports/execute-cycle-perf-baseline.md
```

This appends a `## Stage-B autopsy snapshot` section to this file with: ranked spans, cache hit %, per-tool histogram, reviewer prompt section breakdown.

The operator must then manually edit the "Confirmed vs. rejected suspects" table below with verdicts based on those numbers.

### Run metadata

| Field           | Value           |
| --------------- | --------------- |
| Ticket          | _TBD_           |
| Operator        | _TBD_           |
| Start (UTC)     | _TBD_           |
| End (UTC)       | _TBD_           |
| Wall time (min) | _TBD_           |
| Result          | _TBD_           |

### Wall vs. agent vs. gap (vs. Stage 0 averages)

_TBD_

### Top-N spans (full pipeline)

_TBD_ — populated from `tests/perf/autopsy.py --rank`.

### Cache hit rate per agent

_TBD_ — populated from `tests/perf/autopsy.py --cache-rate`. (NEW — not visible in Stage 0.)

### Per-tool actual wallclock histogram

_TBD_ — populated from `tests/perf/autopsy.py --tool-hist`. (NEW — not visible in Stage 0.)

### Reviewer prompt section breakdown

_TBD_ — populated from `tests/perf/autopsy.py --reviewer-prompt`. (NEW — not visible in Stage 0.)

### Confirmed vs. rejected suspects

_For each a-priori suspect, state whether the data confirms or rejects, with numbers._

| Suspect                                              | Stage 0 hypothesis                                                                                       | Stage B verdict | Numbers |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | --------------- | ------- |
| Uncached prefill on every dev tool call              | `cache_read_input_tokens` near zero across the developer's 50–75 roundtrips                              | _TBD_           | _TBD_   |
| Reviewer prompt bloat (600–820k chars)               | A small set of sections dominates the byte count; one or more are redundant                              | _TBD_           | _TBD_   |
| Plan-generator regenerations                         | ≥3 plan_generator invocations per ticket; deterministic regenerate triggers                              | _TBD_           | _TBD_   |
| Container / composer / test overhead                 | Rejected by Stage 0 (gap was 0–9 min). Re-verify with perf spans now that we can measure it.            | _TBD_           | _TBD_   |

---

## Stage C — Recommended follow-on plans

_To be populated by Task 8 — one entry per data-confirmed hot path. Each candidate is conditional on Stage B numbers._

| Candidate plan file                                 | Trigger condition (from Stage B)                                                              | Predicted Δ | Filed | Implemented | Actual Δ |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------- | ----------- | ----- | ----------- | -------- |
| `perf-fix-prompt-cache-wiring.plan.md`              | `cache_read_input_tokens` ≈ 0 across developer roundtrips                                     | _TBD_       | _TBD_ | _TBD_       | _TBD_    |
| `perf-fix-reviewer-prompt-slim.plan.md`             | One section > 300k chars and mostly redundant context                                         | _TBD_       | _TBD_ | _TBD_       | _TBD_    |
| `perf-fix-plan-cache.plan.md`                       | ≥ 3 plan_generator regenerations per ticket with deterministic triggers                       | _TBD_       | _TBD_ | _TBD_       | _TBD_    |
| `perf-fix-dev-tool-decisiveness.plan.md`            | > 50 Bash calls per dev invocation with high redundancy (many `ls` / `cat`)                   | _TBD_       | _TBD_ | _TBD_       | _TBD_    |

If the data does not warrant any fix, this section will say so explicitly.
