// Step 6.1: the dashboard's own API client -- always same-origin
// (/api/..., this task's own cookie/origin design: Caddy routes /api on
// the dashboard hostname in production, the Vite dev proxy does the
// identical job locally, vite.config.ts), always `credentials:
// "include"` (the __Host-session cookie rides along automatically; it
// is HttpOnly, so JavaScript never reads or sets it directly), and
// attaches the CSRF header on every state-changing request (backend/app/
// dashboard/csrf.py's own double-submit pattern: the __Host-csrf cookie
// is deliberately NOT HttpOnly specifically so this code can read it).
export const CSRF_COOKIE_NAME = "__Host-csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split("; ")) {
    if (part.startsWith(prefix)) {
      return decodeURIComponent(part.slice(prefix.length));
    }
  }
  return null;
}

/** Called once, before the login form is ever submitted -- primes the
 * CSRF cookie so the very first state-changing request (login itself)
 * already has one to echo back (closes the "login CSRF" gap, see
 * backend/app/dashboard/csrf.py's own header comment).
 */
export async function ensureCsrfCookie(): Promise<void> {
  await fetch("/api/v1/dashboard/auth/csrf", {
    method: "GET",
    credentials: "include",
  });
}

export async function apiFetch<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (method !== "GET" && method !== "HEAD") {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers[CSRF_HEADER_NAME] = csrfToken;
    }
  }

  const response = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `request failed with status ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return data as T;
}

export interface DashboardUser {
  id: string;
  email: string;
}

export interface DashboardTenant {
  id: string;
  name: string;
}

export interface DashboardSessionResponse {
  user: DashboardUser;
  tenant: DashboardTenant;
}

export async function login(email: string, password: string): Promise<DashboardSessionResponse> {
  return apiFetch<DashboardSessionResponse>("/api/v1/dashboard/auth/login", {
    method: "POST",
    body: { email, password },
  });
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/api/v1/dashboard/auth/logout", { method: "POST" });
}

export async function me(): Promise<DashboardSessionResponse> {
  return apiFetch<DashboardSessionResponse>("/api/v1/dashboard/auth/me");
}
