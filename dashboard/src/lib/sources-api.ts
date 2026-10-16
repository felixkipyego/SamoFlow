// Step 6.3: typed functions over /api/v1/dashboard/sources/* and
// /api/v1/dashboard/domains/*, following lib/api.ts's own apiFetch()
// pattern (same CSRF/credentials handling, same error shape).
import { ApiError, CSRF_COOKIE_NAME, CSRF_HEADER_NAME, apiFetch, readCookie } from "@/lib/api";

export interface Source {
  id: string;
  type: "urls" | "crawl" | "upload" | "database";
  status: "ok" | "syncing" | "error" | "disabled";
  enabled: boolean;
  refresh_interval: string;
  last_run_at: string | null;
  next_run_at: string | null;
  exceeds_plan: boolean;
  document_count: number;
  may_need_javascript: boolean;
  config: Record<string, unknown>;
}

export interface SourcesList {
  sources: Source[];
  total_indexed_pages: number;
  total_indexed_pages_limit: number | null;
}

export interface Domain {
  id: string;
  domain: string;
  method: "dns" | "file" | "meta_tag";
  status: "pending" | "verified" | "revoked";
  verification_token: string;
  verified_at: string | null;
  revoked_at: string | null;
}

export async function listSources(): Promise<SourcesList> {
  return apiFetch<SourcesList>("/api/v1/dashboard/sources");
}

export async function createUrlsSource(
  urls: string[],
  refreshInterval: string,
): Promise<Source> {
  return apiFetch<Source>("/api/v1/dashboard/sources", {
    method: "POST",
    body: { type: "urls", urls, refresh_interval: refreshInterval },
  });
}

export async function createCrawlSource(
  seedUrl: string,
  refreshInterval: string,
): Promise<Source> {
  return apiFetch<Source>("/api/v1/dashboard/sources", {
    method: "POST",
    body: { type: "crawl", seed_url: seedUrl, refresh_interval: refreshInterval },
  });
}

export async function updateUrlsSource(sourceId: string, urls: string[]): Promise<Source> {
  return apiFetch<Source>(`/api/v1/dashboard/sources/${sourceId}`, {
    method: "PATCH",
    body: { urls },
  });
}

export async function disableSource(sourceId: string): Promise<Source> {
  return apiFetch<Source>(`/api/v1/dashboard/sources/${sourceId}/disable`, { method: "POST" });
}

export async function enableSource(sourceId: string): Promise<Source> {
  return apiFetch<Source>(`/api/v1/dashboard/sources/${sourceId}/enable`, { method: "POST" });
}

export async function deleteSource(sourceId: string): Promise<void> {
  await apiFetch<void>(`/api/v1/dashboard/sources/${sourceId}`, { method: "DELETE" });
}

export async function refreshSourceNow(sourceId: string): Promise<Source> {
  return apiFetch<Source>(`/api/v1/dashboard/sources/${sourceId}/refresh-now`, {
    method: "POST",
  });
}

export async function uploadFile(file: File, sourceId?: string): Promise<Source> {
  // Deliberately NOT apiFetch() (JSON-only) -- a real multipart body,
  // but the identical CSRF-header/credentials handling by hand.
  const formData = new FormData();
  formData.append("file", file);
  const path = sourceId
    ? `/api/v1/dashboard/sources/upload?source_id=${encodeURIComponent(sourceId)}`
    : "/api/v1/dashboard/sources/upload";

  const csrfToken = readCookie(CSRF_COOKIE_NAME);
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: csrfToken ? { [CSRF_HEADER_NAME]: csrfToken } : {},
    body: formData,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `upload failed with status ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return data as Source;
}

export async function listDomains(): Promise<Domain[]> {
  return apiFetch<Domain[]>("/api/v1/dashboard/domains");
}

export async function claimDomain(
  domain: string,
  method: "dns" | "file" | "meta_tag",
): Promise<Domain> {
  return apiFetch<Domain>("/api/v1/dashboard/domains", {
    method: "POST",
    body: { domain, method },
  });
}

export async function verifyDomain(domainId: string): Promise<Domain> {
  return apiFetch<Domain>(`/api/v1/dashboard/domains/${domainId}/verify`, { method: "POST" });
}
