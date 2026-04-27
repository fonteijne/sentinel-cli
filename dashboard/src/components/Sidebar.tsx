import { Icon } from "../icons";
import type { RouteId } from "../routes";

interface Props {
  current: RouteId;
  onNavigate: (id: RouteId) => void;
  serviceHealth: "ok" | "down" | "unknown";
}

interface NavItem {
  id: RouteId;
  label: string;
  icon: Parameters<typeof Icon>[0]["name"];
  count?: number | string;
  comingSoon?: boolean;
}

export function Sidebar({ current, onNavigate, serviceHealth }: Props) {
  const workspace: NavItem[] = [
    { id: "overview", label: "Overview", icon: "home" },
    { id: "worktrees", label: "Worktrees", icon: "branch" },
    { id: "executions", label: "Executions", icon: "layers" },
    { id: "inbox", label: "Inbox", icon: "inbox", comingSoon: true },
  ];
  const insights: NavItem[] = [
    { id: "insights", label: "Insights", icon: "chart", comingSoon: true },
    { id: "settings", label: "Settings", icon: "settings", comingSoon: true },
  ];

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 9,
            background:
              "linear-gradient(135deg, var(--primary), hsl(calc(var(--primary-h) + 30) var(--primary-s) calc(var(--primary-l) + 8%)))",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "white",
            fontWeight: 800,
            fontSize: 13,
            boxShadow:
              "0 4px 14px hsl(var(--primary-h) var(--primary-s) var(--primary-l) / 0.5)",
          }}
        >
          S
        </div>
        <span>Sentinel</span>
      </div>

      <div className="nav-section">Command Center</div>
      {workspace.map((it) => (
        <NavLink
          key={it.id}
          item={it}
          active={current === it.id}
          onClick={() => onNavigate(it.id)}
        />
      ))}

      <div className="nav-section">Operations</div>
      {insights.map((it) => (
        <NavLink
          key={it.id}
          item={it}
          active={current === it.id}
          onClick={() => onNavigate(it.id)}
        />
      ))}

      <div style={{ flex: 1 }} />

      <div
        style={{
          marginTop: "var(--space-3)",
          padding: "var(--space-3)",
          borderRadius: "var(--radius-md)",
          background: "var(--sidebar-bg-2)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-3)",
        }}
      >
        <span
          className="avatar avatar-sm"
          style={{ background: "#a78bfa33", color: "#c4b5fd", border: "none" }}
        >
          OP
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              color: "var(--sidebar-text-strong)",
              fontWeight: 600,
              fontSize: "var(--fs-sm)",
            }}
          >
            Operator
          </div>
          <div
            style={{
              color: "var(--text-subtle)",
              fontSize: "var(--fs-xs)",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background:
                  serviceHealth === "ok"
                    ? "var(--success)"
                    : serviceHealth === "down"
                      ? "var(--danger)"
                      : "var(--text-subtle)",
              }}
            />
            {serviceHealth === "ok"
              ? "Service healthy"
              : serviceHealth === "down"
                ? "Service degraded"
                : "Service unknown"}
          </div>
        </div>
      </div>
    </aside>
  );
}

function NavLink({
  item,
  active,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`nav-item${active ? " active" : ""}`}
      onClick={onClick}
      style={{
        cursor: "pointer",
        textAlign: "left",
        opacity: item.comingSoon ? 0.78 : 1,
      }}
    >
      <Icon name={item.icon} size={16} /> {item.label}
      {item.comingSoon && (
        <span
          className="nav-count"
          style={{ marginLeft: "auto", fontSize: 9, letterSpacing: 0.5 }}
        >
          SOON
        </span>
      )}
    </button>
  );
}
