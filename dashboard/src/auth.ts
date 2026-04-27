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
  // If the dashboard is served from the same origin, default to the same host.
  const { protocol, hostname } = window.location;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return "http://localhost:8787";
  }
  return `${protocol}//${hostname}:8787`;
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
