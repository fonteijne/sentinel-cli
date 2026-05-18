"""Autopsy script for `sentinel execute` performance data.

Reads ``logs/agent_diagnostics.jsonl`` and (when present) ``logs/perf.jsonl``
and emits ranked summaries of the four blind spots Stage A targets:

* per-execute-session wall vs. agent-wallclock breakdown
* top-N spans by total time + invocation count (perf.jsonl)
* prompt-cache hit-rate per agent (extended exec_complete fields)
* per-tool actual wallclock histogram (tool_complete events)
* reviewer prompt section breakdown (assemble_prompt span meta)

Reproduces the Stage-0 baseline tabled in
``execute-cycle-perf-iteration.plan.md`` from the existing JSONL alone.

Usage::

    python tests/perf/autopsy.py [--diagnostics PATH] [--perf PATH] [--report-out PATH]
                                 [--sessions] [--rank] [--cache-rate] [--tool-hist]
                                 [--reviewer-prompt]

Default (no flag) prints all sections to stdout. ``--report-out PATH`` ALSO
appends a "## Stage B" Markdown section to the file at that path.

The script is read-only and side-effect-free apart from the optional
``--report-out`` append.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# A gap longer than this between adjacent diagnostic events ⇒ new execute session.
SESSION_GAP_SECONDS = 30 * 60


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _parse_ts(value: str) -> datetime | None:
    try:
        # Python 3.11 supports trailing 'Z' via fromisoformat; defensively handle both.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _bucket_sessions(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Bucket records into sessions separated by SESSION_GAP_SECONDS gaps.

    Records without a parseable timestamp are skipped from segmentation but
    appended to the closest preceding session.
    """
    sessions: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    last_ts: datetime | None = None
    for rec in records:
        ts = _parse_ts(rec.get("ts", ""))
        if ts is None:
            if current:
                current.append(rec)
            continue
        if last_ts is not None and (ts - last_ts).total_seconds() > SESSION_GAP_SECONDS:
            sessions.append(current)
            current = []
        current.append(rec)
        last_ts = ts
    if current:
        sessions.append(current)
    return sessions


