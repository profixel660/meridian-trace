/**
 * Typed client for the `/setup/*` HTTP endpoints (round-17). Mirrors the
 * shape of `lib/apiClient/{tender,evidence,xref}.ts`.
 *
 * Contract is reconciled against Stream C's actual API (see Stream C report
 * + `src/meridian/wizard/`):
 *
 *   GET  /setup/state            → SetupState
 *   POST /setup/api-key          → ApiKeyValidationResponse
 *   POST /setup/projects         → 200 {created:true, slug, db_path}
 *                                  409 {detail: {error: "slug_exists",
 *                                       existing_db_path}}
 *                                  400 {detail: {error: "projects_dir_not_writeable",
 *                                       message}}
 *   POST /setup/import           → {job_id}
 *   GET  /setup/import/{job_id}  → ImportJobStatus  (status:
 *                                       pending|running|succeeded|failed,
 *                                       total/completed/imported/deduped/errors)
 *   POST /setup/import/skip      → {acknowledged:true}
 *   POST /setup/complete         → SetupState  (idempotent)
 *
 * Auth posture: the wizard runs BEFORE the user has a bearer token. All
 * `/setup/*` endpoints are public; `apiFetch` only injects an
 * `Authorization` header when one is stored in localStorage, so the
 * pre-auth path flows cleanly.
 *
 * `createProject` and `importStatus` are adapters that normalise Stream C's
 * raw HTTP shapes into the three-outcome / progress shapes the wizard
 * pages were authored against. Keeping the adapters here means the pages
 * never reach into `MeridianApiError.body` and never JSON-parse error
 * envelopes — that responsibility belongs in one place.
 */

import { MeridianApiError } from "./api";
import { apiFetch } from "./fetcher";

/** Aggregate setup state — drives wizard resume + the `/` redirect. */
export interface SetupState {
  /** True iff all required steps are done. */
  complete: boolean;
  /** True once a valid (or unable-to-verify) Anthropic key has been saved. */
  api_key_set: boolean;
  /** Slug of the first project, or null if not yet created. */
  first_project_slug: string | null;
  /** Display name of the first project, or null if not yet created. */
  first_project_name: string | null;
  /** Resolved projects-root path for the first project, or null. */
  first_project_dir: string | null;
  /** Number of documents imported into the first project. */
  documents_imported: number;
  /** True if the user explicitly skipped the documents step. */
  documents_skipped: boolean;
  /** Next wizard step the user should land on. */
  next_step:
    | "api_key"
    | "first_project"
    | "first_documents"
    | "ready"
    | "complete";
}

export const DEFAULT_SETUP_STATE: SetupState = {
  complete: false,
  api_key_set: false,
  first_project_slug: null,
  first_project_name: null,
  first_project_dir: null,
  documents_imported: 0,
  documents_skipped: false,
  next_step: "api_key",
};

export type ValidationOutcome = "valid" | "invalid" | "unable_to_verify";

export interface ApiKeyValidationResponse {
  outcome: ValidationOutcome;
  message: string;
}

/**
 * Three-outcome create-project shape the wizard page renders. Adapter
 * translates from the raw HTTP shape (200 / 409 / 400) Stream C ships.
 */
export interface CreateProjectResponse {
  outcome: "created" | "conflict" | "invalid";
  /** Human-readable message safe to render verbatim. */
  message: string;
  /** Slug of the created or conflicting project. */
  slug: string | null;
  /** Absolute path on disk for the created or conflicting project. */
  project_path: string | null;
  /** OS error string when outcome === "invalid". */
  os_error?: string | null;
}

export interface ImportRequest {
  project_slug: string;
  paths: string[];
}

export interface ImportResponse {
  job_id: string;
}

/**
 * Adapted import-job status. Stream C ships flat aggregate counts; this
 * shape adds a derived `progress` fraction and maps the four-state status
 * to the wizard's three-outcome terminal vocabulary so the existing page
 * UX (progress bar + outcome blocks) keeps working.
 */
export interface ImportJobStatus {
  job_id: string;
  /** Raw backend status from Stream C. */
  raw_status: "pending" | "running" | "succeeded" | "failed";
  /**
   * Wizard-facing terminal state. While the job is in-flight, this is
   * `running`. On terminal: `imported_all` (no errors), `imported_partial`
   * (some errors but at least one imported), or `failed` (zero imported
   * OR raw_status === "failed").
   */
  status:
    | "queued"
    | "running"
    | "imported_all"
    | "imported_partial"
    | "failed";
  /** 0..1 fraction; 1.0 when terminal. */
  progress: number;
  total: number;
  completed: number;
  imported: number;
  deduped: number;
  /** Flat list of error messages — Stream C does not surface per-file. */
  errors: string[];
}

