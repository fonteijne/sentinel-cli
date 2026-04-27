import type {
  ExecutionKind,
  ExecutionOut,
  ExecutionStatus,
  Worktree,
  WorktreeStage,
  WorktreeStatus,
} from "./types";

export function fmtCost(cents: number | undefined | null): string {
  if (!cents) return "$0.00";
  return `$${(cents / 100).toFixed(2)}`;
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (Number.isNaN(diff)) return "—";
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

export function fmtDuration(start: string, end: string | null): string {
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const diff = Math.max(0, e - s);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  if (m < 60) return `${m}m ${r}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function statusTone(
  status: ExecutionStatus
): "success" | "info" | "warning" | "danger" | "default" {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
    case "queued":
      return "info";
    case "cancelling":
      return "warning";
    case "failed":
      return "danger";
    case "cancelled":
      return "default";
  }
}


export function fmtElapsed(start: string | null | undefined): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  if (Number.isNaN(s)) return "—";
  const diff = Math.max(0, Date.now() - s);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// Severity ordering for `finding.posted` payloads. Unknown severities sort
// last but are still rendered so we don't silently drop new tiers.
const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  moderate: 2,
  low: 3,
  info: 4,
  informational: 4,
};

export function severityRank(value: unknown): number {
  if (typeof value !== "string") return 99;
  return SEVERITY_RANK[value.toLowerCase()] ?? 50;
}

export function severityTone(
  value: unknown
): "danger" | "warning" | "info" | "default" {
  const r = severityRank(value);
  if (r <= 1) return "danger";
  if (r <= 2) return "warning";
  if (r <= 4) return "info";
  return "default";
}

export function deriveWorktrees(executions: ExecutionOut[]): Worktree[] {
  const groups = new Map<string, ExecutionOut[]>();
  for (const ex of executions) {
    const slug = `${ex.project}__${ex.ticket_id}`;
    const arr = groups.get(slug) ?? [];
    arr.push(ex);
    groups.set(slug, arr);
  }
  const out: Worktree[] = [];
  for (const [slug, arr] of groups.entries()) {
    arr.sort(
      (a, b) =>
        new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
    );
    const latest = arr[0] ?? null;
    const total_cost_cents = arr.reduce((s, e) => s + (e.cost_cents ?? 0), 0);
    out.push({
      slug,
      project: latest!.project,
      ticket_id: latest!.ticket_id,
      latest,
      total_cost_cents,
      run_count: arr.length,
      stage: stageFor(latest),
      status: statusFor(latest),
    });
  }
  out.sort((a, b) => {
    const ta = a.latest ? new Date(a.latest.started_at).getTime() : 0;
    const tb = b.latest ? new Date(b.latest.started_at).getTime() : 0;
    return tb - ta;
  });
  return out;
}

/**
 * The card lives in whichever stage column matches its latest execution
 * `kind`. If there is no execution yet, the ticket starts at the front of
 * the workflow (`debrief`). Unknown kinds fall back to `debrief` so the
 * board never silently drops a card.
 */
function stageFor(latest: ExecutionOut | null): WorktreeStage {
  if (!latest) return "debrief";
  if (latest.kind === "debrief" || latest.kind === "plan" || latest.kind === "execute") {
    return latest.kind;
  }
  return "debrief";
}

/**
 * Visual status inside the stage. The user-facing semantics are:
 *   running → blue   (queued / running / cancelling collapse here)
 *   failed  → red
 *   done    → green  (succeeded only — cancelled is treated as idle)
 *   idle    → neutral
 */
function statusFor(latest: ExecutionOut | null): WorktreeStatus {
  if (!latest) return "idle";
  switch (latest.status) {
    case "running":
    case "queued":
    case "cancelling":
      return "running";
    case "failed":
      return "failed";
    case "succeeded":
      return "done";
    case "cancelled":
      return "idle";
  }
}

export const WORKTREE_STAGES: WorktreeStage[] = ["debrief", "plan", "execute"];

export function stageLabel(s: WorktreeStage): string {
  return { debrief: "Debrief", plan: "Plan", execute: "Execution" }[s];
}

export function stageDot(s: WorktreeStage): string {
  return {
    debrief: "var(--text-subtle)",
    plan: "var(--primary)",
    execute: "var(--success)",
  }[s];
}

export function statusLabel(s: WorktreeStatus): string {
  return { running: "Running", failed: "Failed", done: "Done", idle: "Idle" }[s];
}

/**
 * Status colour token. Maps to the requested palette: running blue, failed
 * red, done green, idle neutral. Used both for the chip border and the
 * rounded indicator on the card.
 */
export function statusColor(s: WorktreeStatus): string {
  return {
    running: "var(--info)",
    failed: "var(--danger)",
    done: "var(--success)",
    idle: "var(--text-subtle)",
  }[s];
}

export function statusToneFor(
  s: WorktreeStatus
): "info" | "danger" | "success" | "default" {
  return { running: "info", failed: "danger", done: "success", idle: "default" }[
    s
  ] as "info" | "danger" | "success" | "default";
}

export function kindLabel(k: ExecutionKind): string {
  return { debrief: "Debrief", plan: "Plan", execute: "Execute" }[k];
}
