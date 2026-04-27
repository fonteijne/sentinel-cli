// Mirror of backend Pydantic models in src/service/schemas.py — read-only.

export type ExecutionStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ExecutionKind = "plan" | "execute" | "debrief";

export interface ExecutionOut {
  id: string;
  ticket_id: string;
  project: string;
  kind: ExecutionKind;
  status: ExecutionStatus;
  phase: string | null;
  started_at: string;
  ended_at: string | null;
  cost_cents: number;
  error: string | null;
  metadata: Record<string, unknown>;
}

export interface EventOut {
  seq: number;
  ts: string;
  agent: string | null;
  type: string;
  payload: Record<string, unknown>;
}

export interface AgentResultOut {
  agent: string;
  result: Record<string, unknown>;
  created_at: string;
}

export interface ListResponse<T> {
  items: T[];
  next_cursor: string | null;
}

export interface StreamFrame {
  kind: "event" | "heartbeat" | "end";
  seq?: number;
  ts?: string;
  type?: string;
  agent?: string | null;
  payload?: Record<string, unknown>;
  execution_status?: ExecutionStatus;
}

/**
 * Stage = the workflow column the card lives in. The board is now ordered
 * `debrief → plan → execute` so the dashboard matches the user-facing
 * ticket-flow vocabulary. `null` means there is no execution yet (idle
 * card); we render those in the first column with a neutral status.
 */
export type WorktreeStage = ExecutionKind;

/**
 * Status sub-state inside a stage — drives the card's color chip.
 *   running  → blue   (queued/running/cancelling all collapse to this)
 *   failed   → red    (failed)
 *   done     → green  (succeeded)
 *   idle     → neutral (no execution recorded yet, or cancelled)
 */
export type WorktreeStatus = "running" | "failed" | "done" | "idle";

export interface Worktree {
  slug: string; // `${project}__${ticket_id}`
  project: string;
  ticket_id: string;
  latest: ExecutionOut | null;
  total_cost_cents: number;
  run_count: number;
  /** Workflow column the card belongs to (debrief / plan / execute). */
  stage: WorktreeStage;
  /** Visual status within the current stage. */
  status: WorktreeStatus;
}
