import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { Icon } from "../icons";
import type { EventOut, ExecutionStatus, StreamFrame } from "../types";
import { fmtRelative } from "../utils";

interface Props {
  baseUrl: string;
  token: string;
  executionId: string;
  initialStatus: ExecutionStatus;
  onTerminal: (status: ExecutionStatus) => void;
}

const TYPE_TONE: Record<string, string> = {
  "execution.started": "var(--info)",
  "execution.completed": "var(--success)",
  "execution.failed": "var(--danger)",
  "execution.cancelling": "var(--warning)",
  "execution.cancelled": "var(--text-muted)",
  "phase.changed": "var(--primary)",
  "tool.called": "var(--primary)",
  "agent.message_sent": "var(--info)",
  "agent.response_received": "var(--info)",
  "cost.accrued": "var(--success)",
  "rate_limited": "var(--warning)",
};

export function EventStream({
  baseUrl,
  token,
  executionId,
  initialStatus,
  onTerminal,
}: Props) {
  const [events, setEvents] = useState<EventOut[]>([]);
  const [status, setStatus] = useState<ExecutionStatus>(initialStatus);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"ws" | "polling" | "stopped">("ws");
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

    const onClose = (reason?: string) => {
      if (cancelled || mode === "stopped") return;
      // Fall back to polling if WS dies for any reason.
      setError(`Stream closed (${reason ?? "unknown"}); polling instead.`);
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

      {error && (
        <div
          className="alert"
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
              style={{
                fontSize: "var(--fs-sm)",
                padding: "6px 10px",
                background: "var(--surface)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border)",
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 50,
                  background: TYPE_TONE[ev.type] ?? "var(--text-subtle)",
                  flexShrink: 0,
                }}
              />
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
  return Object.keys(p).length ? JSON.stringify(p).slice(0, 120) : "";
}
