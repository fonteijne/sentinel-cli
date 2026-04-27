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
import { api, ApiError, makeIdempotencyKey } from "../api";

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
  // "coming soon" reset / delete affordances. Backend has no HTTP CRUD for
  // worktrees yet (`WorktreeManager` is CLI-only — see API_CONTRACT §4),
  // so we surface the intent with an explanatory disabled-state modal
  // instead of pretending to call an endpoint that doesn't exist.
  const [resetRequest, setResetRequest] = useState<{ slug: string } | null>(
    null
  );
  const [deleteRequest, setDeleteRequest] = useState<{ slug: string } | null>(
    null
  );
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

  // Track which card+kind action is currently submitting so we can disable
  // its button and keep clicks idempotent. Keyed by `${slug}::${kind}`.
  const [pendingCardAction, setPendingCardAction] = useState<string | null>(
    null
  );

  // Card action → operates on the selected ticket directly via the same
  // `POST /executions` the dialog would call, with the card's kind preset.
  // No second modal: the user already chose ticket+kind by clicking the
  // button on the card. If the ticket somehow fails the backend pattern
  // we fall back to the dialog so the user can fix it.
  const startKindFromCard = async (
    wt: Worktree,
    kind: ExecutionKind
  ): Promise<void> => {
    const key = `${wt.slug}::${kind}`;
    if (pendingCardAction === key) return;
    setPendingCardAction(key);
    setError(null);
    try {
      const created = await api.startExecution(
        { baseUrl, token },
        {
          ticket_id: wt.ticket_id,
          project: wt.project || undefined,
          kind,
        },
        makeIdempotencyKey()
      );
      onChanged();
      navigate("executions", created.id);
    } catch (e) {
      if (e instanceof ApiError) {
        // 422 from the backend means our preset failed validation — fall
        // back to the manual dialog so the user can correct it instead of
        // burying the error in a toast.
        if (e.status === 422) {
          setStartWith({
            ticket: wt.ticket_id,
            project: wt.project,
            kind,
          });
        } else {
          setError(
            `Start ${kind} failed: HTTP ${e.status} ${e.detail ?? ""}`.trim()
          );
        }
      } else if (e instanceof Error) {
        setError(`Start ${kind} failed: ${e.message}`);
      }
    } finally {
      setPendingCardAction(null);
    }
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
        data-testid="worktree-crud-banner"
        style={{ background: "var(--surface-2)", padding: "var(--space-3) var(--space-4)" }}
      >
        <div className="inline-3 muted" style={{ fontSize: "var(--fs-sm)" }}>
          <Icon name="alert" size={14} />
          <span>
            Worktree <strong>create / reset / delete</strong> is{" "}
            <Badge tone="warning" dot>
              coming soon
            </Badge>{" "}
            — today, worktrees are derived from executed tickets and
            <code className="font-mono"> WorktreeManager </code>
            is CLI-only. Each card has reset / delete affordances that
            explain the constraint.
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
                  pendingKind={
                    pendingCardAction?.startsWith(`${w.slug}::`)
                      ? (pendingCardAction.split("::")[1] as ExecutionKind)
                      : null
                  }
                  onOpen={() => w.latest && navigate("executions", w.latest.id)}
                  onStart={(k) => startKindFromCard(w, k)}
                  onCancel={() => {
                    if (w.latest) {
                      setConfirmCancel({ id: w.latest.id, ticket: w.ticket_id });
                    }
                  }}
                  onRetry={() => w.latest && retry(w.latest.id)}
                  onResetRequest={() => setResetRequest({ slug: w.slug })}
                  onDeleteRequest={() => setDeleteRequest({ slug: w.slug })}
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
        presetKind={startWith?.kind}
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

      <UnavailableActionDialog
        open={!!resetRequest}
        title="Reset worktree — backend support required"
        slug={resetRequest?.slug ?? null}
        action="reset"
        plannedRoute="POST /worktrees/{slug}/reset"
        cliFallback="sentinel reset <ticket>"
        onClose={() => setResetRequest(null)}
      />
      <UnavailableActionDialog
        open={!!deleteRequest}
        title="Delete worktree — backend support required"
        slug={deleteRequest?.slug ?? null}
        action="delete"
        plannedRoute="DELETE /worktrees/{slug}"
        cliFallback="git worktree remove <path> && rm -rf <worktree-dir>"
        onClose={() => setDeleteRequest(null)}
      />
    </div>
  );
}

/**
 * Dialog explaining why a destructive worktree action is not yet available.
 * The dashboard intentionally refuses to invent endpoints: the only mutating
 * routes today are `POST /executions`, `cancel`, and `retry`. When/if a
 * `WorktreeManager` HTTP surface lands, we'll replace this with a real
 * type-to-confirm `ConfirmDialog` wired to the new endpoint.
 */
function UnavailableActionDialog({
  open,
  title,
  slug,
  action,
  plannedRoute,
  cliFallback,
  onClose,
}: {
  open: boolean;
  title: string;
  slug: string | null;
  action: "reset" | "delete";
  plannedRoute: string;
  cliFallback: string;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="worktree-unavailable-title"
      data-testid={`worktree-${action}-unavailable`}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20,23,38,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 60,
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: 460, maxWidth: "94vw", boxShadow: "var(--shadow-xl)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-head">
          <div id="worktree-unavailable-title" className="card-title">
            {title}
          </div>
        </div>
        <div className="card-body stack-3">
          <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
            The Sentinel service does not expose any worktree CRUD endpoint
            yet. <code className="font-mono">WorktreeManager</code> lives in
            the CLI process, so the dashboard cannot {action} a worktree from
            the browser without a backend change.
          </div>
          {slug && (
            <div
              style={{
                fontSize: "var(--fs-xs)",
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                padding: "var(--space-2) var(--space-3)",
              }}
            >
              <div className="eyebrow">Target worktree</div>
              <code className="font-mono">{slug}</code>
            </div>
          )}
          <div className="stack-2">
            <div className="eyebrow">When wired up</div>
            <code
              className="font-mono"
              style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}
            >
              {plannedRoute}
            </code>
          </div>
          <div className="stack-2">
            <div className="eyebrow">Out-of-band today</div>
            <code
              className="font-mono"
              style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}
            >
              {cliFallback}
            </code>
          </div>
        </div>
        <div
          className="card-foot"
          style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
        >
          <button
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            data-testid={`worktree-${action}-unavailable-dismiss`}
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
