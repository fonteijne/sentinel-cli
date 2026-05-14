---
branch: feat/sentinel-learning-system
created: 2026-05-14
source-review: feat-sentinel-learning-system-review.md
status: open follow-ups (post-HIGH/MEDIUM closure)
---

# Follow-ups — feat/sentinel-learning-system

The original PR review opened 7 HIGH and 9 MEDIUM findings. All are closed (see `feat-sentinel-learning-system-review.md` for the original review and `phase-1-close-the-leash-report.md` siblings + the H1–M8 implementation reports for the fixes). The items below are what remain.

None of these block merge. They are LOW-severity polish, plus three items that are not code fixes at all (operator gates, a forward-binding invariant, and a single pre-existing lint warning).

---

## LOW priority — code polish

Line numbers verified post-fix (2026-05-14). Pick up in any order; none depend on each other.

### L1 — `_split_statements` is fragile
- `src/core/persistence/db.py:161-203`
- Hand-rolled SQL splitter; comment in the docstring already acknowledges the fragility.
- Fix: replace with `sqlite3.complete_statement()` walking the input. Add a test for a migration whose statement contains a string literal with a `;` to lock in the difference.

### L2 — `_find_module_root` walks more than necessary
- `src/agents/base_developer.py:1111`
- Uses `current.glob("*.info.yml")` and converts to a list. For deep module trees this walks every entry per parent.
- Fix: `next(current.glob("*.info.yml"), None) is not None` short-circuits at the first match.

### L3 — Magic threshold in `_extract_blockers`
- `src/cli.py:121` (helper) and call sites at `:847`, `:1231`, `:1254`
- The `count > 5` heuristic for security_reviewer high-severity blockers is hardcoded.
- Fix: pull to a module constant `_SECURITY_REVIEWER_HIGH_THRESHOLD = 5` with a comment pointing at the matching threshold inside `src/agents/security_reviewer.py`.

### L4 — Magic length in `is_pure_symptom`
- `src/core/learning/extract.py:112` (`if len(s) >= 30:`)
- Fix: pull to a module-level constant `_MIN_SIGNATURE_LENGTH = 30` with a one-line comment explaining the heuristic.

### L5 — `normalize_failure_signature` truncation can split UTF-8
- `src/agents/_structured_errors.py:473`
- `s[:200]` operates on the str (codepoints), so this is actually safe at the codepoint level — flag is on the byte form. Re-verify whether the truncation is ever applied to bytes anywhere downstream; if not, this can be marked **NOT-A-BUG** and closed without code change.
- If the byte-form path matters: switch to `s.encode("utf-8")[:200].decode("utf-8", errors="ignore")`.

### L6 — `time.sleep(0.01)` flakiness risk
- `tests/core/test_extract.py:302`
- `tests/core/test_feedback_rules_helpers.py:116`
- Both rely on SQLite `datetime('now')` rolling over within 10 ms. Usually fine; theoretically racy on a heavily loaded CI runner.
- Fix: monkeypatch `datetime.now` with a controlled clock, or use `freezegun`. Costs nothing now and removes a future flake report.

### L7 — Hardcoded absolute prompts path in test
- `tests/integration/test_postmortem_injection.py:39`: `REAL_PROMPTS_DIR = Path("/workspace/sentinel/prompts")`
- Breaks outside the dev container.
- Fix: `REAL_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"`.

### L8 — Pre-existing F541 in `plan_generator.py`
- 17 occurrences (e.g. `:721`, `:722`, `:1235`, `:1575`, `:1581`, `:1608`, `:1629`, `:1634`, `:1652`, `:1664`, `:1670`, `:1694`, `:1700`, `:1713`, `:1719`, `:1726`, `:1733`)
- F541 is "f-string without any placeholders". All `logger.info(f"…")` lines that don't actually interpolate.
- Auto-fixable: `ruff check --fix src/agents/plan_generator.py`.
- Note: the original review attributed 3 of these to this branch ("plan_generator.py:1719/1726/1733"). The other 14 are pre-existing. Fix all in one pass since they're auto-fixable.

