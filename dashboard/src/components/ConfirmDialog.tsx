import { useState } from "react";

interface Props {
  open: boolean;
  title: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  /** When set, the user must type this exact string to enable the confirm button. */
  typeToConfirm?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText = "Confirm",
  cancelText = "Cancel",
  typeToConfirm,
  destructive,
  onConfirm,
  onCancel,
}: Props) {
  const [typed, setTyped] = useState("");
  if (!open) return null;
  const ok = typeToConfirm ? typed === typeToConfirm : true;
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
      onClick={onCancel}
    >
      <div
        className="card"
        style={{ width: 420, maxWidth: "92vw", boxShadow: "var(--shadow-xl)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-head">
          <div className="card-title">{title}</div>
        </div>
        <div className="card-body stack-3">
          <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
            {description}
          </div>
          {typeToConfirm && (
            <div className="stack-2">
              <label
                className="label"
                style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}
              >
                Type{" "}
                <code className="font-mono">{typeToConfirm}</code> to continue
              </label>
              <input
                className="input"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoFocus
              />
            </div>
          )}
        </div>
        <div
          className="card-foot"
          style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
        >
          <button className="btn btn-secondary btn-sm" onClick={onCancel}>
            {cancelText}
          </button>
          <button
            className={`btn btn-sm ${destructive ? "btn-danger" : "btn-primary"}`}
            disabled={!ok}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
