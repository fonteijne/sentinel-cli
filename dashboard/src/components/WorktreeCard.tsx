import { Badge } from "./Badge";
import { Icon } from "../icons";
import type { ExecutionKind, Worktree } from "../types";
import { fmtCost, fmtElapsed, fmtRelative, statusTone } from "../utils";

interface Props {
  wt: Worktree;
  /** When set, the matching action button shows a busy state and is
   *  disabled. Other action buttons stay clickable. */
  pendingKind: ExecutionKind | null;
  onOpen: () => void;
  onStart: (kind: ExecutionKind) => void;
  onCancel: () => void;
  onRetry: () => void;
  /** Card-scoped destructive affordances. Today these surface a "backend
   *  support required" dialog because no worktree CRUD endpoint exists. */
  onResetRequest: () => void;
  onDeleteRequest: () => void;
}

export function WorktreeCard({
  wt,
  pendingKind,
  onOpen,
  onStart,
  onCancel,
  onRetry,
  onResetRequest,
  onDeleteRequest,
}: Props) {
  const live =
    wt.latest?.status === "running" ||
    wt.latest?.status === "queued" ||
    wt.latest?.status === "cancelling";
  const terminal =
    wt.latest?.status === "succeeded" ||
    wt.latest?.status === "failed" ||
    wt.latest?.status === "cancelled";

  return (
    <div className="task-card">
      <div className="between">
        <span
          className="badge"
          style={{ background: "var(--primary-soft)", color: "var(--primary)" }}
        >
          {wt.project}
        </span>
        {wt.latest && (
          <Badge tone={statusTone(wt.latest.status)} dot>
            {wt.latest.status}
          </Badge>
        )}
      </div>

      {wt.latest?.status === "queued" && (
        <div
          className="inline-2 muted"
          data-testid="queued-duration"
          style={{ fontSize: "var(--fs-xs)" }}
        >
          <Icon name="clock" size={12} />
          queued for <strong>{fmtElapsed(wt.latest.started_at)}</strong>
        </div>
      )}

      <button
        className="task-title"
        style={{
          background: "none",
          padding: 0,
          textAlign: "left",
          cursor: wt.latest ? "pointer" : "default",
        }}
        onClick={() => wt.latest && onOpen()}
      >
        <span className="font-mono">{wt.ticket_id}</span>
      </button>

      {wt.latest?.phase && (
        <div className="inline-2 muted" style={{ fontSize: "var(--fs-xs)" }}>
          <Icon name="layers" size={12} />
          phase: <strong>{wt.latest.phase}</strong>
        </div>
      )}

      <div className="task-meta" style={{ alignItems: "center" }}>
        <div className="inline-3 muted" style={{ fontSize: "var(--fs-xs)" }}>
          <span className="inline-2">
            <Icon name="layers" size={12} /> {wt.run_count} runs
          </span>
          <span className="inline-2">
            <Icon name="clock" size={12} />
            {wt.latest ? fmtRelative(wt.latest.started_at) : "never"}
          </span>
          <span className="inline-2">
            <Icon name="chart" size={12} /> {fmtCost(wt.total_cost_cents)}
          </span>
        </div>
      </div>

      <div className="inline-2" style={{ marginTop: 8, flexWrap: "wrap" }}>
        <KindButton
          kind="plan"
          label="Plan"
          icon="rocket"
          slug={wt.slug}
          ticket={wt.ticket_id}
          pendingKind={pendingKind}
          onStart={onStart}
        />
        <KindButton
          kind="execute"
          label="Execute"
          icon="play"
          slug={wt.slug}
          ticket={wt.ticket_id}
          pendingKind={pendingKind}
          onStart={onStart}
        />
        <KindButton
          kind="debrief"
          label="Debrief"
          icon="msg"
          slug={wt.slug}
          ticket={wt.ticket_id}
          pendingKind={pendingKind}
          onStart={onStart}
        />
        {live && (
          <button
            className="btn btn-danger btn-sm"
            onClick={onCancel}
            data-testid={`worktree-${wt.slug}-cancel`}
            aria-label={`Cancel run for ${wt.ticket_id}`}
          >
            <Icon name="stop" size={12} /> Cancel
          </button>
        )}
        {terminal && (
          <button
            className="btn btn-primary btn-sm"
            onClick={onRetry}
            data-testid={`worktree-${wt.slug}-retry`}
            aria-label={`Retry latest run for ${wt.ticket_id}`}
          >
            <Icon name="refresh" size={12} /> Retry
          </button>
        )}
      </div>

      {/* Worktree-scoped destructive affordances. Disabled until a backend
          `WorktreeManager` HTTP surface exists; clicking surfaces a dialog
          that explains the constraint and shows the CLI fallback. */}
      <div
        className="inline-2"
        style={{
          marginTop: 6,
          flexWrap: "wrap",
          paddingTop: 6,
          borderTop: "1px dashed var(--border)",
        }}
      >
        <span
          className="muted"
          style={{ fontSize: "var(--fs-xs)", marginRight: "auto" }}
        >
          Manage worktree
        </span>
        <button
          className="btn btn-secondary btn-sm"
          onClick={onResetRequest}
          data-testid={`worktree-${wt.slug}-reset`}
          aria-label={`Reset worktree ${wt.slug} (coming soon)`}
          title="Reset worktree — backend support required"
        >
          <Icon name="refresh" size={12} /> Reset…
        </button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={onDeleteRequest}
          data-testid={`worktree-${wt.slug}-delete`}
          aria-label={`Delete worktree ${wt.slug} (coming soon)`}
          title="Delete worktree — backend support required"
        >
          <Icon name="x" size={12} /> Delete…
        </button>
      </div>
    </div>
  );
}

function KindButton({
  kind,
  label,
  icon,
  slug,
  ticket,
  pendingKind,
  onStart,
}: {
  kind: ExecutionKind;
  label: string;
  icon: "rocket" | "play" | "msg";
  slug: string;
  ticket: string;
  pendingKind: ExecutionKind | null;
  onStart: (k: ExecutionKind) => void;
}) {
  const busy = pendingKind === kind;
  return (
    <button
      className="btn btn-secondary btn-sm"
      onClick={() => onStart(kind)}
      disabled={busy}
      data-testid={`worktree-${slug}-start-${kind}`}
      aria-label={`Start ${kind} for ${ticket}`}
    >
      <Icon name={icon} size={12} /> {busy ? `${label}…` : label}
    </button>
  );
}
