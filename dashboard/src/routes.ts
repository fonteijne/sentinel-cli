import { useEffect, useState } from "react";

export type RouteId =
  | "overview"
  | "worktrees"
  | "executions"
  | "inbox"
  | "insights"
  | "settings";

const ALL: readonly RouteId[] = [
  "overview",
  "worktrees",
  "executions",
  "inbox",
  "insights",
  "settings",
];

export function readRoute(): RouteId {
  if (typeof window === "undefined") return "overview";
  const hash = window.location.hash.replace(/^#\/?/, "");
  const id = hash.split("/")[0] as RouteId;
  return ALL.includes(id) ? id : "overview";
}

export function navigate(id: RouteId, sub?: string): void {
  const target = sub ? `#/${id}/${sub}` : `#/${id}`;
  if (window.location.hash !== target) {
    window.location.hash = target;
  }
}

export function useRoute(): RouteId {
  const [route, setRoute] = useState<RouteId>(() => readRoute());
  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

export function useDeepLink(): string | null {
  // Read /<route>/<sub> sub-segment for drawers (e.g. #/executions/abcd-1234).
  const [sub, setSub] = useState<string | null>(() => readSub());
  useEffect(() => {
    const onHash = () => setSub(readSub());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return sub;
}

function readSub(): string | null {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const parts = hash.split("/");
  return parts.length >= 2 && parts[1] ? parts[1] : null;
}
