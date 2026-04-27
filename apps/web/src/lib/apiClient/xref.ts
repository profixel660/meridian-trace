/**
 * Typed wrappers for the cross-reference sweep endpoints
 * (`src/meridian/extract/cross_references_api.py`).
 *
 * NOTE: round-11's classification fix is rolling out in parallel; the API
 * may still return only `borderline` until that lands. The UI handles
 * whatever outcome string the backend returns and groups unknown values
 * under the `borderline` bucket so reviewers see them safely.
 */

import { MeridianApiError, meridianRequest } from "../api";

const proj = (name: string) => `/projects/${encodeURIComponent(name)}`;

export interface SweepRequest {
  llm_assist?: boolean;
  dry_run?: boolean;
}

export interface SweepResponse {
  sweep_run_id: string;
  started_at: string;
  finished_at: string;
  status: string;
  sources_scanned: number;
  deliverables_scanned: number;
  findings_total: number;
  confirmed: number;
  borderline: number;
  rejected: number;
  report_csv_path: string | null;
  report_md_path: string | null;
  dry_run: boolean;
  llm_assist: boolean;
}

/**
 * Anchor kinds from the Python side. Kept open-ended (string) because the
 * backend may add new kinds without breaking the UI table.
 */
export type AnchorKind =
  | "section"
  | "standard"
  | "drawing"
  | "equipment_tag"
  | "owner_id"
  | "spec"
  | string;

/**
 * Outcome values. The round-11 classification fix introduces
 * `external_reference`; the table groups unknown strings under
 * `borderline` to stay safe.
 */
export type XrefOutcome =
  | "confirmed"
  | "borderline"
  | "external_reference"
  | "rejected"
  | string;

export interface XrefFinding {
  from_source_id: string;
  to_source_id: string | null;
  to_deliverable_id: string | null;
  anchor_kind: AnchorKind;
  raw_match: string;
  normalised_match: string;
  detection_method: string;
  outcome: XrefOutcome;
  confidence: string;
  reason: string | null;
  review_question_id: string | null;
}

export interface XrefReportResponse {
  sweep_run_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  sources_scanned: number;
  deliverables_scanned: number;
  references_confirmed: number;
  references_borderline: number;
  references_rejected: number;
  report_csv_path: string | null;
  report_md_path: string | null;
  findings: XrefFinding[];
}

export const xrefApi = {
  sweep(projectName: string, body: SweepRequest = {}): Promise<SweepResponse> {
    return meridianRequest<SweepResponse>(
      `${proj(projectName)}/xref/sweep`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm_assist: false,
          dry_run: false,
          ...body,
        }),
      },
    );
  },

  /**
   * Returns the latest sweep report. Resolves to `null` (instead of throwing)
   * when the backend reports 404 — the page treats that as "no sweep yet"
   * and shows the empty-state tutorial.
   */
  async report(projectName: string): Promise<XrefReportResponse | null> {
    try {
      return await meridianRequest<XrefReportResponse>(
        `${proj(projectName)}/xref/report`,
      );
    } catch (err) {
      if (err instanceof MeridianApiError && err.status === 404) {
        return null;
      }
      throw err;
    }
  },
};
