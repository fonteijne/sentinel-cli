---
branch: feat/sentinel-learning-system
base: main
reviewed: 2026-05-14
recommendation: request-changes
---

# PR Review: feat/sentinel-learning-system → main

**Branch**: `feat/sentinel-learning-system` (10 commits ahead of `main`)
**Scale**: 136 files, +34457/-408 (src: 6629 insertions across 35 files; tests: ~6000 LOC; docs/plans/reports: ~22000 LOC)
**Reviewers**: sentinel-learning-reviewer (specialist), implementation reviewer, test reviewer, plus local static-check sweep

---

## Summary

Omnibus PR landing Phases 1, 2A, 2B, 2C, and 3A of the agent-learning-from-feedback system, plus two follow-ups (`verifier-changed-files-scope`, `verifier-cross-iteration-feedback`). Implementation quality is **high**: SQL is parameterized everywhere, append-only invariants are explicit and tested, subprocess discipline is clean, feature flags default-off, and 12 realistic golden fixtures back the structured-error parser tests. All 10 settled design decisions and 6 ADRs are honored with documented deferrals.

The **specialist reviewer recommends APPROVE** on design/decisions grounds. The **code reviewer raises 7 HIGH-severity issues** — none critical, but several are real production hazards (db connection leak, GitLab N+1 in revert detection, unbounded pagination loop, branch-state leak in promotion flow). My consolidated recommendation is **REQUEST CHANGES** to address H1–H6 before merge; H7, mediums, and lows can land as follow-ups.

---

## Implementation Context

| Artifact | Path |
|---|---|
| Phase 1 plan / report | `.claude/PRPs/plans/completed/phase-1-close-the-leash.plan.md` / `.claude/PRPs/reports/phase-1-close-the-leash-report.md` |
| Phase 2A plan / report | `phase-2a-pitfalls-visible.{plan,report}.md` |
| Phase 2B plan / report | `phase-2b-closed-loops.{plan,report}.md` |
| Phase 2C plan / report | `phase-2c-promotion-path.{plan,report}.md` |
| Phase 3A plan / report | `phase-3a-outcome-ingestion.{plan,report}.md` |
| Verifier follow-ups | `verifier-changed-files-scope.{plan,report}.md`, `verifier-cross-iteration-feedback.{plan,report}.md` |
| Design doc | `docs/agent-learning-from-feedback-2026-05-03.md` |
| Decisions log | `docs/learning-decisions-log.md` (D1–D8 + ADRs) |

All deviations called out in implementation reports were accepted as documented (see "Documented deferrals" below).

---

## Decision & ADR compliance (specialist)

| Decision | Status | Note |
|---|---|---|
| D1 — Karpathy hybrid, grounded first | PASS | Loop A wired in `base_developer.py:708` before memory injection; postmortems gated on `POSTMORTEM_INJECTION=0` default at `prompt_loader.py:19` |
| D2 — Default scope `project:<KEY>` | PARTIAL | Phase 2C extracts at `scope=stack_type` directly (`extract.py:214`); FeedbackDistiller-driven project→stack widening deferred. Reviewer-attribution (≥2 reviewers) not yet enforced. Documented in plan; flag, not block. |
| D3 — Physical home split | PASS | Stack overlays in `prompts/overlays/`; project-scoped path correctly absent |
| D4 — Append-only ledger | PASS | No DELETE helpers; `mark_superseded` flips status + sets pointer; `executions.outcome` enforces append-once via `WHERE outcome IS NULL` |
| D5 — DB canonical, markdown generated | PASS | Overlay markdown written only after MR creation |
| D6 — Prompt budget hard cap | DEFERRED | Pitfalls limited to 15 rows + `PromptBudgetExceeded` log. Full 12k-token cap not enforced — acceptable while only postmortems inject |
| D7 — Snapshot frozen per execution | PASS | Cache key `(agent_name, stack_type)` in `prompt_loader.py:62` |
| D8 — Pull-on-demand outcome ingestion | PASS | `OUTCOME_SYNC_ENABLED` flag gate at `cli.py:87`/`cli.py:3458`; no `python-gitlab` dep |
| D9 — Never learn from bot comments | DEFERRED | No comment ingestion in PR — must land before distiller PR |
| D10 — `raw_comment` verbatim | DEFERRED | Same — gated on distiller PR |

