import type { ReactNode } from "react";
import { Badge } from "../components/Badge";
import { Icon, type IconName } from "../icons";

interface Item {
  icon: IconName;
  title: string;
  description: string;
}

const PAGES: Record<string, { title: string; eyebrow: string; intro: string; items: Item[] }> = {
  inbox: {
    eyebrow: "Tickets · Jira & GitLab",
    title: "Inbox",
    intro:
      "A unified, read-only view over Jira issues and GitLab MRs assigned to you, with a one-click 'Plan it' that calls POST /executions. Backend proxy endpoints are not implemented yet.",
    items: [
      { icon: "ticket", title: "Assigned Jira tickets", description: "Pull from JiraClient via a future /tickets endpoint." },
      { icon: "branch", title: "Open GitLab MRs", description: "Mirror open MRs touched by Sentinel runs." },
      { icon: "rocket", title: "Plan from inbox", description: "Bypass the worktree board entirely for triage." },
    ],
  },
  insights: {
    eyebrow: "Insights",
    title: "Cost & duration analytics",
    intro:
      "Daily / weekly / monthly cost-by-project, top tickets by spend, average run duration, success rate. Today, the dashboard reports point-in-time numbers from /executions only.",
    items: [
      { icon: "chart", title: "Cost trend", description: "Stacked area of cost_cents per project per day." },
      { icon: "target", title: "Success rate", description: "Rolling 7-day succeeded / (succeeded + failed) by kind." },
      { icon: "alert", title: "Findings & test results", description: "Will surface finding.posted and test.result events once the orchestrator emits them (gap G-04)." },
    ],
  },
  settings: {
    eyebrow: "Settings",
    title: "Service configuration",
    intro:
      "View the live config, rotate the bearer token, manage CORS allowlists. Backend currently exposes config only through YAML on disk; an admin API is not implemented yet.",
    items: [
      { icon: "settings", title: "Live config view", description: "Read-only diff of effective config vs. defaults." },
      { icon: "users", title: "Per-user tokens", description: "Replace the shared bearer with per-user tokens + roles." },
      { icon: "stop", title: "Drain mode", description: "Operator-level cancel-all + reject-new switch." },
    ],
  },
};

export function ComingSoon({ pageId }: { pageId: string }) {
  const cfg = PAGES[pageId] ?? PAGES.inbox!;
  return (
    <div className="stack-5">
      <div className="between" style={{ flexWrap: "wrap", gap: "var(--space-3)" }}>
        <div className="stack-2" style={{ minWidth: 0 }}>
          <div className="eyebrow">{cfg.eyebrow}</div>
          <h2 className="h4" style={{ margin: 0 }}>
            {cfg.title} <Badge tone="warning" dot>coming soon</Badge>
          </h2>
        </div>
      </div>
      <div className="card">
        <div className="card-body stack-4">
          <p className="muted" style={{ fontSize: "var(--fs-md)", maxWidth: 720 }}>
            {cfg.intro}
          </p>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            {cfg.items.map((it) => (
              <Card key={it.title} icon={it.icon} title={it.title}>
                {it.description}
              </Card>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Card({ icon, title, children }: { icon: IconName; title: string; children: ReactNode }) {
  return (
    <div
      style={{
        padding: "var(--space-4)",
        border: "1px dashed var(--border-strong)",
        borderRadius: "var(--radius-md)",
        background: "var(--surface-2)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div className="inline-2">
        <span
          className="kpi-icon"
          style={{ width: 28, height: 28, background: "var(--primary-soft)", color: "var(--primary)" }}
        >
          <Icon name={icon} size={14} />
        </span>
        <div style={{ fontWeight: 600 }}>{title}</div>
      </div>
      <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {children}
      </div>
    </div>
  );
}
