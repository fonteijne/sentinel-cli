import type {
  AgentResultOut,
  EventOut,
  ExecutionKind,
  ExecutionOut,
  ListResponse,
  StreamFrame,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail?: string;
  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

interface ApiOptions {
  baseUrl: string;
  token: string;
}

function buildHeaders(token: string, extra?: Record<string, string>): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    ...(extra ?? {}),
  };
}

async function request<T>(
  opts: ApiOptions,
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${opts.baseUrl}${path}`, {
    ...init,
    headers: buildHeaders(opts.token, init?.headers as Record<string, string>),
  });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      detail = body?.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, `HTTP ${res.status}`, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface ExecutionsQuery {
  project?: string;
  ticket_id?: string;
  status?: string;
  kind?: ExecutionKind;
  limit?: number;
  before?: string;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  health: (opts: ApiOptions) =>
    fetch(`${opts.baseUrl}/health`).then((r) => r.ok),

  listExecutions: (opts: ApiOptions, query: ExecutionsQuery = {}) =>
    request<ListResponse<ExecutionOut>>(
      opts,
      `/executions${qs(query as Record<string, unknown>)}`
    ),

  getExecution: (opts: ApiOptions, id: string) =>
    request<ExecutionOut>(opts, `/executions/${encodeURIComponent(id)}`),

  listEvents: (
    opts: ApiOptions,
    id: string,
    sinceSeq = 0,
    limit = 200
  ) =>
    request<ListResponse<EventOut>>(
      opts,
      `/executions/${encodeURIComponent(id)}/events${qs({
        since_seq: sinceSeq,
        limit,
      })}`
    ),

  listAgentResults: (opts: ApiOptions, id: string) =>
    request<ListResponse<AgentResultOut>>(
      opts,
      `/executions/${encodeURIComponent(id)}/agent-results`
    ),

  startExecution: (
    opts: ApiOptions,
    body: {
      ticket_id: string;
      project?: string;
      kind: ExecutionKind;
      options?: {
        revise?: boolean;
        max_turns?: number | null;
        follow_up_ticket?: string | null;
      };
    },
    idempotencyKey?: string
  ) =>
    request<ExecutionOut>(opts, "/executions", {
      method: "POST",
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {},
      body: JSON.stringify(body),
    }),

  cancelExecution: (opts: ApiOptions, id: string) =>
    request<ExecutionOut>(opts, `/executions/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),

  retryExecution: (opts: ApiOptions, id: string) =>
    request<ExecutionOut>(opts, `/executions/${encodeURIComponent(id)}/retry`, {
      method: "POST",
    }),

  openStream(
    opts: ApiOptions,
    id: string,
    sinceSeq: number,
    onFrame: (frame: StreamFrame) => void,
    onClose: (reason?: string, code?: number) => void
  ): { close: () => void } {
    // Backend reads bearer via WS subprotocol; pass token via querystring as a
    // reverse-proxy-friendly fallback. Both work with the current require_token_ws.
    // baseUrl is either absolute (`http://host:8787`, used by Vite dev) or
    // relative (`/api`, used when the production bundle is proxied by the
    // dashboard nginx image). The WebSocket constructor needs an absolute
    // URL, so resolve a relative base against window.location.
    const wsBase = /^https?:/i.test(opts.baseUrl)
      ? opts.baseUrl.replace(/^http/i, "ws")
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${opts.baseUrl}`;
    const wsUrl =
      wsBase +
      `/executions/${encodeURIComponent(id)}/stream?since_seq=${sinceSeq}`;
    const ws = new WebSocket(wsUrl, ["bearer", opts.token]);
    ws.addEventListener("message", (e) => {
      try {
        const frame = JSON.parse(e.data) as StreamFrame;
        onFrame(frame);
      } catch {
        /* swallow parse errors */
      }
    });
    // Forward the close reason verbatim so the UI can detect the
    // `ws_connections_per_token_exhausted` per-token cap (1008) signal
    // emitted by `src/service/routes/stream.py`.
    ws.addEventListener("close", (e) =>
      onClose(e.reason || `code ${e.code}`, e.code)
    );
    ws.addEventListener("error", () => onClose("error"));
    return {
      close: () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      },
    };
  },
};

export function makeIdempotencyKey(): string {
  // Crypto.randomUUID is widely available in modern browsers.
  return crypto.randomUUID();
}
