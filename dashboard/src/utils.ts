import type { ExecutionOut, ExecutionStatus, Worktree } from "./types";

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

const FIVE_MIN_MS = 5 * 60 * 1000;
const ONE_HOUR_MS = 60 * 60 * 1000;

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
      bucket: bucketFor(latest),
    });
  }
  out.sort((a, b) => {
    const ta = a.latest ? new Date(a.latest.started_at).getTime() : 0;
    const tb = b.latest ? new Date(b.latest.started_at).getTime() : 0;
    return tb - ta;
  });
  return out;
}

function bucketFor(latest: ExecutionOut | null): Worktree["bucket"] {
  if (!latest) return "idle";
  const startedAt = new Date(latest.started_at).getTime();
  const live = latest.status === "running" || latest.status === "queued";
  if (live) {
    const stale = Date.now() - startedAt > FIVE_MIN_MS && !latest.ended_at;
    return stale ? "at_risk" : "running";
  }
  if (latest.status === "failed") return "failed";
  if (latest.status === "succeeded" || latest.status === "cancelled")
    return "done";
  if (latest.status === "cancelling") return "running";
  // idle if last activity > 1h
  const last = latest.ended_at ? new Date(latest.ended_at).getTime() : startedAt;
  return Date.now() - last > ONE_HOUR_MS ? "idle" : "done";
}

export function bucketLabel(b: Worktree["bucket"]): string {
  return {
    idle: "Idle",
    running: "Running",
    at_risk: "At risk",
    failed: "Failed",
    done: "Done",
  }[b];
}

export function bucketDot(b: Worktree["bucket"]): string {
  return {
    idle: "var(--text-subtle)",
    running: "var(--info)",
    at_risk: "var(--warning)",
    failed: "var(--danger)",
    done: "var(--success)",
  }[b];
}
