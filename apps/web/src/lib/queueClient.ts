/**
 * Typed wrappers for the mutating review-queue endpoints.
 *
 * Mirrors `meridian.api.main`'s POST surface for accept/reject/edit
 * deliverables, promote audit rows, resolve/dismiss questions, resolve
 * conflicts, and confirm/merge/reject taxonomy proposals.
 *
 * All wrappers return the parsed JSON body or throw `MeridianApiError`.
 * Pages compose these inside client components and use `router.refresh()`
 * to re-render server-side data after a successful mutation.
 */

import { meridianRequest } from "./api";

const proj = (name: string) => `/projects/${encodeURIComponent(name)}`;

// ─── Deliverables ──────────────────────────────────────────────────────────

export interface AcceptDeliverableInput {
  resolved_by_question_id?: string | null;
}
export interface RejectDeliverableInput {
  reason?: string | null;
}
export interface EditDeliverableInput {
  summary?: string | null;
  trade_value?: string | null;
  service_value?: string | null;
  category_value?: string | null;
  applicable_standards?: string[] | null;
  flags?: string[] | null;
  flag_context?: Record<string, unknown> | null;
}

export interface DeliverableMutationResult {
  before_status: string;
  after_status: string;
  deliverable_id: string;
  parent_deliverable_id: string | null;
}

export const queueClient = {
  acceptDeliverable(
    projectName: string,
    deliverableId: string,
    input: AcceptDeliverableInput = {},
  ): Promise<DeliverableMutationResult> {
    return meridianRequest<DeliverableMutationResult>(
      `${proj(projectName)}/deliverables/${encodeURIComponent(
        deliverableId,
      )}/accept`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  rejectDeliverable(
    projectName: string,
    deliverableId: string,
    input: RejectDeliverableInput = {},
  ): Promise<DeliverableMutationResult> {
    return meridianRequest<DeliverableMutationResult>(
      `${proj(projectName)}/deliverables/${encodeURIComponent(
        deliverableId,
      )}/reject`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  editDeliverable(
    projectName: string,
    deliverableId: string,
    input: EditDeliverableInput,
  ): Promise<DeliverableMutationResult> {
    return meridianRequest<DeliverableMutationResult>(
      `${proj(projectName)}/deliverables/${encodeURIComponent(
        deliverableId,
      )}/edit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  // ─── Audit ────────────────────────────────────────────────────────────────

  promoteAudit(
    projectName: string,
    auditId: string,
    input: {
      trade_value?: string | null;
      service_value?: string | null;
      category_value?: string | null;
      summary?: string | null;
      flags?: string[] | null;
      applicable_standards?: string[] | null;
    } = {},
  ): Promise<unknown> {
    return meridianRequest(
      `${proj(projectName)}/audit/${encodeURIComponent(auditId)}/promote`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  // ─── Questions ───────────────────────────────────────────────────────────

  resolveQuestion(
    projectName: string,
    questionId: string,
    input: {
      resolution_payload: Record<string, unknown>;
      mark_status?: "resolved" | "dismissed";
    },
  ): Promise<unknown> {
    return meridianRequest(
      `${proj(projectName)}/questions/${encodeURIComponent(
        questionId,
      )}/resolve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  dismissQuestion(
    projectName: string,
    questionId: string,
    input: { reason?: string | null } = {},
  ): Promise<unknown> {
    return meridianRequest(
      `${proj(projectName)}/questions/${encodeURIComponent(
        questionId,
      )}/dismiss`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  // ─── Conflicts ───────────────────────────────────────────────────────────

  resolveConflict(
    projectName: string,
    conflictId: string,
    input: {
      action: "accept_a" | "accept_b" | "reject_both" | "hybrid";
      accept_party_id?: string | null;
      hybrid_summary?: string | null;
      hybrid_trade_value?: string | null;
      hybrid_service_value?: string | null;
      hybrid_category_value?: string | null;
      hybrid_flags?: string[] | null;
    },
  ): Promise<unknown> {
    return meridianRequest(
      `${proj(projectName)}/conflicts/${encodeURIComponent(
        conflictId,
      )}/resolve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  // ─── Taxonomy ────────────────────────────────────────────────────────────

  confirmTaxonomy(
    projectName: string,
    input: {
      table: "trade" | "service" | "category";
      value: string;
    },
  ): Promise<unknown> {
    return meridianRequest(`${proj(projectName)}/taxonomy/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  },

  mergeTaxonomy(
    projectName: string,
    input: {
      table: "trade" | "service" | "category";
      source_value: string;
      target_value: string;
    },
  ): Promise<unknown> {
    return meridianRequest(`${proj(projectName)}/taxonomy/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  },

  rejectTaxonomy(
    projectName: string,
    input: {
      table: "trade" | "service" | "category";
      value: string;
    },
  ): Promise<unknown> {
    return meridianRequest(`${proj(projectName)}/taxonomy/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  },
};

/**
 * Turn any thrown value into a PM-friendly string with next-step guidance.
 * Surfaced via ToastHost on action failure.
 */
export function explainQueueError(err: unknown): string {
  const base =
    err instanceof Error ? err.message : typeof err === "string" ? err : "";
  if (
    base.includes("Failed to fetch") ||
    base.includes("NetworkError") ||
    base.toLowerCase().includes("could not")
  ) {
    return "Couldn't reach the Meridian API. Run `uv run uvicorn meridian.api.main:app --reload --port 8000` from the project root and reload.";
  }
  return base || "Something went wrong. Try again, or reload the page.";
}
