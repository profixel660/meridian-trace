import { apiFetch } from "@/lib/fetcher";
import type { ConflictItem } from "@/lib/api";

export type ConflictRegisterStatusFilter = "all" | "pending" | "resolved" | "superseded";

export interface ConflictRegisterCounts {
  all: number;
  pending: number;
  resolved: number;
  superseded: number;
}

export interface ConflictRegisterResponse {
  items: ConflictItem[];
  counts: ConflictRegisterCounts;
}

export async function fetchConflictRegister(
  slug: string, status: ConflictRegisterStatusFilter = "all",
): Promise<ConflictRegisterResponse> {
  return apiFetch<ConflictRegisterResponse>(
    `/projects/${encodeURIComponent(slug)}/conflict-register?status=${status}`,
  );
}

export function conflictRegisterXlsxUrl(slug: string): string {
  return `/api/projects/${encodeURIComponent(slug)}/conflict-register.xlsx`;
}
