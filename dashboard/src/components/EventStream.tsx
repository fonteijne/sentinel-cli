import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { Icon, type IconName } from "../icons";
import type { EventOut, ExecutionStatus, StreamFrame } from "../types";
import { fmtRelative } from "../utils";

interface Props {
  baseUrl: string;
  token: string;
  executionId: string;
  initialStatus: ExecutionStatus;
  onTerminal: (status: ExecutionStatus) => void;
  onWsCapExhausted?: () => void;
}

const TYPE_TONE: Record<string, string> = {
  "execution.started": "var(--info)",
  "execution.completed": "var(--success)",
  "execution.failed": "var(--danger)",
  "execution.cancelling": "var(--warning)",
  "execution.cancelled": "var(--text-muted)",
  "phase.changed": "var(--primary)",
  "tool.called": "var(--primary)",
  "agent.started": "var(--info)",
  "agent.finished": "var(--info)",
  "agent.message_sent": "var(--info)",
  "agent.response_received": "var(--info)",
  "cost.accrued": "var(--success)",
  "test.result": "var(--success)",
  "finding.posted": "var(--warning)",
  "debrief.turn": "var(--primary)",
  "revision.requested": "var(--warning)",
  "rate_limited": "var(--warning)",
};

const TYPE_ICON: Record<string, IconName> = {
  "execution.started": "play",
  "execution.completed": "check",
  "execution.failed": "alert",
  "execution.cancelling": "stop",
  "execution.cancelled": "stop",
  "phase.changed": "layers",
  "tool.called": "box",
  "agent.started": "users",
  "agent.finished": "users",
  "agent.message_sent": "msg",
  "agent.response_received": "msg",
  "cost.accrued": "chart",
  "test.result": "check",
  "finding.posted": "flag",
  "debrief.turn": "msg",
  "revision.requested": "edit",
  "rate_limited": "clock",
};

