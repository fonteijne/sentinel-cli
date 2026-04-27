import { Badge } from "./Badge";
import { Icon } from "../icons";
import type { ExecutionKind, Worktree } from "../types";
import { fmtCost, fmtElapsed, fmtRelative, statusTone } from "../utils";

interface Props {
  wt: Worktree;
  onOpen: () => void;
  onStart: (kind: ExecutionKind) => void;
  onCancel: () => void;
  onRetry: () => void;
}

export function WorktreeCard({ wt, onOpen, onStart, onCancel, onRetry }: Props) {
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
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => onStart("plan")}
        >
          <Icon name="rocket" size={12} /> Plan
        </button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => onStart("execute")}
        >
          <Icon name="play" size={12} /> Execute
        </button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => onStart("debrief")}
        >
          <Icon name="msg" size={12} /> Debrief
        </button>
        {live && (
          <button className="btn btn-danger btn-sm" onClick={onCancel}>
            <Icon name="stop" size={12} /> Cancel
          </button>
        )}
        {terminal && (
          <button className="btn btn-primary btn-sm" onClick={onRetry}>
            <Icon name="refresh" size={12} /> Retry
          </button>
        )}
      </div>
    </div>
  );
}
