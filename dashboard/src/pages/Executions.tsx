import { useState } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../icons";
import { navigate } from "../routes";
import type { ExecutionKind, ExecutionOut, ExecutionStatus } from "../types";
import { fmtCost, fmtDuration, fmtRelative, statusTone } from "../utils";

interface Props {
  executions: ExecutionOut[];
  loading: boolean;
  onRefresh: () => void;
}

const STATUS_OPTIONS: (ExecutionStatus | "")[] = [
  "",
  "queued",
  "running",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
];
const KIND_OPTIONS: (ExecutionKind | "")[] = ["", "plan", "execute", "debrief"];

export function Executions({ executions, loading, onRefresh }: Props) {
  const [status, setStatus] = useState<string>("");
  const [kind, setKind] = useState<string>("");
  const [project, setProject] = useState("");
  const [ticket, setTicket] = useState("");

  const filtered = executions.filter(
    (e) =>
      (!status || e.status === status) &&
      (!kind || e.kind === kind) &&
      (!project || e.project.toLowerCase().includes(project.toLowerCase())) &&
      (!ticket || e.ticket_id.toLowerCase().includes(ticket.toLowerCase()))
  );

  return (
    <div className="stack-5">
      <div className="between" style={{ flexWrap: "wrap", gap: "var(--space-3)" }}>
        <div className="stack-2" style={{ minWidth: 0 }}>
          <div className="eyebrow">All executions</div>
          <h2 className="h4" style={{ margin: 0 }}>
            Executions
          </h2>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onRefresh}>
          <Icon name="refresh" size={14} /> Refresh
        </button>
      </div>

      <div className="card">
        <div className="card-body" style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap" }}>
          <div className="stack-2" style={{ minWidth: 200, flex: "1 1 auto" }}>
            <label className="label">Ticket</label>
            <input className="input" value={ticket} onChange={(e) => setTicket(e.target.value)} />
          </div>
          <div className="stack-2" style={{ minWidth: 160 }}>
            <label className="label">Project</label>
            <input className="input" value={project} onChange={(e) => setProject(e.target.value)} />
          </div>
          <div className="stack-2" style={{ minWidth: 160 }}>
            <label className="label">Status</label>
            <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s || "any"}
                </option>
              ))}
            </select>
          </div>
          <div className="stack-2" style={{ minWidth: 160 }}>
            <label className="label">Kind</label>
            <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
              {KIND_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s || "any"}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div className="card-title">{filtered.length} matches</div>
          <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
            Newest first · click row for details
          </span>
        </div>
        <div className="card-body" style={{ overflowX: "auto" }}>
          {loading && executions.length === 0 ? (
            <div className="muted" style={{ padding: "var(--space-6)", textAlign: "center" }}>
              Loading…
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState
              icon="layers"
              title="No matching executions"
              description="Adjust filters or start a new run from the Worktrees page."
            />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Ticket</th>
                  <th>Project</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Phase</th>
                  <th>Duration</th>
                  <th>Cost</th>
                  <th>Started</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((e) => (
                  <tr
                    key={e.id}
                    style={{ cursor: "pointer" }}
                    onClick={() => navigate("executions", e.id)}
                  >
                    <td>
                      <span className="font-mono">{e.ticket_id}</span>
                    </td>
                    <td>{e.project}</td>
                    <td>
                      <Badge>{e.kind}</Badge>
                    </td>
                    <td>
                      <Badge tone={statusTone(e.status)} dot>
                        {e.status}
                      </Badge>
                    </td>
                    <td className="muted">{e.phase ?? "—"}</td>
                    <td className="font-mono">{fmtDuration(e.started_at, e.ended_at)}</td>
                    <td className="font-mono">{fmtCost(e.cost_cents)}</td>
                    <td className="muted">{fmtRelative(e.started_at)}</td>
                    <td>
                      <Icon name="chevronRight" size={14} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
