import { Icon } from "../icons";

interface Props {
  baseUrl: string;
  onChangeBaseUrl: (u: string) => void;
  onLogout: () => void;
  onRefresh: () => void;
}

export function Topbar({ baseUrl, onChangeBaseUrl, onLogout, onRefresh }: Props) {
  return (
    <div className="topbar">
      <div className="search input-group" style={{ maxWidth: 360 }}>
        <span className="input-icon">
          <Icon name="search" size={16} />
        </span>
        <input
          className="input"
          placeholder="Search executions, tickets, projects…"
          disabled
          title="Coming soon"
        />
      </div>
      <div style={{ flex: 1 }} />

      <div className="inline-2" title="API base URL">
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
          API
        </span>
        <input
          className="input"
          style={{ width: 220, height: "var(--control-h-sm)" }}
          value={baseUrl}
          onChange={(e) => onChangeBaseUrl(e.target.value)}
        />
      </div>

      <button className="icon-btn" onClick={onRefresh} title="Refresh">
        <Icon name="refresh" size={18} />
      </button>
      <button className="icon-btn" disabled title="Notifications (coming soon)">
        <Icon name="bell" size={18} />
      </button>
      <div
        style={{
          width: 1,
          height: 24,
          background: "var(--border)",
          margin: "0 var(--space-2)",
        }}
      />
      <button className="btn btn-secondary btn-sm" onClick={onLogout}>
        <Icon name="x" size={14} /> Sign out
      </button>
    </div>
  );
}
