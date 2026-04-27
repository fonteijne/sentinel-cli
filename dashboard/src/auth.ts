import { useEffect, useState } from "react";

const TOKEN_KEY = "sentinel.token";
const BASE_KEY = "sentinel.baseUrl";

export function readToken(): string | null {
  // Allow ?token=… on first load to seed sessionStorage, then strip it.
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("token");
  if (fromQuery) {
    sessionStorage.setItem(TOKEN_KEY, fromQuery);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
  }
  return sessionStorage.getItem(TOKEN_KEY);
}

export function writeToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function readBaseUrl(): string {
  return localStorage.getItem(BASE_KEY) ?? defaultBaseUrl();
}

export function writeBaseUrl(url: string): void {
  localStorage.setItem(BASE_KEY, url);
}

function defaultBaseUrl(): string {
  if (typeof window === "undefined") return "http://localhost:8787";
  // Production bundle (served by the dashboard nginx image) ships with a
  // built-in `/api/` reverse proxy to the FastAPI backend. Defaulting to
  // the same-origin proxy means no CORS, no host-port discovery, and no
  // splash-screen footgun for compose users. `vite dev` runs at 5173 with
  // no proxy, so the dev flow keeps the explicit localhost:8787 default.
  if (import.meta.env.DEV) {
    const { protocol, hostname } = window.location;
    if (hostname === "localhost" || hostname === "127.0.0.1") {
      return "http://localhost:8787";
    }
    return `${protocol}//${hostname}:8787`;
  }
  return "/api";
}

export function useAuth() {
  const [token, setToken] = useState<string | null>(() => readToken());
  const [baseUrl, setBaseUrl] = useState<string>(() => readBaseUrl());

  useEffect(() => {
    if (token === null) clearToken();
    else writeToken(token);
  }, [token]);

  useEffect(() => {
    writeBaseUrl(baseUrl);
  }, [baseUrl]);

  return { token, setToken, baseUrl, setBaseUrl };
}