### L9 — `_ensure_composer_deps` missing return annotation
- `src/agents/base_developer.py:1191`: `def _ensure_composer_deps(self, max_attempts: int = 3):`
- Should be `-> ExecResult | None` per the docstring at `:1198-1203`.
- One-line fix; restores mypy parity (was the only NEW mypy error introduced by this branch per the original review).

### L10 — Pre-existing F541 in `base_developer.py:1288`
- `f"Running tests in container (service=appserver)"` — no placeholders.
- Pre-existing on the branch (predates the H/M work). Catch with the L8 sweep.

---

## NOT code fixes

These are tracked here so they don't get lost, but they are not implementable as PRs against the current code.

### G1 — Phase 1 operational gates 7 & 8
Phase 1 ("close the leash") is **not closed by merge alone**. The two operational gates verified at review-time, not in code, require ≥20 production executions before they can be checked.

After ≥20 cap-out-eligible executions accumulate post-merge, an operator must run the SQL queries from the `sentinel-learning-reviewer` charter against the production DB:

- **Gate 7**: every cap-out execution wrote exactly one postmortem with `provenance='auto'` and exactly one MR comment containing the `Sentinel paused here` token.
- **Gate 8**: no execution exceeded `attempts > MAX_ATTEMPTS=3`.

Only then can `phase-1-close-the-leash-report.md` be marked fully closed in the decisions log.

### G2 — M9 forward-binding invariant
M9 was correctly determined to need no plan today: Phase 2D defers `project:<KEY>` scope entirely, and the widening-as-new-row constraint is documented in `docs/agent-learning-from-feedback-2026-05-03.md` §D.3. When the future "project-scope + widening" phase is being planned, three constraints must be baked in:

1. **No SQL anywhere does `UPDATE feedback_rules SET scope = ?`.** Add a persistence-layer test that grep-asserts no source file under `src/` mutates the `scope` column.
2. **Widening logic must live in a separate function** (e.g. `promote_project_rule_to_stack`) that explicitly INSERTs a new row with `superseded_by` pointing back to the project-scoped row. It must not reuse `upsert_rule` (whose `(signature, scope, agent_target)` key is correct for evidence accumulation but the wrong shape for cross-scope promotion).
3. **`agent-learning-from-feedback-DECISIONS.md` §80** should add an explicit numbered decision item for "new row, never UPDATE on scope" — it's currently only implicit in §D.3.

Suggested artifact: a one-line entry under **Architecture / Invariants** in `IDEAS.md`:
> Widening promotion must always insert a new `feedback_rules` row with `superseded_by` linking back to the project-scoped row; never `UPDATE` the `scope` column. (See feat-sentinel-learning-system-followups.md §G2.)

### G3 — Memory-poisoning watch
Original review's top rollout risk: scope=stack rules can be created from cluster aggregation alone (without distiller-driven scope inference), so a flaky-test pattern from one project could bleed across the whole stack if `min_projects=2` is met by chance. Mitigation already in place: `POSTMORTEM_INJECTION=0` default. **Action for operator**: keep that flag off until a manual review of accumulated `feedback_rules` rows passes. No code change required.

---

## Suggested implementation order

If a single follow-up PR sweep is desired:

1. **L8 + L10** (`ruff check --fix src/agents/plan_generator.py src/agents/base_developer.py`) — one command, removes all F541 noise.
2. **L9** — one-line return annotation.
3. **L7** — one-line test path fix.
4. **L1, L2, L3, L4** — code-polish quartet, minutes each.
5. **L6** — replace `time.sleep` with controlled clock; small but worth doing before this becomes a CI-flake story.
6. **L5** — investigate first; may close as NOT-A-BUG.
7. **G2** — file the IDEAS.md memo so the future widening phase doesn't lose the constraint.

G1 and G3 are operator actions, not coding work.
