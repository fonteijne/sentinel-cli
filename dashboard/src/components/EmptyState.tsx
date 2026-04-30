import type { ReactNode } from "react";
import { Icon, type IconName } from "../icons";

interface Props {
  icon?: IconName;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon = "inbox", title, description, action }: Props) {
  return (
    <div
      className="stack-3"
      style={{
        textAlign: "center",
        padding: "var(--space-12) var(--space-6)",
        border: "1px dashed var(--border)",
        borderRadius: "var(--radius-lg)",
        background: "var(--surface-2)",
        color: "var(--text-muted)",
      }}
    >
      <div
        style={{
          margin: "0 auto",
          width: 44,
          height: 44,
          borderRadius: "var(--radius-md)",
          background: "var(--surface-3)",
          color: "var(--text-subtle)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Icon name={icon} size={22} />
      </div>
      <div style={{ color: "var(--text)", fontWeight: 600 }}>{title}</div>
      {description && (
        <div style={{ fontSize: "var(--fs-sm)", maxWidth: 420, margin: "0 auto" }}>
          {description}
        </div>
      )}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}