def _session_summary(session: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize one session: wall, agent-elapsed total, by-agent breakdown."""
    starts: list[datetime] = []
    ends: list[datetime] = []
    by_agent: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"invocations": 0, "elapsed_s": 0.0, "tools": 0}
    )
    cwd_first: str | None = None
    for rec in session:
        ts = _parse_ts(rec.get("ts", ""))
        if ts is not None:
            starts.append(ts)
            ends.append(ts)
        if rec.get("cwd") and cwd_first is None:
            cwd_first = str(rec["cwd"])
        if rec.get("event") == "exec_complete":
            agent = rec.get("agent", "?")
            by_agent[agent]["invocations"] += 1
            by_agent[agent]["elapsed_s"] += float(rec.get("total_elapsed_s") or 0)
            by_agent[agent]["tools"] += int(rec.get("tool_count") or 0)
    wall = (max(ends) - min(starts)).total_seconds() if starts else 0.0
    agent_total = sum(a["elapsed_s"] for a in by_agent.values())
    return {
        "start": min(starts).isoformat() if starts else None,
        "end": max(ends).isoformat() if ends else None,
        "wall_s": round(wall, 1),
        "agent_total_s": round(agent_total, 1),
        "gap_s": round(max(0.0, wall - agent_total), 1),
        "by_agent": {a: {"invocations": v["invocations"],
                         "elapsed_s": round(v["elapsed_s"], 1),
                         "tools": v["tools"]} for a, v in by_agent.items()},
        "cwd_sample": cwd_first,
    }


def render_sessions(sessions: list[list[dict[str, Any]]]) -> str:
    lines = ["## Sessions", "",
             "| # | Wall (min) | Agent (min) | Gap (min) | Agents | CWD sample |",
             "| - | ---------- | ----------- | --------- | ------ | ---------- |"]
    for ix, session in enumerate(sessions, start=1):
        s = _session_summary(session)
        agents_summary = ", ".join(
            f"{a}×{v['invocations']}" for a, v in sorted(s["by_agent"].items())
        )
        cwd = (s["cwd_sample"] or "")[-50:]
        lines.append(
            f"| {ix} | {s['wall_s']/60:.1f} | {s['agent_total_s']/60:.1f} | "
            f"{s['gap_s']/60:.1f} | {agents_summary} | {cwd} |"
        )
    return "\n".join(lines)


def render_rank_perf(perf_records: list[dict[str, Any]]) -> str:
    """Top-N spans by total time across perf.jsonl."""
    by_span: dict[str, dict[str, float]] = defaultdict(lambda: {"total_s": 0.0, "count": 0})
    for rec in perf_records:
        name = rec.get("span", "?")
        by_span[name]["total_s"] += float(rec.get("elapsed_s") or 0)
        by_span[name]["count"] += 1
    if not by_span:
        return "## Top spans (perf.jsonl)\n\n_no perf.jsonl records_"
    ranked = sorted(by_span.items(), key=lambda kv: kv[1]["total_s"], reverse=True)
    lines = ["## Top spans (perf.jsonl)", "",
             "| Span | Total (s) | Calls | Avg (s) |",
             "| ---- | --------- | ----- | ------- |"]
    for name, agg in ranked[:40]:
        avg = agg["total_s"] / agg["count"] if agg["count"] else 0
        lines.append(f"| {name} | {agg['total_s']:.1f} | {int(agg['count'])} | {avg:.2f} |")
    return "\n".join(lines)


def render_cache_rate(diagnostics: list[dict[str, Any]]) -> str:
    """Per-agent cache_read / total prefill ratio from extended exec_complete fields."""
    by_agent: dict[str, dict[str, int]] = defaultdict(lambda: {
        "calls": 0, "input": 0, "cache_read": 0, "cache_creation": 0,
    })
    for rec in diagnostics:
        if rec.get("event") != "exec_complete":
            continue
        a = rec.get("agent", "?")
        by_agent[a]["calls"] += 1
        by_agent[a]["input"] += int(rec.get("input_tokens") or 0)
        by_agent[a]["cache_read"] += int(rec.get("cache_read_input_tokens") or 0)
        by_agent[a]["cache_creation"] += int(rec.get("cache_creation_input_tokens") or 0)
    if not by_agent:
        return "## Cache hit rate (per agent)\n\n_no diagnostics_"
    has_cache_data = any(v["cache_read"] > 0 or v["cache_creation"] > 0
                         for v in by_agent.values())
    lines = ["## Cache hit rate (per agent)", ""]
    if not has_cache_data:
        lines.append(
            "_No `cache_read_input_tokens` / `cache_creation_input_tokens` recorded._"
        )
        lines.append("_(Pre-Stage-A data, or SDK didn't surface usage object.)_")
    lines.extend([
        "| Agent | Calls | Input tokens | Cache read | Cache creation | Hit % |",
        "| ----- | ----- | ------------ | ---------- | -------------- | ----- |",
    ])
    for agent, v in sorted(by_agent.items()):
        prefill_total = v["input"] + v["cache_read"] + v["cache_creation"]
        hit_pct = (100.0 * v["cache_read"] / prefill_total) if prefill_total else 0.0
        lines.append(
            f"| {agent} | {v['calls']} | {v['input']} | {v['cache_read']} | "
            f"{v['cache_creation']} | {hit_pct:.1f} |"
        )
    return "\n".join(lines)


def render_tool_hist(diagnostics: list[dict[str, Any]]) -> str:
    """Per-tool wallclock histogram from tool_complete events."""
    by_tool: dict[str, list[float]] = defaultdict(list)
    for rec in diagnostics:
        if rec.get("event") != "tool_complete":
            continue
        tool = rec.get("tool", "?")
        elapsed = float(rec.get("actual_elapsed_s") or 0)
        by_tool[tool].append(elapsed)
    if not by_tool:
        return ("## Per-tool wallclock histogram\n\n"
                "_No `tool_complete` events found — pre-Stage-A data only._")
    lines = ["## Per-tool wallclock histogram", "",
             "| Tool | N | Total (s) | Median (s) | p95 (s) | Max (s) |",
             "| ---- | - | --------- | ---------- | ------- | ------- |"]
    rows = []
    for tool, samples in by_tool.items():
        samples.sort()
        n = len(samples)
        total = sum(samples)
        median = statistics.median(samples)
        p95 = samples[int(0.95 * n)] if n > 1 else samples[0]
        max_s = samples[-1]
        rows.append((total, tool, n, total, median, p95, max_s))
    rows.sort(key=lambda r: r[0], reverse=True)
    for _, tool, n, total, median, p95, max_s in rows:
        lines.append(
            f"| {tool} | {n} | {total:.1f} | {median:.2f} | {p95:.2f} | {max_s:.2f} |"
        )
    return "\n".join(lines)


def render_reviewer_prompt(perf_records: list[dict[str, Any]]) -> str:
    """Reviewer prompt section breakdown from drupal_reviewer.assemble_prompt spans."""
    sections: list[dict[str, int]] = []
    for rec in perf_records:
        if rec.get("span") != "drupal_reviewer.assemble_prompt":
            continue
        meta = rec.get("meta") or {}
        sections.append({
            "header": int(meta.get("section_header_chars") or 0),
            "description": int(meta.get("section_description_chars") or 0),
            "diff": int(meta.get("section_diff_chars") or 0),
            "file_contents": int(meta.get("section_file_contents_chars") or 0),
            "footer": int(meta.get("section_footer_chars") or 0),
            "system_prompt": int(meta.get("system_prompt_chars") or 0),
            "total_user": int(meta.get("total_user_prompt_chars") or 0),
        })
    if not sections:
        return ("## Reviewer prompt section breakdown\n\n"
                "_No `drupal_reviewer.assemble_prompt` spans recorded._")
    keys = ["header", "description", "diff", "file_contents", "footer",
            "system_prompt", "total_user"]
    lines = ["## Reviewer prompt section breakdown", "",
             "| Call | " + " | ".join(keys) + " |",
             "| ---- | " + " | ".join("---" for _ in keys) + " |"]
    for ix, s in enumerate(sections, start=1):
        lines.append(
            "| " + str(ix) + " | " + " | ".join(str(s[k]) for k in keys) + " |"
        )
    avg = {k: sum(s[k] for s in sections) // len(sections) for k in keys}
    lines.append("| AVG | " + " | ".join(str(avg[k]) for k in keys) + " |")
    return "\n".join(lines)


def render_global_tool_distribution(diagnostics: list[dict[str, Any]]) -> str:
    """Global tool_use distribution (mirrors Stage-0 autopsy)."""
    counter: Counter[str] = Counter()
    by_agent_tool: dict[tuple[str, str], int] = Counter()
    for rec in diagnostics:
        if rec.get("event") != "tool_use":
            continue
        tool = rec.get("tool", "?")
        agent = rec.get("agent", "?")
        counter[tool] += 1
        by_agent_tool[(agent, tool)] += 1
    if not counter:
        return ""
    lines = ["## Global tool_use distribution", "",
             "| Tool | Count |",
             "| ---- | ----- |"]
    for tool, n in counter.most_common():
        lines.append(f"| {tool} | {n} |")
    return "\n".join(lines)


def build_report(
    diagnostics: list[dict[str, Any]],
    perf_records: list[dict[str, Any]],
    flags: argparse.Namespace,
) -> str:
    show_all = not any([
        flags.sessions, flags.rank, flags.cache_rate,
        flags.tool_hist, flags.reviewer_prompt,
    ])

    sessions = _bucket_sessions(diagnostics) if (show_all or flags.sessions) else []

    parts: list[str] = []
    if show_all or flags.sessions:
        parts.append(render_sessions(sessions))
    if show_all or flags.rank:
        parts.append(render_rank_perf(perf_records))
    if show_all or flags.cache_rate:
        parts.append(render_cache_rate(diagnostics))
    if show_all or flags.tool_hist:
        parts.append(render_tool_hist(diagnostics))
    if show_all or flags.reviewer_prompt:
        parts.append(render_reviewer_prompt(perf_records))
    if show_all:
        global_dist = render_global_tool_distribution(diagnostics)
        if global_dist:
            parts.append(global_dist)
    return "\n\n".join(parts) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Autopsy `sentinel execute` perf data.")
    parser.add_argument("--diagnostics", type=Path,
                        default=Path("logs/agent_diagnostics.jsonl"))
    parser.add_argument("--perf", type=Path, default=Path("logs/perf.jsonl"))
    parser.add_argument("--report-out", type=Path, default=None,
                        help="Append a Stage-B section to this report.")
    parser.add_argument("--sessions", action="store_true")
    parser.add_argument("--rank", action="store_true")
    parser.add_argument("--cache-rate", action="store_true")
    parser.add_argument("--tool-hist", action="store_true")
    parser.add_argument("--reviewer-prompt", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    diagnostics = _load_jsonl(args.diagnostics)
    perf_records = _load_jsonl(args.perf)
    report = build_report(diagnostics, perf_records, args)
    sys.stdout.write(report)

    if args.report_out is not None:
        target = Path(args.report_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as f:
            f.write("\n\n---\n\n## Stage-B autopsy snapshot\n\n")
            f.write(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
