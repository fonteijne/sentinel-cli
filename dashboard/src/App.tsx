import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { RunDrawer } from "./components/RunDrawer";
import { Icon } from "./icons";
import { ComingSoon } from "./pages/ComingSoon";
import { Executions } from "./pages/Executions";
import { Overview } from "./pages/Overview";
import { Worktrees } from "./pages/Worktrees";
import { navigate, useDeepLink, useRoute } from "./routes";
import type { ExecutionOut } from "./types";
import { useAuth } from "./auth";

export default function App() {
  const { token, setToken, baseUrl, setBaseUrl } = useAuth();
  const route = useRoute();
  const sub = useDeepLink();

  const [executions, setExecutions] = useState<ExecutionOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [serviceHealth, setServiceHealth] = useState<"ok" | "down" | "unknown">("unknown");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.listExecutions({ baseUrl, token }, { limit: 200 });
      setExecutions(res.items);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setToken(null);
      }
      if (e instanceof ApiError) setError(`HTTP ${e.status}: ${e.detail ?? ""}`);
      else if (e instanceof Error) setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [baseUrl, token, setToken]);

  useEffect(() => {
    if (!token) return;
    refresh();
    const id = window.setInterval(refresh, 5000);
    return () => window.clearInterval(id);
  }, [refresh, token]);

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const ok = await api.health({ baseUrl, token: token ?? "" });
        if (!cancelled) setServiceHealth(ok ? "ok" : "down");
      } catch {
        if (!cancelled) setServiceHealth("down");
      }
    };
    probe();
    const id = window.setInterval(probe, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [baseUrl, token]);

  const drawerExec = useMemo(
    () => (route === "executions" && sub ? sub : null),
    [route, sub]
  );

  if (!token) {
    return (
      <TokenSplash
        baseUrl={baseUrl}
        onChangeBaseUrl={setBaseUrl}
        onSubmit={(t) => setToken(t)}
      />
    );
  }

  return (
    <>
      <div className="dash">
        <Sidebar
          current={route}
          onNavigate={(id) => navigate(id)}
          serviceHealth={serviceHealth}
        />
        <div className="main">
          <Topbar
            baseUrl={baseUrl}
            onChangeBaseUrl={setBaseUrl}
            onLogout={() => setToken(null)}
            onRefresh={refresh}
          />
          <div className="content">
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
            {route === "overview" && <Overview executions={executions} loading={loading} />}
            {route === "worktrees" && (
              <Worktrees
                baseUrl={baseUrl}
                token={token}
                executions={executions}
                loading={loading}
                onChanged={refresh}
              />
            )}
            {route === "executions" && (
              <Executions
                executions={executions}
                loading={loading}
                onRefresh={refresh}
              />
            )}
            {(route === "inbox" || route === "insights" || route === "settings") && (
              <ComingSoon pageId={route} />
            )}
          </div>
        </div>
      </div>

      {drawerExec && (
        <RunDrawer
          baseUrl={baseUrl}
          token={token}
          executionId={drawerExec}
          onClose={() => navigate("executions")}
          onChanged={refresh}
        />
      )}
    </>
  );
}

function TokenSplash({
  baseUrl,
  onChangeBaseUrl,
  onSubmit,
}: {
  baseUrl: string;
  onChangeBaseUrl: (u: string) => void;
  onSubmit: (token: string) => void;
}) {
  const [t, setT] = useState("");
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg)",
        padding: "var(--space-6)",
      }}
    >
      <div className="card" style={{ width: 460, maxWidth: "94vw", boxShadow: "var(--shadow-lg)" }}>
        <div className="card-body stack-5">
          <div className="inline-3">
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 9,
                background:
                  "linear-gradient(135deg, var(--primary), hsl(calc(var(--primary-h) + 30) var(--primary-s) calc(var(--primary-l) + 8%)))",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "white",
                fontWeight: 800,
                fontSize: 14,
              }}
            >
              S
            </div>
            <div className="stack-2">
              <div className="eyebrow">Sentinel · Command Center</div>
              <div style={{ fontWeight: 600 }}>Sign in</div>
            </div>
          </div>
          <p className="muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
            Paste your service bearer token. It is stored in this tab only
            (sessionStorage) and is never logged.
          </p>
          <div className="stack-2">
            <label className="label">API base URL</label>
            <input
              className="input"
              value={baseUrl}
              onChange={(e) => onChangeBaseUrl(e.target.value)}
            />
          </div>
          <div className="stack-2">
            <label className="label">Bearer token</label>
            <input
              className="input"
              type="password"
              value={t}
              onChange={(e) => setT(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && t) onSubmit(t.trim());
              }}
              autoFocus
            />
          </div>
          <button
            className="btn btn-primary"
            disabled={!t}
            onClick={() => onSubmit(t.trim())}
          >
            <Icon name="rocket" size={14} /> Open dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
