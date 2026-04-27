/**
 * Typed wrappers for the Tender Package Builder endpoints
 * (`src/meridian/tender/api.py`).
 *
 * Kept in its own module per the round-10 UI surface convention so the
 * primary `lib/api.ts` stays focused on the core review queues.
 */

import { meridianRequest } from "../api";

const proj = (name: string) => `/projects/${encodeURIComponent(name)}`;

export interface TradeCount {
  trade: string;
  deliverable_count: number;
}

export interface TradeListResponse {
  project: string;
  trades: TradeCount[];
}

export interface TenderBuildRequest {
  trade: string;
  format: "xlsx" | "md";
  output_dir?: string | null;
  /**
   * When true the API streams the file body. The UI uses false (default) and
   * surfaces the resulting server-side path so the operator can copy it.
   */
  stream?: boolean;
}

export interface TenderBuildResponse {
  project: string;
  trade: string;
  format: string;
  output_path: string;
  output_dir: string;
}

export interface TenderOutputDirResponse {
  project: string;
  default_output_dir: string;
}

export const tenderApi = {
  trades(projectName: string): Promise<TradeListResponse> {
    return meridianRequest<TradeListResponse>(
      `${proj(projectName)}/tender/trades`,
    );
  },

  build(
    projectName: string,
    body: TenderBuildRequest,
  ): Promise<TenderBuildResponse> {
    return meridianRequest<TenderBuildResponse>(
      `${proj(projectName)}/tender/build`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stream: false, ...body }),
      },
    );
  },

  outputDir(projectName: string): Promise<TenderOutputDirResponse> {
    return meridianRequest<TenderOutputDirResponse>(
      `${proj(projectName)}/tender/output-dir`,
    );
  },
};
