/**
 * Thin typed client for the Meridian FastAPI backend.
 *
 * Mirrors the endpoints defined in `src/meridian/api/main.py`. Read-side
 * wrappers live here; mutating endpoints (review actions) live in
 * `lib/queueClient.ts` to keep this file readable.
 *
 * Round-12 auth: every call here flows through `withAuth(...)` which
 * injects `Authorization: Bearer <token>` when a session is live. POST/
 * PUT/DELETE endpoints require a token (§3.2 enforcement); GETs work
 * unauthenticated and degrade gracefully when no token is stored.
 *
 * 401 handling: the browser-side `apiFetch` wrapper in `lib/fetcher.ts`
 * is the canonical entry point for client components — it clears the
 * stale token and redirects to `/login?from=...`. `meridianRequest` is
 * used directly by server components (where there is no localStorage and
 * therefore no bearer to inject); 401s there bubble up as `MeridianApiError`
 * and the page surfaces them via `ApiErrorPanel`.
 */

import { withAuth } from "./auth";

// Empty default = same-origin relative URLs. The static export is served
// by the FastAPI backend itself, so the page origin (whether it's
// `127.0.0.1:8000`, `localhost:8000`, a future Tauri custom protocol, or
// an LAN IP) is exactly the API origin — no CORS, no host-mismatch.
// Set `NEXT_PUBLIC_MERIDIAN_API` at build time to override (e.g. for a
// deployment that splits frontend and backend across different hosts).
const API_BASE = process.env.NEXT_PUBLIC_MERIDIAN_API ?? "";

export class MeridianApiError extends Error {
  status: number;
  body: string;
  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = "MeridianApiError";
    this.status = status;
    this.body = body;
  }
}

export async function meridianRequest<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const { revalidate, ...rest } = init ?? {};
  const authed = withAuth(rest);
  const mergedHeaders = new Headers(authed.headers ?? {});
  if (!mergedHeaders.has("Accept")) {
    mergedHeaders.set("Accept", "application/json");
  }
  const res = await fetch(url, {
    ...authed,
    headers: mergedHeaders,
    next: { revalidate: revalidate ?? 0 },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    const err = new MeridianApiError(
      `Meridian API ${res.status} ${res.statusText} for ${path}`,
      res.status,
      body,
    );
    // Browser-side 401 → drop the stale token and redirect to /login so
    // the user can re-auth and come back via `?from=...`. Server-side
    // (SSR) 401 must continue to throw so the page can render
    // `ApiErrorPanel` — server has no localStorage / window anyway.
    if (
      res.status === 401 &&
      typeof window !== "undefined" &&
      !path.startsWith("/auth/")
    ) {
      // Lazy-import avoids an SSR-time circular dependency between
      // api.ts ↔ fetcher.ts ↔ auth.ts.
      const { handleUnauthorizedRedirect } = await import("./fetcher");
      handleUnauthorizedRedirect();
    }
    throw err;
  }
  return (await res.json()) as T;
}

// Local alias preserves the previous private helper name used below.
const request = meridianRequest;

// --- Response types -------------------------------------------------------

export interface HealthResponse {
  status: string;
  version: string;
}

export interface ProjectStatus {
  sources: number;
  deliverables_total: number;
  deliverables_master: number;
  audit_outside: number;
  questions_pending: number;
  extraction_jobs: number;
  llm_calls: number;
}

export interface ProjectListItem {
  name: string;
  db_path: string;
  created_at: string;
  deliverables_master: number;
  sources: number;
  questions_pending: number;
  conflicts_pending: number;
}

export interface SourceRef {
  // Schema is intentionally loose — see CONTEXT.md §7. Shape varies by source
  // type (page+clause for PDFs, sheet+bbox for drawings, etc.).
  [key: string]: unknown;
}

export interface MasterRow {
  id: string;
  summary: string | null;
  trade: string | null;
  service: string | null;
  category: string | null;
  confidence: number | string | null;
  flags: string[];
  applicable_standards: string[];
  source_ref: SourceRef;
  source_document: string | null;
  document_class: string | null;
  document_state: string | null;
}

export interface SourceItem {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  imported_at: string;
  document_class: string | null;
  document_state: string | null;
  revision: string | null;
  is_template: boolean;
  is_demarcation_schedule: boolean;
  extraction_path: string;
  deliverables_count: number;
  audit_count: number;
  quality_scan_summary: string | null;
}

export interface QuestionItem {
  id: string;
  kind: string;
  context: string;
  question_text: string;
  candidate_source_refs: unknown[];
  proposed_resolution: string | null;
  created_at: string;
  extraction_job_id: string;
}

export interface ConflictParty {
  party_kind: "deliverable" | "audit";
  party_id: string;
  party_position: string | null;
  summary_or_text: string;
}

export interface ConflictItem {
  id: string;
  kind: string;
  status: string;
  most_onerous_party_id: string | null;
  most_onerous_reasoning: string;
  created_at: string;
  resolved_at: string | null;
  parties: ConflictParty[];
}

export interface AuditItem {
  id: string;
  source_id: string;
  source_document: string;
  source_ref: SourceRef;
  candidate_text: string;
  rejection_reason: string;
  landlord_response: string | null;
  user_promoted_to_deliverable_id: string | null;
  created_at: string;
}