| ADR | Status | Evidence |
|---|---|---|
| ADR-1 cap N=3 | PASS | `MAX_ATTEMPTS=3` at `base_developer.py:39` (no per-stack overrides) |
| ADR-2 Haiku distiller | N/A | Distiller deferred |
| ADR-3 probation injection | N/A | Probation rules not yet wired into prompt_loader (deliberate) |
| ADR-4 widening PR draft=True | PASS | `propose_overlay.py:521` hard-codes `draft=True` |
| ADR-5 no CI overlay cap | PASS | No CI job added |
| ADR-6 per-installation watermark | PASS | `005_outcome_ingestion.sql:34` PK is `project` only — D6 enforced |
| ADR-7 cap-out re-asserts draft | PASS | `post_execute.py:131` calls `mark_as_draft` unconditionally on cap-out |
| ADR-8 no Loop A comments | PASS | Cap-out posts exactly one comment at `post_execute.py:148-152`; retries are events-only |

### Operational gates 7 & 8 (Phase 1 closure)

NOT VERIFIABLE FROM SANDBOX. Both gates require ≥20 production executions. **Phase 1 cannot be declared closed by merge alone** — operator must run the gate-7/gate-8 SQL queries against production after telemetry accumulates.

---

## Phase exit criteria

| Phase | Status | Closure evidence |
|---|---|---|
| 1 | PASS | `tests/integration/test_verifier_retry.py::test_cap_out_writes_postmortem_reverts_draft_posts_one_comment` |
| 2A | PASS | `tests/integration/test_postmortem_injection.py::test_run_n_postmortem_visible_in_run_n_plus_1_prompt` (real prompts dir) |
| 2B | PASS | `test_post_execute_handoff.py` (8 tests) + `test_plan_generator_auto_investigate.py` (3 tests). Note: `test_loop_c_e2e.py` is misnamed — it's a CLI-helper unit test, not E2E |
| 2C | PASS | `test_phase2c_promotion.py::test_extract_propose_promote_revoke_full_workflow` (real CliRunner, real overlay, real git init) + `test_phase2c_supersede_chain.py` |
| 3A | PASS | `test_phase3a_outcomes.py::test_phase3a_exit_criterion` (success + rolled_back + regressed fixture per PRD §496-497) |

---

## Issues found

### Critical
None.

### High Priority

- **H1. `db_conn` leaked in `cli.py::execute()`** — `src/cli.py:645-1048`
  - Both revise and main execute paths do `db_conn = connect(); apply_migrations(db_conn)` but neither has `try/finally: db_conn.close()`. Other CLI commands (`postmortems_list`, `learning_*`, `outcomes_sync`) do this correctly.
  - **Fix**: wrap body in `try/finally` and close the connection.

- **H2. `propose_overlays` does not snapshot/restore current branch** — `src/core/learning/propose_overlay.py:438-472`
  - Real-run path with successful push leaves operator's working copy on `sentinel-learning/promote-...`. Failure path explicitly does not clean up.
  - **Fix**: capture starting ref via `git symbolic-ref --short HEAD`, restore in `finally`.

- **H3. Branch-name collision within same minute** — `src/core/learning/propose_overlay.py:188`
  - `_branch_name_for(scope)` uses `%Y%m%d-%H%M` precision. Dry-run + real-run within one minute collides (dry-run deletes its branch; failed real-run leaves it).
  - **Fix**: add seconds, or 6-char random suffix.

- **H4. Dry-run writes overlay to working tree before reverting** — `src/core/learning/propose_overlay.py:512-528`
  - `_apply_overlay_edit` writes file before `git branch -D`. If user has uncommitted edits to that overlay, dry-run silently overwrites them.
  - **Fix**: assert clean working tree for `prompts/overlays/` before edit, or operate on a worktree-isolated copy.

- **H5. GitLab N+1 in revert-MR detection** — `src/core/learning/outcome_sync.py:494-526`
  - `_find_revert_mr` calls `list_merge_requests` for *every* processed MR, fetching the project's full merged-MR list each time. Becomes O(N²) GitLab calls per sync.
  - **Fix**: hoist the listing out of `_process_mr` and cache once per `sync()`, or constrain by `created_after=mr.merged_at`.

- **H6. `list_merged_mrs_since` unbounded loop on header omission** — `src/gitlab_client.py:241-280`
  - If `X-Total-Pages` is missing AND every page returns exactly `per_page` items, the `len(batch) < per_page` guard never trips. Real GitLab is fine; a misbehaving proxy turns this into an infinite loop.
  - **Fix**: add `max_pages` safety hatch (e.g., 1000).