interface RawImportJobStatus {
  job_id: string;
  status: "pending" | "running" | "succeeded" | "failed";
  total: number;
  completed: number;
  imported: number;
  deduped: number;
  errors: string[];
}

interface RawCreateSuccess {
  created: true;
  slug: string;
  db_path: string;
}

interface RawConflictDetail {
  error: "slug_exists";
  existing_db_path: string;
}

interface RawInvalidDetail {
  error:
    | "projects_dir_not_writeable"
    | "projects_dir_not_a_directory"
    | "projects_dir_required"
    | "name_required"
    | "slug_required"
    | "slug_invalid"
    | string;
  message: string;
}

function safeJsonParse<T>(body: string): T | null {
  try {
    return JSON.parse(body) as T;
  } catch {
    return null;
  }
}

export const setupApi = {
  state(): Promise<SetupState> {
    return apiFetch<SetupState>("/setup/state");
  },

  validateApiKey(key: string): Promise<ApiKeyValidationResponse> {
    return apiFetch<ApiKeyValidationResponse>("/setup/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  },

  async createProject(
    name: string,
    slug: string,
    projectsDir: string,
  ): Promise<CreateProjectResponse> {
    try {
      const res = await apiFetch<RawCreateSuccess>("/setup/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug, projects_dir: projectsDir }),
      });
      return {
        outcome: "created",
        message: "Project created.",
        slug: res.slug,
        project_path: res.db_path,
      };
    } catch (err) {
      if (err instanceof MeridianApiError) {
        const parsed = safeJsonParse<{
          detail?: RawConflictDetail | RawInvalidDetail | string;
        }>(err.body);
        const detail = parsed?.detail;

        if (
          err.status === 409 &&
          detail &&
          typeof detail === "object" &&
          detail.error === "slug_exists"
        ) {
          const conflict = detail as RawConflictDetail;
          return {
            outcome: "conflict",
            message:
              "A project with this slug already exists in that folder. Open it, or pick a different slug.",
            slug,
            project_path: conflict.existing_db_path,
          };
        }

        if (
          err.status === 400 &&
          detail &&
          typeof detail === "object" &&
          "message" in detail
        ) {
          const invalid = detail as RawInvalidDetail;
          return {
            outcome: "invalid",
            message: invalid.message,
            slug,
            project_path: null,
            os_error: invalid.message,
          };
        }
      }
      throw err;
    }
  },

  importDocuments(
    projectSlug: string,
    paths: string[],
  ): Promise<ImportResponse> {
    return apiFetch<ImportResponse>("/setup/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_slug: projectSlug,
        paths,
      } satisfies ImportRequest),
    });
  },

  async importStatus(jobId: string): Promise<ImportJobStatus> {
    const raw = await apiFetch<RawImportJobStatus>(
      `/setup/import/${encodeURIComponent(jobId)}`,
    );
    const total = raw.total || 0;
    const progress = total > 0 ? raw.completed / total : raw.status === "succeeded" || raw.status === "failed" ? 1 : 0;

    let status: ImportJobStatus["status"];
    if (raw.status === "pending") {
      status = "queued";
    } else if (raw.status === "running") {
      status = "running";
    } else if (raw.status === "failed" || raw.imported === 0) {
      status = "failed";
    } else if (raw.errors.length > 0) {
      status = "imported_partial";
    } else {
      status = "imported_all";
    }

    return {
      job_id: raw.job_id,
      raw_status: raw.status,
      status,
      progress,
      total,
      completed: raw.completed,
      imported: raw.imported,
      deduped: raw.deduped,
      errors: raw.errors,
    };
  },

  skipImport(projectSlug: string): Promise<{ acknowledged: true }> {
    return apiFetch<{ acknowledged: true }>("/setup/import/skip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_slug: projectSlug }),
    });
  },

  /**
   * Idempotent — safe to call from the ready page on every mount. Returns
   * the full state object so the caller can verify `complete: true`.
   */
  complete(): Promise<SetupState> {
    return apiFetch<SetupState>("/setup/complete", { method: "POST" });
  },
};

/**
 * Derive a URL-safe slug from a free-text project name. Rules match
 * Stream C's `wizard.py` slugify so the suggested slug matches what the
 * API would produce on its own.
 */
export function suggestSlug(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

/** True iff `slug` matches the constraints expected by the API. */
export function isValidSlug(slug: string): boolean {
  if (!slug) return false;
  if (slug.length > 64) return false;
  return /^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug);
}
