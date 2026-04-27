import { useEffect, useState } from "react";
import { api, ApiError, makeIdempotencyKey } from "../api";
import type { ExecutionKind } from "../types";

interface Props {
  open: boolean;
  baseUrl: string;
  token: string;
  presetTicket?: string;
  presetProject?: string;
  presetKind?: ExecutionKind;
  onClose: () => void;
  onCreated: (executionId: string) => void;
}

const TICKET_RE = /^[A-Z][A-Z0-9_]+-\d+$/;

export function StartRunDialog({
  open,
  baseUrl,
  token,
  presetTicket,
  presetProject,
  presetKind,
  onClose,
  onCreated,
}: Props) {
  const [ticket, setTicket] = useState(presetTicket ?? "");
  const [project, setProject] = useState(presetProject ?? "");
  const [kind, setKind] = useState<ExecutionKind>(presetKind ?? "plan");
  const [revise, setRevise] = useState(false);
  const [maxTurns, setMaxTurns] = useState<string>("");
  const [followUp, setFollowUp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Reset form fields whenever the dialog (re)opens with new presets so a
  // second click on a different card / kind doesn't carry stale state from a
  // previous open. Without this, switching between Plan and Execute on the
  // same card was a no-op because `useState`'s initialiser only runs once.
  useEffect(() => {
    if (!open) return;
    setTicket(presetTicket ?? "");
    setProject(presetProject ?? "");
    setKind(presetKind ?? "plan");
    setRevise(false);
    setMaxTurns("");
    setFollowUp("");
    setError(null);
    setBusy(false);
  }, [open, presetTicket, presetProject, presetKind]);

  if (!open) return null;
  const ticketOk = TICKET_RE.test(ticket);

  const submit = async () => {
    if (!ticketOk) {
      setError("Ticket ID must match ^[A-Z][A-Z0-9_]+-\\d+$");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.startExecution(
        { baseUrl, token },
        {
          ticket_id: ticket,
          project: project || undefined,
          kind,
          options: {
            revise,
            max_turns: maxTurns ? Number.parseInt(maxTurns, 10) : null,
            follow_up_ticket: followUp || null,
          },
        },
        makeIdempotencyKey()
      );
      onCreated(created.id);
    } catch (e) {
      if (e instanceof ApiError) setError(`HTTP ${e.status}: ${e.detail ?? ""}`);
      else if (e instanceof Error) setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
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
        style={{ width: 520, maxWidth: "94vw", boxShadow: "var(--shadow-xl)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-head">
          <div className="card-title">Start a new run</div>
        </div>
        <div className="card-body stack-4">
          <div className="stack-2">
            <label className="label">Ticket ID</label>
            <input
              className="input"
              placeholder="ACME-123 or COE_JIRATESTAI-2352"
              value={ticket}
              onChange={(e) => setTicket(e.target.value.trim())}
              autoFocus
            />
            <div
              className="help"
              style={{
                color: ticketOk || !ticket ? "var(--text-subtle)" : "var(--danger)",
              }}
            >
              Pattern: <code className="font-mono">^[A-Z][A-Z0-9_]+-\d+$</code>
            </div>
          </div>

          <div className="stack-2">
            <label className="label">Project (optional)</label>
            <input
              className="input"
              placeholder="derived from ticket prefix"
              value={project}
              onChange={(e) => setProject(e.target.value.trim())}
            />
          </div>

          <div className="stack-2">
            <label className="label">Kind</label>
            <div className="segmented">
              {(["plan", "execute", "debrief"] as ExecutionKind[]).map((k) => (
                <button
                  key={k}
                  data-testid={`start-run-kind-${k}`}
                  className={`seg ${kind === k ? "active" : ""}`}
                  onClick={() => setKind(k)}
                  type="button"
                >
                  {k}
                </button>
              ))}
            </div>
          </div>

          <details>
            <summary
              style={{ cursor: "pointer", fontSize: "var(--fs-sm)", color: "var(--text-muted)" }}
            >
              Options
            </summary>
            <div className="stack-3" style={{ marginTop: 12 }}>
              <label className="inline-3" style={{ cursor: "pointer" }}>
                <span
                  className={"switch " + (revise ? "on" : "")}
                  onClick={() => setRevise(!revise)}
                />
                <span style={{ fontSize: "var(--fs-base)" }}>Revise mode</span>
              </label>
              <div className="stack-2">
                <label className="label">Max turns (1–200)</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={200}
                  value={maxTurns}
                  onChange={(e) => setMaxTurns(e.target.value)}
                />
              </div>
              <div className="stack-2">
                <label className="label">Follow-up ticket</label>
                <input
                  className="input"
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value.trim())}
                  placeholder="ACME-124"
                />
              </div>
            </div>
          </details>

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
        </div>
        <div
          className="card-foot"
          style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
        >
          <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn btn-primary btn-sm"
            disabled={busy || !ticketOk}
            onClick={submit}
          >
            {busy ? "Starting…" : "Start run"}
          </button>
        </div>
      </div>
    </div>
  );
}