- **H7. Pretask SHA captured per `implement_feature`, not per attempt** — `src/agents/base_developer.py:892-916,945-1020`
  - Loop-A retry attempts re-use the original pretask_sha for `git diff` change-set computation. If the developer accidentally committed between attempts, diff base is wrong.
  - **Fix**: re-capture pretask_sha at each attempt's start, or assert no new commits since capture before computing diff.

### Medium Priority

- **M1.** `_resolve_path` honors `SENTINEL_DB_PATH` verbatim with no validation — `src/core/persistence/db.py:30-35`. Operator-controlled, low risk.
- **M2.** `parse_drush_config_validation` regex `[\w\- ]+?` allows whitespace-only matches — `src/agents/_structured_errors.py:323-329`. Drop empty matches defensively.
- **M3.** `_overlay_relpath_for` uses `agent_target` verbatim in path — `src/core/learning/propose_overlay.py:194-196`. Validate against `^[a-z_]+$` for defense in depth.
- **M4.** `mark_promoted` accepts unvalidated SHA — `feedback_rules.py:208`. Operator typo lands silently.
- **M5.** `_run_outcome_sync_preflight` has no time bound — `src/cli.py:1810-1828`. Slow sync silently delays `plan`/`execute`. **Fix**: 30s budget + warn.
- **M6.** `EventBus.publish` does `MAX(seq)+1` then INSERT in two statements — `src/core/events/bus.py:75-100`. Single-process safe today; document.
- **M7.** `DrupalDeveloperAgent._parse_test_output` reads `/tmp/phpunit-junit.xml` from host, but file is in container `/tmp` under containerized execution — **PHPUnit failures never produce structured errors on the actual Drupal path** — `src/agents/drupal_developer.py:380-392`. **Fix**: `env_manager.exec(["cat", "/tmp/phpunit-junit.xml"])` or use a bind-mounted path.
- **M8.** `_print_outcome_sync_summary` uses `getattr` on dataclass it imports lazily in same module — `src/cli.py:1759-1786`. Either accept coupling or document why decoupling matters.
- **M9.** Phase 2C: extractor creates rules at `scope=<stack>` directly, bypassing the design's project→stack widening. Documented in plan; forward-binding risk: when distiller lands, widening must be a *new row* with `superseded_by` pointing back, never an UPDATE on `scope`.

### Suggestions / Low

- **L1.** `_split_statements` is fragile — consider `sqlite3.complete_statement()`. (`db.py:91-110`)
- **L2.** `_find_module_root` uses `glob` where `next(glob, None)` would short-circuit. (`base_developer.py:1024-1034`)
- **L3.** Hardcoded security_reviewer threshold `count > 5` in `_extract_blockers` — pull to a named constant. (`cli.py:147-160`)
- **L4.** `_MIN_SIGNATURE_LENGTH = 30` magic number in `extract.py:166`.
- **L5.** `normalize_failure_signature` truncates with `s[:200]` — UTF-8 byte boundaries unsafe (rare in test output). (`_structured_errors.py:482-490`)
- **L6.** `time.sleep(0.01)` in `test_extract.py:302` and `test_feedback_rules_helpers.py:116` — minor flakiness risk on loaded CI; monkeypatch `datetime.now` would be safer.
- **L7.** Hardcoded `/workspace/sentinel/prompts` in `test_postmortem_injection.py:39` — breaks outside dev container; use `Path(__file__).parents[2] / "prompts"`.
- **L8.** New ruff F541 in `plan_generator.py:1719`/`1726`/`1733` (3 unused f-string prefixes added by this branch).
- **L9.** New mypy: `_ensure_composer_deps` missing return annotation at `base_developer.py:1148`. Should be `-> ExecResult | None`.

---

## Validation results

| Check | Status | Details |
|---|---|---|
| Type Check (mypy on changed src/) | NOOP-DELTA | 26 errors total; only 1 new (`_ensure_composer_deps` missing return type). All `EnvironmentManager | None` `union-attr` errors are pre-existing on `main`. |
| Lint (ruff on changed src/) | DELTA +1 | 18 errors on branch vs 17 on main; new ones: 3× F541 in `plan_generator.py:1719,1726,1733`. |
| Unit + integration tests | NOT RUN HERE | Sandbox lacks runtime deps (`dotenv`, `claude_agent_sdk`, etc.); CLAUDE.md mandates tests run inside `sentinel-dev`. Implementation report records: `pytest -q` → 937 passed, 26 failed (vs baseline 670 passed / 35 failed → net +267 passing, -9 failing). All 26 remaining failures pre-date Phase 3A and live in unrelated modules (`test_environment_manager.py`, `test_jira_server_client.py`, `test_plan_generator.py`, `test_worktree_manager.py`). |
| Build | N/A | Pure Python — no build step |
| Migration safety | PASS | Implementation report: idempotent re-run; all new columns/tables queryable |

