/**
 * Typed wrappers for the Legal Evidence Pack endpoints
 * (`src/meridian/evidence/api.py`).
 */

import { meridianRequest } from "../api";

const proj = (name: string) => `/projects/${encodeURIComponent(name)}`;

export interface EvidenceBuildRequest {
  /** Optional scope. Omit / null = pack the whole project. */
  deliverable_ids?: string[] | null;
}

export interface EvidenceBuildResponse {
  pack_path: string;
  deliverable_count: number;
  llm_call_count: number;
  source_count: number;
  incomplete_provenance_ids: string[];
  schema_notes: string[];
}

export interface EvidencePackListItem {
  filename: string;
  path: string;
  size_bytes: number;
  modified_at: string;
}

export const evidenceApi = {
  build(
    projectName: string,
    body: EvidenceBuildRequest = {},
  ): Promise<EvidenceBuildResponse> {
    return meridianRequest<EvidenceBuildResponse>(
      `${proj(projectName)}/evidence/build`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },

  list(projectName: string): Promise<EvidencePackListItem[]> {
    return meridianRequest<EvidencePackListItem[]>(
      `${proj(projectName)}/evidence/list`,
    );
  },
};
