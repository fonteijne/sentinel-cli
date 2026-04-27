import { Badge } from "./Badge";
import { Icon } from "../icons";
import type { ExecutionKind, Worktree } from "../types";
import {
  fmtCost,
  fmtElapsed,
  fmtRelative,
  kindLabel,
  statusColor,
  statusLabel,
  statusToneFor,
} from "../utils";

interface Props {
  wt: Worktree;
  /** When set, the matching stage button shows a busy state and is
   *  disabled. Other action buttons stay clickable. */
  pendingKind: ExecutionKind | null;
  /** True while a `[retry]` is in flight for this card. */
  retrying: boolean;
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
  retrying,
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
  const hasLatest = !!wt.latest;
  const stageColor = statusColor(wt.status);
  const stageBadgeText = statusLabel(wt.status);

  return (
    <div
      className="task-card"
      data-testid={`worktree-card-${wt.slug}`}
      data-stage={wt.stage}
      data-status={wt.status}
      style={{
        borderLeft: `3px solid ${stageColor}`,
      }}
    >
      <div className="between">
        <span
          className="badge"
          style={{ background: "var(--primary-soft)", color: "var(--primary)" }}
        >
          {wt.project}
        </span>
        <Badge
          tone={statusToneFor(wt.status)}
          dot
          data-testid={`worktree-${wt.slug}-status-${wt.status}`}
        >
          {stageBadgeText}
        </Badge>
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
          cursor: hasLatest ? "pointer" : "default",
        }}
        onClick={() => hasLatest && onOpen()}
      >
        <span className="font-mono">{wt.ticket_id}</span>
      </button>

      <div className="inline-2 muted" style={{ fontSize: "var(--fs-xs)" }}>
        <Icon name="layers" size={12} />
        stage:{" "}
        <strong data-testid={`worktree-${wt.slug}-stage`}>
          {kindLabel(wt.stage)}
        </strong>
        {wt.latest?.phase ? (
          <span style={{ marginLeft: 8 }}>
            · phase: <strong>{wt.latest.phase}</strong>
          </span>
        ) : null}
        {wt.latest?.status ? (
          <span
            style={{ marginLeft: 8 }}
            data-testid={`worktree-${wt.slug}-raw-status`}
          >
            · {wt.latest.status}
          </span>
        ) : null}
      </div>

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

      {/* Stage actions — clicking moves the card into that workflow column.
          Order matches the board: Debrief → Plan → Execute. */}
      <div className="inline-2" style={{ marginTop: 8, flexWrap: "wrap" }}>
        <KindButton
          kind="debrief"
          icon="msg"
          slug={wt.slug}
          ticket={wt.ticket_id}
          pendingKind={pendingKind}
          onStart={onStart}
        />
        <KindButton
          kind="plan"
          icon="rocket"
          slug={wt.slug}
          ticket={wt.ticket_id}
          pendingKind={pendingKind}
          onStart={onStart}
        />
        <KindButton
          kind="execute"
          icon="play"
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
        {hasLatest && (
          <button
            className="btn btn-secondary btn-sm"
            onClick={onRetry}
            disabled={retrying}
            data-testid={`worktree-${wt.slug}-retry`}
            aria-label={`Retry ${kindLabel(wt.stage).toLowerCase()} for ${wt.ticket_id}`}
            title={`Retry ${kindLabel(wt.stage)} for ${wt.ticket_id}`}
          >
            <Icon name="refresh" size={12} /> {retrying ? "Retry…" : "Retry"}
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
  icon,
  slug,
  ticket,
  pendingKind,
  onStart,
}: {
  kind: ExecutionKind;
  icon: "rocket" | "play" | "msg";
  slug: string;
  ticket: string;
  pendingKind: ExecutionKind | null;
  onStart: (k: ExecutionKind) => void;
}) {
  const busy = pendingKind === kind;
  const label = kindLabel(kind);
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
