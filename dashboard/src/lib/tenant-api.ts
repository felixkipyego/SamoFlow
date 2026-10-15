// Step 6.2: typed functions over /api/v1/dashboard/tenant/*, following
// lib/api.ts's own established apiFetch() pattern exactly (same CSRF/
// credentials handling, same error shape) -- a sibling module, not a
// rewrite, since app/dashboard/routes.py (auth) and app/dashboard/
// tenant_routes.py (settings) are already two separate backend route
// modules for the identical reason.
import { apiFetch } from "@/lib/api";

export interface TenantConfig {
  support_email: string | null;
  support_phone: string | null;
  support_contact_link: string | null;
  support_hours: string | null;
  support_message: string | null;
  alert_email: string | null;
  escalation_email: string | null;
  title: string;
  greeting: string;
  colors: { primary: string };
  logo_url: string | null;
  position: "left" | "right";
  privacy_notice_url: string | null;
  retention_days: number;
  escalation_retention_months: number;
  trace_text_enabled: boolean;
}

export interface SiteKey {
  id: string;
  key: string;
  allowed_origins: string[];
  environment: string;
  status: "draft" | "live" | "suspended";
}

export interface InstallSnippet {
  site_key: string;
  base_url: string | null;
}

export async function getTenantConfig(): Promise<TenantConfig> {
  return apiFetch<TenantConfig>("/api/v1/dashboard/tenant");
}

export async function updateSupportDetails(payload: {
  support_email: string | null;
  support_phone: string | null;
  support_contact_link: string | null;
  support_hours: string | null;
  support_message: string | null;
}): Promise<TenantConfig> {
  return apiFetch<TenantConfig>("/api/v1/dashboard/tenant/support-details", {
    method: "PATCH",
    body: payload,
  });
}

export async function updateAlertEmails(payload: {
  alert_email: string | null;
  escalation_email: string | null;
}): Promise<TenantConfig> {
  return apiFetch<TenantConfig>("/api/v1/dashboard/tenant/alert-emails", {
    method: "PATCH",
    body: payload,
  });
}

export async function updateWidgetLook(payload: {
  title: string;
  greeting: string;
  colors: { primary: string };
  logo_url: string | null;
  position: "left" | "right";
  privacy_notice_url: string | null;
}): Promise<TenantConfig> {
  return apiFetch<TenantConfig>("/api/v1/dashboard/tenant/widget-look", {
    method: "PATCH",
    body: payload,
  });
}

export async function updateRetention(payload: {
  retention_days: number;
  escalation_retention_months: number;
}): Promise<TenantConfig> {
  return apiFetch<TenantConfig>("/api/v1/dashboard/tenant/retention", {
    method: "PATCH",
    body: payload,
  });
}

export async function updateTraceText(payload: {
  trace_text_enabled: boolean;
}): Promise<TenantConfig> {
  return apiFetch<TenantConfig>("/api/v1/dashboard/tenant/trace-text", {
    method: "PATCH",
    body: payload,
  });
}

export async function listSiteKeys(): Promise<SiteKey[]> {
  return apiFetch<SiteKey[]>("/api/v1/dashboard/tenant/site-keys");
}

export async function createSiteKey(): Promise<SiteKey> {
  return apiFetch<SiteKey>("/api/v1/dashboard/tenant/site-keys", { method: "POST", body: {} });
}

export async function updateSiteKeyOrigins(
  siteKeyId: string,
  allowedOrigins: string[],
): Promise<SiteKey> {
  return apiFetch<SiteKey>(`/api/v1/dashboard/tenant/site-keys/${siteKeyId}/origins`, {
    method: "PATCH",
    body: { allowed_origins: allowedOrigins },
  });
}

export async function getInstallSnippet(): Promise<InstallSnippet> {
  return apiFetch<InstallSnippet>("/api/v1/dashboard/tenant/install-snippet");
}
