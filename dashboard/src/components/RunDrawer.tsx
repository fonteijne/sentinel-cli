import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { EmptyState } from "./EmptyState";
import { EventStream } from "./EventStream";
import { Icon } from "../icons";
import type {
  AgentResultOut,
  ExecutionOut,
  ExecutionStatus,
} from "../types";
import { fmtCost, fmtDuration, fmtRelative, statusTone } from "../utils";

interface Props {
  baseUrl: string;
  token: string;
  executionId: string;
  onClose: () => void;
  onChanged: () => void;
}

export function RunDrawer({ baseUrl, token, executionId, onClose, onChanged }: Props) {
  const [exec, setExec] = useState<ExecutionOut | null>(null);
  const [agentResults, setAgentResults] = useState<AgentResultOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | { kind: "cancel" | "retry" }>(null);

  const refresh = async () => {
    try {
      const ex = await api.getExecution({ baseUrl, token }, executionId);
      setExec(ex);
      const ars = await api.listAgentResults({ baseUrl, token }, executionId);
      setAgentResults(ars.items);
    } catch (e) {
      if (e instanceof ApiError) setError(`HTTP ${e.status}: ${e.detail ?? ""}`);
      else if (e instanceof Error) setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [executionId]);

  const live =
    exec?.status === "running" ||
    exec?.status === "queued" ||
    exec?.status === "cancelling";
  const terminal =
    exec?.status === "succeeded" ||
    exec?.status === "failed" ||
    exec?.status === "cancelled";

  const doCancel = async () => {
    if (!exec) return;
    setConfirm(null);
    try {
      await api.cancelExecution({ baseUrl, token }, exec.id);
      await refresh();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError) setError(`Cancel failed: HTTP ${e.status} ${e.detail ?? ""}`);
    }
  };
  const doRetry = async () => {
    if (!exec) return;
    setConfirm(null);
    try {
      await api.retryExecution({ baseUrl, token }, exec.id);
      onChanged();
      onClose();
    } catch (e) {
      if (e instanceof ApiError) setError(`Retry failed: HTTP ${e.status} ${e.detail ?? ""}`);
    }
  };

  return (
    <>
      <div
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(20,23,38,0.36)",
          zIndex: 40,
        }}
        onClick={onClose}
      />
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(720px, 96vw)",
          background: "var(--bg)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "var(--shadow-xl)",
          zIndex: 50,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          className="between"
          style={{
            padding: "var(--space-4) var(--space-6)",
            borderBottom: "1px solid var(--border)",
            background: "var(--surface)",
          }}
        >
          <div className="stack-2" style={{ minWidth: 0 }}>
            <div className="eyebrow">Execution</div>
            <div className="inline-3" style={{ minWidth: 0 }}>
              <span
                className="font-mono"
                style={{
                  fontWeight: 600,
                  fontSize: "var(--fs-md)",
                  whiteSpace: "nowrap",
                }}
              >
                {exec?.ticket_id ?? "…"}
              </span>
              {exec && (
                <Badge tone={statusTone(exec.status)} dot>
                  {exec.status}
                </Badge>
              )}
              {exec?.kind && <Badge>{exec.kind}</Badge>}
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" size={18} />
          </button>
        </div>

        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--space-6)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-5)",
          }}
        >
          {error && (
            <div
              className="alert"
              style={{
                background: "var(--danger-soft)",
                color: "var(--danger)",
                padding: "var(--space-3)",
                borderRadius: "var(--radius-md)",
                fontSize: "var(--fs-sm)",
              }}
            >
              {error}
            </div>
          )}

          {exec && (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: "var(--space-3)",
                }}
              >
                <Stat label="Project" value={exec.project} />
                <Stat
                  label="Started"
                  value={fmtRelative(exec.started_at)}
                />
                <Stat
                  label="Duration"
                  value={fmtDuration(exec.started_at, exec.ended_at)}
                />
                <Stat label="Cost" value={fmtCost(exec.cost_cents)} />
              </div>

              {exec.phase && (
                <div className="card">
                  <div className="card-body inline-3">
                    <Icon name="layers" size={16} />
                    <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                      Current phase
                    </span>
                    <Badge tone="primary">{exec.phase}</Badge>
                  </div>
                </div>
              )}

              {exec.error && (
                <div className="card">
                  <div className="card-head">
                    <div className="card-title">Error</div>
                  </div>
                  <div
                    className="card-body font-mono"
                    style={{
                      fontSize: "var(--fs-sm)",
                      whiteSpace: "pre-wrap",
                      color: "var(--danger)",
                    }}
                  >
                    {exec.error}
                  </div>
                </div>
              )}

              <div className="card">
                <div className="card-head">
                  <div className="card-title">Live event stream</div>
                  <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                    WS · auto-fallback to polling
                  </span>
                </div>
                <div className="card-body">
                  <EventStream
                    baseUrl={baseUrl}
                    token={token}
                    executionId={exec.id}
                    initialStatus={exec.status}
                    onTerminal={(s: ExecutionStatus) => {
                      setExec((cur) => (cur ? { ...cur, status: s } : cur));
                      onChanged();
                    }}
                  />
                </div>
              </div>

              <div className="card">
                <div className="card-head">
                  <div className="card-title">Agent results</div>
                  <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {agentResults.length} recorded
                  </span>
                </div>
                <div className="card-body">
                  {agentResults.length === 0 ? (
                    <EmptyState
                      icon="users"
                      title="No agent results yet"
                      description="Findings, test results, and agent debrief turns will appear here once the orchestrator emits them."
                    />
                  ) : (
                    <div className="stack-3">
                      {agentResults.map((a, i) => (
                        <details
                          key={i}
                          style={{
                            border: "1px solid var(--border)",
                            borderRadius: "var(--radius-md)",
                            padding: "var(--space-3)",
                            background: "var(--surface-2)",
                          }}
                        >
                          <summary
                            style={{ cursor: "pointer", fontWeight: 600 }}
                          >
                            {a.agent}{" "}
                            <span
                              className="muted"
                              style={{ fontSize: "var(--fs-xs)" }}
                            >
                              · {fmtRelative(a.created_at)}
                            </span>
                          </summary>
                          <pre
                            className="font-mono"
                            style={{
                              marginTop: 8,
                              fontSize: "var(--fs-xs)",
                              whiteSpace: "pre-wrap",
                              color: "var(--text-muted)",
                            }}
                          >
                            {JSON.stringify(a.result, null, 2)}
                          </pre>
                        </details>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        <div
          className="between"
          style={{
            padding: "var(--space-4) var(--space-6)",
            borderTop: "1px solid var(--border)",
            background: "var(--surface)",
          }}
        >
          <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
            ID <code className="font-mono">{exec?.id ?? "—"}</code>
          </div>
          <div className="inline-2">
            <button
              className="btn btn-secondary btn-sm"
              onClick={refresh}
            >
              <Icon name="refresh" size={14} /> Refresh
            </button>
            {live && (
              <button
                className="btn btn-danger btn-sm"
                onClick={() => setConfirm({ kind: "cancel" })}
              >
                <Icon name="stop" size={14} /> Cancel run
              </button>
            )}
            {terminal && (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => setConfirm({ kind: "retry" })}
              >
                <Icon name="refresh" size={14} /> Retry
              </button>
            )}
          </div>
        </div>
      </aside>

      <ConfirmDialog
        open={confirm?.kind === "cancel"}
        title="Cancel execution?"
        description="Cancellation sends SIGTERM, then escalates to SIGINT/SIGKILL over up to 30 seconds. The supervisor will tear down child compose projects. This action is final."
        confirmText="Cancel run"
        destructive
        typeToConfirm={exec?.ticket_id}
        onConfirm={doCancel}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.kind === "retry"}
        title="Retry this execution?"
        description="A new execution will be created with the same ticket, project, kind, and options. The new run will be linked to this one via metadata.retry_of."
        confirmText="Retry"
        onConfirm={doRetry}
        onCancel={() => setConfirm(null)}
      />
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: "var(--space-3)",
        borderRadius: "var(--radius-md)",
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
      }}
    >
      <div
        className="muted"
        style={{ fontSize: "var(--fs-xs)", textTransform: "uppercase", letterSpacing: 0.04 }}
      >
        {label}
      </div>
      <div style={{ fontWeight: 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}
