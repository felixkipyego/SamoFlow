// Step 6.4: typed functions over /api/v1/dashboard/escalations/* and
// /api/v1/dashboard/usage, following lib/api.ts's own apiFetch()
// pattern exactly (same CSRF/credentials handling, same error shape).
import { apiFetch } from "@/lib/api";

export interface EscalationSummary {
  id: string;
  status: "new" | "seen" | "resolved";
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  created_at: string;
}

export interface EscalationDetail extends EscalationSummary {
  // Already markdown-unescaped by the server -- render as plain text
  // (never via dangerouslySetInnerHTML).
  transcript: string;
}

export interface EscalationsList {
  escalations: EscalationSummary[];
}

export interface Usage {
  plan_assigned: boolean;
  quota: number | null;
  used_this_month: number;
  fraction_used: number | null;
  state: "no_plan" | "normal" | "warning" | "exceeded";
}

export async function listEscalations(): Promise<EscalationsList> {
  return apiFetch<EscalationsList>("/api/v1/dashboard/escalations");
}

export async function getOpenEscalationCount(): Promise<{ count: number }> {
  return apiFetch<{ count: number }>("/api/v1/dashboard/escalations/open-count");
}

export async function getEscalation(id: string): Promise<EscalationDetail> {
  return apiFetch<EscalationDetail>(`/api/v1/dashboard/escalations/${id}`);
}

export async function updateEscalationStatus(
  id: string,
  status: "seen" | "resolved",
): Promise<EscalationSummary> {
  return apiFetch<EscalationSummary>(`/api/v1/dashboard/escalations/${id}/status`, {
    method: "PATCH",
    body: { status },
  });
}

export async function deleteEscalation(id: string): Promise<void> {
  await apiFetch<void>(`/api/v1/dashboard/escalations/${id}`, { method: "DELETE" });
}

export async function getUsage(): Promise<Usage> {
  return apiFetch<Usage>("/api/v1/dashboard/usage");
}