**Validation gap: I did not independently verify the test suite results — the implementation report's pytest counts are the authoritative source.** If the team wants a fresh run, execute `pytest -q` inside `sentinel-dev`.

---

## Pattern compliance

- [x] Follows existing code structure (helpers keyword-only, lazy imports for cheap `--help`, DB canonical / markdown generated)
- [x] Append-only invariants enforced
- [x] SQL parameterized; no f-string SQL concat
- [x] Subprocess: `shell=False`, list args, `capture_output=True`, timeouts
- [x] Migrations: explicit `BEGIN IMMEDIATE/COMMIT`, schema_migrations gating
- [x] Feature flags default-off; `os.getenv` per call (not cached)
- [x] Tests added for all new src/ surface
- [x] Documentation updated (design doc, decisions log, plans, reports)

---

## What's good

- **Append-only discipline rigorously enforced** — no `update_rule`/`delete_rule` exports, validated by tests, documented at every helper.
- **Migrations**: explicit `BEGIN IMMEDIATE/COMMIT/ROLLBACK`, no `executescript`, schema_migrations gating.
- **Parser adapters**: every parser returns `list` (never None), defensive top-level try/except, docstrings explain *why*.
- **Real CliRunner + real prompts directory** in integration tests — strongest possible signal that exit criteria are genuinely closed.
- **Negative-space assertions**: flag-off byte-for-byte parity, "supersession ≠ revocation", "second sync produces zero new events" — catches the regressions that integration tests typically miss.
- **12 realistic golden fixtures** for structured-error parsers (phpstan with `identifier`/`ignorable`, ruff with `fix.edits.location.row`, pytest short summary, etc.).
- **Verifier loop correctness**: `MAX_ATTEMPTS=3` hard cap, `last_errors` carries across iteration boundaries, structured errors typed, cap-out path persists postmortem → reverts to draft → posts exactly one MR comment per D7+D8.
- **GitLab `mark_as_draft` idempotent**; `propose_overlays` hard-codes `draft=True`.
- **Docstrings explain WHY, not WHAT** — three-pronged DB wipe rationale, pretask_sha rationale, partial-unique-index rationale.

---

## Top 5 production rollout risks

1. **Memory poisoning via stack-scoped probation extraction** without distiller-driven scope inference — a flaky-test pattern from one project could bleed across stacks if `min_projects=2` is met by chance. Mitigation: keep `POSTMORTEM_INJECTION=0` until manual eyes pass over accumulated rules.
2. **`OutcomeSyncService` rate-limit pressure** if multiple Sentinel installations track the same projects (D6 trade-off accepted; H5 makes this worse and should be fixed first).
3. **Cap-out comment storms** if a project hits cap on every execution. D8 collapses to 1 comment per execution; monitor MR-comment volume after rollout.
4. **PHPUnit failures invisible to verifier loop** under containerized execution (M7) — Loop A's effective signal on Drupal is currently static-checks only.
5. **Unbounded GitLab pagination** (H6) under proxy misbehavior — single bad day for a proxy = infinite loop in CLI.

---

## Recommendation

**REQUEST CHANGES** — primarily on **H1 (db_conn leak)**, **H5 (GitLab N+1)**, **H6 (unbounded pagination)**, and **M7 (PHPUnit results invisible under DooD)**. H2/H3/H4 (promotion-flow operator UX) are smaller but cheap to fix in the same revision.

H7 (pretask_sha per-attempt), all medium-priority items, and all lows can land as follow-ups without blocking merge — they are real but bounded, and the verifier loop's other guards (max_attempts hard cap, structured-error carry, event audit trail) make this a tolerable starting state.

Once H1/H5/H6/M7 are resolved, this is an **APPROVE**. The design discipline is excellent and the test coverage on phase exit-criteria is unusually thorough. Do not declare Phase 1 closed until operational gates 7 and 8 are run against production telemetry post-merge.

---

## Notes

- Sandbox could not authenticate `gh` — review **not posted** to GitHub. Operator (or a host-side session) should post this report as a PR comment if a PR exists, or attach to the PR when opened.
- Sandbox could not run the test suite — implementation reports' pytest counts taken as authoritative.

*Report: `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md`*
