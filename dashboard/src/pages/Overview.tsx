import { useMemo } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { KPI } from "../components/KPI";
import { Icon } from "../icons";
import { navigate } from "../routes";
import type { ExecutionOut } from "../types";
import { fmtCost, fmtRelative, statusTone } from "../utils";

interface Props {
  executions: ExecutionOut[];
  loading: boolean;
}

export function Overview({ executions, loading }: Props) {
  const kpis = useMemo(() => computeKpis(executions), [executions]);
  const recent = executions.slice(0, 8);

  return (
    <div className="stack-5">
      <div className="between" style={{ flexWrap: "wrap", gap: "var(--space-3)" }}>
        <div className="stack-2" style={{ minWidth: 0 }}>
          <div className="eyebrow">Command Center</div>
          <h2 className="h4" style={{ margin: 0 }}>
            Overview
          </h2>
        </div>
        <div className="inline-2">
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => navigate("executions")}
          >
            <Icon name="layers" size={14} /> All executions
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => navigate("worktrees")}
          >
            <Icon name="rocket" size={14} /> Start a run
          </button>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "var(--space-4)",
        }}
      >
        <KPI
          icon="layers"
          tone="primary"
          label="Total executions"
          value={kpis.total}
          data={kpis.totalSpark}
        />
        <KPI
          icon="play"
          tone="info"
          label="In flight"
          value={kpis.inflight}
          data={kpis.inflightSpark}
        />
        <KPI
          icon="check"
          tone="success"
          label="Succeeded (recent)"
          value={kpis.succeeded}
          data={kpis.succeededSpark}
        />
        <KPI
          icon="alert"
          tone="warning"
          label="Failures (recent)"
          value={kpis.failed}
          data={kpis.failedSpark}
        />
      </div>

      <div className="card">
        <div className="card-head">
          <div className="card-title">Recent executions</div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => navigate("executions")}
          >
            View all <Icon name="chevronRight" size={14} />
          </button>
        </div>
        <div className="card-body">
          {loading && recent.length === 0 ? (
            <div className="muted" style={{ padding: "var(--space-6)", textAlign: "center" }}>
              Loading…
            </div>
          ) : recent.length === 0 ? (
            <EmptyState
              icon="layers"
              title="No executions yet"
              description="Once the backend records its first execution, it shows up here."
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
                  <th>Cost</th>
                  <th>Started</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {recent.map((e) => (
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

function computeKpis(executions: ExecutionOut[]): {
  total: number;
  inflight: number;
  succeeded: number;
  failed: number;
  totalSpark: number[];
  inflightSpark: number[];
  succeededSpark: number[];
  failedSpark: number[];
} {
  const total = executions.length;
  const inflight = executions.filter(
    (e) => e.status === "running" || e.status === "queued" || e.status === "cancelling"
  ).length;
  const recent = executions.slice(0, 50);
  const succeeded = recent.filter((e) => e.status === "succeeded").length;
  const failed = recent.filter((e) => e.status === "failed").length;
  // Buckets-of-7 sparkline: count by day for last 7 days
  const buckets = (predicate: (e: ExecutionOut) => boolean): number[] => {
    const now = Date.now();
    const day = 24 * 3600 * 1000;
    return Array.from({ length: 7 }, (_, i) => {
      const start = now - (6 - i) * day;
      const end = start + day;
      return executions.filter(
        (e) =>
          predicate(e) &&
          new Date(e.started_at).getTime() >= start &&
          new Date(e.started_at).getTime() < end
      ).length;
    });
  };
  return {
    total,
    inflight,
    succeeded,
    failed,
    totalSpark: buckets(() => true),
    inflightSpark: buckets((e) => e.status === "running" || e.status === "queued"),
    succeededSpark: buckets((e) => e.status === "succeeded"),
    failedSpark: buckets((e) => e.status === "failed"),
  };
}
