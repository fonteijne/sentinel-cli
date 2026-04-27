import { useMemo, useState } from "react";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { StartRunDialog } from "../components/StartRunDialog";
import { WorktreeCard } from "../components/WorktreeCard";
import { Icon } from "../icons";
import { navigate } from "../routes";
import type { ExecutionKind, ExecutionOut, Worktree } from "../types";
import { bucketDot, bucketLabel, deriveWorktrees } from "../utils";
import { api, ApiError } from "../api";

interface Props {
  baseUrl: string;
  token: string;
  executions: ExecutionOut[];
  loading: boolean;
  onChanged: () => void;
}

const COLUMNS: Worktree["bucket"][] = ["running", "at_risk", "failed", "done", "idle"];

export function Worktrees({ baseUrl, token, executions, loading, onChanged }: Props) {
  const worktrees = useMemo(() => deriveWorktrees(executions), [executions]);
  const [filter, setFilter] = useState("");
  const [startWith, setStartWith] = useState<{
    ticket?: string;
    project?: string;
    kind: ExecutionKind;
  } | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<{
    id: string;
    ticket: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const filtered = filter
    ? worktrees.filter(
        (w) =>
          w.ticket_id.toLowerCase().includes(filter.toLowerCase()) ||
          w.project.toLowerCase().includes(filter.toLowerCase())
      )
    : worktrees;

  const grouped: Record<Worktree["bucket"], Worktree[]> = {
    running: [],
    at_risk: [],
    failed: [],
    done: [],
    idle: [],
  };
  for (const w of filtered) grouped[w.bucket].push(w);

  const startKind = async (kind: ExecutionKind, ticket?: string, project?: string) => {
    setStartWith({ ticket, project, kind });
  };

  const cancel = async (id: string) => {
    setConfirmCancel(null);
    try {
      await api.cancelExecution({ baseUrl, token }, id);
      onChanged();
    } catch (e) {
      if (e instanceof ApiError) setError(`Cancel failed: HTTP ${e.status} ${e.detail ?? ""}`);
    }
  };

  const retry = async (id: string) => {
    try {
      await api.retryExecution({ baseUrl, token }, id);
      onChanged();
    } catch (e) {
      if (e instanceof ApiError) setError(`Retry failed: HTTP ${e.status} ${e.detail ?? ""}`);
    }
  };

  return (
    <div className="stack-5">
      <div
        className="between"
        style={{ flexWrap: "wrap", gap: "var(--space-3)" }}
      >
        <div className="stack-2" style={{ minWidth: 0 }}>
          <div className="eyebrow">Tickets · grouped by worktree</div>
          <h2 className="h4" style={{ margin: 0 }}>
            Worktrees board
          </h2>
        </div>
        <div className="inline-2">
          <div className="search input-group" style={{ width: 280 }}>
            <span className="input-icon">
              <Icon name="search" size={16} />
            </span>
            <input
              className="input"
              placeholder="Filter by ticket or project…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => setStartWith({ kind: "plan" })}
          >
            <Icon name="rocket" size={14} /> New run
          </button>
        </div>
      </div>

      <div
        className="card"
        style={{ background: "var(--surface-2)", padding: "var(--space-3) var(--space-4)" }}
      >
        <div className="inline-3 muted" style={{ fontSize: "var(--fs-sm)" }}>
          <Icon name="alert" size={14} />
          <span>
            Worktree CRUD (create / delete / reset) is{" "}
            <Badge tone="warning" dot>
              coming soon
            </Badge>{" "}
            — today, worktrees are derived from executed tickets.
          </span>
        </div>
      </div>

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

      {loading && worktrees.length === 0 ? (
        <div className="muted" style={{ padding: "var(--space-6)", textAlign: "center" }}>
          Loading…
        </div>
      ) : worktrees.length === 0 ? (
        <EmptyState
          icon="branch"
          title="No worktrees yet"
          description="Worktrees appear here as soon as the backend records executions for them."
        />
      ) : (
        <div className="kanban">
          {COLUMNS.map((b) => (
            <div key={b} className="kanban-col">
              <div className="kanban-col-head between">
                <div className="inline-2">
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 50,
                      background: bucketDot(b),
                      display: "inline-block",
                    }}
                  />
                  <span>{bucketLabel(b)}</span>
                  <span className="kanban-col-count">{grouped[b].length}</span>
                </div>
              </div>
              {grouped[b].map((w) => (
                <WorktreeCard
                  key={w.slug}
                  wt={w}
                  onOpen={() => w.latest && navigate("executions", w.latest.id)}
                  onStart={(k) => startKind(k, w.ticket_id, w.project)}
                  onCancel={() => {
                    if (w.latest) {
                      setConfirmCancel({ id: w.latest.id, ticket: w.ticket_id });
                    }
                  }}
                  onRetry={() => w.latest && retry(w.latest.id)}
                />
              ))}
            </div>
          ))}
        </div>
      )}

      <StartRunDialog
        open={!!startWith}
        baseUrl={baseUrl}
        token={token}
        presetTicket={startWith?.ticket}
        presetProject={startWith?.project}
        onClose={() => setStartWith(null)}
        onCreated={(id) => {
          setStartWith(null);
          onChanged();
          navigate("executions", id);
        }}
      />

      <ConfirmDialog
        open={!!confirmCancel}
        title="Cancel execution?"
        description="This stops the worker (SIGTERM → SIGINT → SIGKILL over up to 30 seconds) and tears down child compose projects."
        confirmText="Cancel run"
        destructive
        typeToConfirm={confirmCancel?.ticket}
        onConfirm={() => confirmCancel && cancel(confirmCancel.id)}
        onCancel={() => setConfirmCancel(null)}
      />
    </div>
  );
}