export function EventStream({
  baseUrl,
  token,
  executionId,
  initialStatus,
  onTerminal,
  onWsCapExhausted,
}: Props) {
  const [events, setEvents] = useState<EventOut[]>([]);
  const [status, setStatus] = useState<ExecutionStatus>(initialStatus);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"ws" | "polling" | "stopped">("ws");
  const [wsCapHit, setWsCapHit] = useState(false);
  const seqRef = useRef(0);
  const wsRef = useRef<{ close: () => void } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setStatus(initialStatus);
    setError(null);
    seqRef.current = 0;

    const opts = { baseUrl, token };

    // Try WebSocket first
    let pollHandle: number | undefined;
    const startPolling = () => {
      setMode("polling");
      const tick = async () => {
        if (cancelled) return;
        try {
          const res = await api.listEvents(opts, executionId, seqRef.current, 200);
          if (cancelled) return;
          if (res.items.length) {
            seqRef.current = res.items[res.items.length - 1]!.seq;
            setEvents((prev) => [...prev, ...res.items]);
          }
          // Check terminal
          const ex = await api.getExecution(opts, executionId);
          if (cancelled) return;
          setStatus(ex.status);
          if (
            ex.status === "succeeded" ||
            ex.status === "failed" ||
            ex.status === "cancelled"
          ) {
            setMode("stopped");
            onTerminal(ex.status);
            return;
          }
        } catch (e) {
          if (e instanceof ApiError) setError(`HTTP ${e.status}: ${e.detail ?? ""}`);
          else if (e instanceof Error) setError(e.message);
        }
        if (!cancelled) pollHandle = window.setTimeout(tick, 2500);
      };
      tick();
    };

    const onFrame = (f: StreamFrame) => {
      if (f.kind === "event" && f.seq !== undefined) {
        seqRef.current = f.seq;
        setEvents((prev) => [
          ...prev,
          {
            seq: f.seq!,
            ts: f.ts ?? new Date().toISOString(),
            agent: f.agent ?? null,
            type: f.type ?? "unknown",
            payload: f.payload ?? {},
          },
        ]);
      } else if (f.kind === "end") {
        if (f.execution_status) {
          setStatus(f.execution_status);
          onTerminal(f.execution_status);
        }
        setMode("stopped");
      }
    };

    const onClose = (reason?: string, code?: number) => {
      if (cancelled || mode === "stopped") return;
      // 1008 with reason `ws_connections_per_token_exhausted` means the
      // service-side per-token WS cap is full. Surface a dedicated banner
      // and let the parent broadcast a toast — silent polling fallback hid
      // a real saturation signal in earlier passes.
      const capExhausted =
        code === 1008 &&
        typeof reason === "string" &&
        reason.includes("ws_connections_per_token_exhausted");
      if (capExhausted) {
        setWsCapHit(true);
        onWsCapExhausted?.();
      }
      setError(
        capExhausted
          ? "Live stream limit reached for this token; polling for updates instead."
          : `Stream closed (${reason ?? "unknown"}); polling instead.`
      );
      startPolling();
    };

    try {
      wsRef.current = api.openStream(opts, executionId, 0, onFrame, onClose);
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      if (pollHandle) window.clearTimeout(pollHandle);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, token, executionId]);

  return (
    <div className="stack-3">
      <div className="between">
        <div className="inline-2 muted" style={{ fontSize: "var(--fs-sm)" }}>
          <Icon name={mode === "ws" ? "play" : mode === "polling" ? "refresh" : "stop"} size={14} />
          <span>
            {mode === "ws" && "Live (WebSocket)"}
            {mode === "polling" && "Polling fallback"}
            {mode === "stopped" && "Stream ended"}
          </span>
        </div>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
          {events.length} events · status: <strong>{status}</strong>
        </span>
      </div>

      {wsCapHit && (
        <div
          role="status"
          aria-live="polite"
          data-testid="ws-cap-banner"
          className="alert"
          style={{
            background: "var(--warning-soft)",
            color: "var(--warning)",
            padding: "var(--space-3) var(--space-4)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--fs-sm)",
            border: "1px solid var(--warning)",
          }}
        >
          <div className="inline-2">
            <Icon name="alert" size={16} />
            <strong>Live stream limit reached for this token.</strong>
          </div>
          <div className="muted" style={{ marginTop: 4, fontSize: "var(--fs-xs)" }}>
            The service caps concurrent WebSocket streams per token
            (config: <code>service.rate_limits.ws_concurrent_per_token</code>).
            Falling back to HTTP polling — events still update, just with a
            short delay. Close another live tab to free a slot.
          </div>
        </div>
      )}

      {error && !wsCapHit && (
        <div
          className="alert"
          data-testid="ws-error"
          style={{
            background: "var(--warning-soft)",
            color: "var(--warning)",
            padding: "var(--space-3) var(--space-4)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--fs-sm)",
          }}
        >
          {error}
        </div>
      )}

      <div
        className="stack-2"
        style={{
          maxHeight: 360,
          overflowY: "auto",
          background: "var(--surface-2)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-3)",
          border: "1px solid var(--border)",
        }}
      >
        {events.length === 0 ? (
          <div
            className="muted"
            style={{ fontSize: "var(--fs-sm)", padding: "var(--space-4)", textAlign: "center" }}
          >
            Waiting for events…
          </div>
        ) : (
          events.map((ev) => (
            <div
              key={ev.seq}
              className="inline-3"
              data-testid={`event-row-${ev.type}`}
              style={{
                fontSize: "var(--fs-sm)",
                padding: "6px 10px",
                background: "var(--surface)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border)",
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 18,
                  height: 18,
                  borderRadius: 50,
                  color: TYPE_TONE[ev.type] ?? "var(--text-subtle)",
                  flexShrink: 0,
                }}
              >
                <Icon name={TYPE_ICON[ev.type] ?? "target"} size={12} />
              </span>
              <span
                className="font-mono subtle"
                style={{ fontSize: "var(--fs-xs)", width: 56, flexShrink: 0 }}
              >
                #{ev.seq}
              </span>
              <span style={{ fontWeight: 600, flexShrink: 0 }}>{ev.type}</span>
              {ev.agent && (
                <span className="badge" style={{ flexShrink: 0 }}>
                  {ev.agent}
                </span>
              )}
              <span className="muted" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {summarizePayload(ev)}
              </span>
              <span
                className="font-mono subtle"
                style={{ fontSize: "var(--fs-xs)", flexShrink: 0 }}
              >
                {fmtRelative(ev.ts)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function summarizePayload(ev: EventOut): string {
  const p = ev.payload ?? {};
  if (ev.type === "tool.called") return `${p.tool ?? "?"} — ${p.args_summary ?? ""}`;
  if (ev.type === "phase.changed") return `→ ${p.phase ?? ""}`;
  if (ev.type === "cost.accrued")
    return `+${(((p.cents as number) ?? 0) / 100).toFixed(2)}$ (in ${p.tokens_in}, out ${p.tokens_out})`;
  if (ev.type === "rate_limited") return `retry after ${p.retry_after_s ?? "?"}s`;
  if (ev.type === "execution.failed") return String(p.error ?? "");
  if (ev.type === "agent.response_received")
    return `${p.response_chars ?? 0} chars, ${p.tool_uses_count ?? 0} tools, ${p.elapsed_s ?? 0}s`;
  if (ev.type === "agent.started" || ev.type === "agent.finished") {
    const sid = p.session_id;
    return sid ? `session ${String(sid).slice(0, 8)}` : "";
  }
  if (ev.type === "test.result") {
    const ok = p.success === true ? "PASS" : p.success === false ? "FAIL" : "?";
    const rc = p.return_code;
    return rc !== undefined ? `${ok} (rc=${rc})` : ok;
  }
  if (ev.type === "finding.posted") {
    const sev = p.severity ? `[${String(p.severity).toUpperCase()}] ` : "";
    return `${sev}${p.summary ?? ""}`;
  }
  if (ev.type === "debrief.turn") {
    return `turn ${p.turn_index ?? "?"} · ${p.prompt_chars ?? 0}→${p.response_chars ?? 0} chars`;
  }
  if (ev.type === "revision.requested") {
    const target = p.revise_of_execution_id
      ? String(p.revise_of_execution_id).slice(0, 8)
      : "";
    const why = p.reason ? ` — ${p.reason}` : "";
    return `revise ${target}${why}`;
  }
  return Object.keys(p).length ? JSON.stringify(p).slice(0, 120) : "";
}