export interface TaxonomyProposal {
  table: "trade" | "service" | "category";
  id: string;
  value: string;
  source: string;
  created_at: string;
  in_use_count: number;
  /** Round-15 auto-assessment recommendation; null on legacy rows that
   * pre-date the bootstrap LLM auto-assessment pass (DECISIONS.md §3.10). */
  llm_recommended_action?: "confirm" | "merge_into" | "defer_to_user" | null;
  /** Suggested target value when llm_recommended_action === "merge_into". */
  llm_merge_target?: string | null;
  /** 0.0–1.0 LLM confidence in its recommendation; null on legacy rows. */
  llm_confidence?: number | null;
  /** Short LLM-generated rationale; null on legacy rows. */
  llm_reasoning?: string | null;
}

export interface QuarantinedItem {
  deliverable_id: string;
  extraction_group_id: string;
  source_id: string;
  source_filename: string | null;
  summary: string;
  confidence: string;
  flags: string[];
  flag_context: Record<string, unknown>;
  trade: string | null;
  service: string | null;
  category: string | null;
  gate_outcome: string;
  status: string;
  created_at: string;
}

export interface StatusBreakdown {
  auto_approved: number;
  user_accepted: number;
  user_edited: number;
  user_promoted: number;
  user_rejected: number;
  quarantined: number;
  total: number;
}

export interface ProjectCoverage {
  project_name: string;
  schema_version: number;
  sources_imported: number;
  sources_scanned: number;
  sources_extracted: number;
  sources_pending_extraction: number;
  deliverable_status: StatusBreakdown;
  audit: { total: number; promoted: number; pending: number };
  questions: {
    pending: number;
    resolved: number;
    dismissed: number;
    total: number;
  };
  conflicts: {
    pending: number;
    resolved_accept_a: number;
    resolved_accept_b: number;
    resolved_hybrid: number;
    resolved_reject_both: number;
    total: number;
  };
  taxonomy: {
    pending_proposals: number;
    confirmed_user_added: number;
    default_count: number;
    by_table: Record<string, Record<string, number>>;
  };
  provenance: {
    total_deliverables: number;
    with_source_id: number;
    with_chunk_id: number;
    with_extraction_job_id: number;
    with_llm_call_id: number;
    with_source_ref: number;
    fully_provenanced: number;
    fully_provenanced_pct: number;
  };
  cost: {
    total_calls: number;
    total_cost_cents: number;
    calls_with_null_cost: number;
    cost_pct_known: number;
  };
  review_coverage_pct: number;
  auto_route_rate_pct: number;
  pending_decisions: number;
  last_extraction_at: string | null;
  last_llm_call_at: string | null;
  last_review_action_at: string | null;
  is_baseline_trustworthy: boolean;
  baseline_trust_blockers: string[];
}

// --- Public client --------------------------------------------------------

export const meridianApi = {
  apiBase: API_BASE,

  health(): Promise<HealthResponse> {
    return request<HealthResponse>("/health");
  },

  projects(): Promise<ProjectListItem[]> {
    return request<ProjectListItem[]>("/projects");
  },

  projectStatus(name: string): Promise<ProjectStatus> {
    return request<ProjectStatus>(
      `/projects/${encodeURIComponent(name)}/status`,
    );
  },

  projectCoverage(name: string): Promise<ProjectCoverage> {
    return request<ProjectCoverage>(
      `/projects/${encodeURIComponent(name)}/coverage`,
    );
  },

  projectMaster(name: string): Promise<MasterRow[]> {
    return request<MasterRow[]>(
      `/projects/${encodeURIComponent(name)}/master`,
    );
  },

  projectSources(name: string): Promise<SourceItem[]> {
    return request<SourceItem[]>(
      `/projects/${encodeURIComponent(name)}/sources`,
    );
  },

  projectQuestions(
    name: string,
    status: "pending" | "resolved" | "dismissed" | "all" = "pending",
  ): Promise<QuestionItem[]> {
    return request<QuestionItem[]>(
      `/projects/${encodeURIComponent(name)}/questions?status=${status}`,
    );
  },

  projectConflicts(
    name: string,
    status = "pending",
  ): Promise<ConflictItem[]> {
    return request<ConflictItem[]>(
      `/projects/${encodeURIComponent(name)}/conflicts?status=${status}`,
    );
  },

  projectAudit(name: string, sourceId?: string): Promise<AuditItem[]> {
    const qs = sourceId
      ? `?source_id=${encodeURIComponent(sourceId)}`
      : "";
    return request<AuditItem[]>(
      `/projects/${encodeURIComponent(name)}/audit${qs}`,
    );
  },

  projectTaxonomyPending(name: string): Promise<TaxonomyProposal[]> {
    return request<TaxonomyProposal[]>(
      `/projects/${encodeURIComponent(name)}/taxonomy/pending`,
    );
  },

  /**
   * Quarantined deliverables. The backend exposes the list via the
   * `meridian.review.deliverables.list_quarantined` library function;
   * the HTTP wrapper is expected at `GET /projects/{name}/quarantine`
   * (matching naming used by the queue actions). If the endpoint isn't
   * yet wired, the page surfaces a clear "next steps" error message.
   */
  projectQuarantine(name: string): Promise<QuarantinedItem[]> {
    return request<QuarantinedItem[]>(
      `/projects/${encodeURIComponent(name)}/quarantine`,
    );
  },
};
