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
 * Five-outcome create-project shape the wizard page renders. Adapter
 * translates from the raw HTTP shape (200 / 409 / 400) Stream C ships.
 *
 * "created"           — happy path; project exists on disk.
 * "conflict"          — 409 slug_exists; a project with this slug already
 *                       lives in the projects folder.
 * "import_in_progress" — 409 import_in_progress; a folder-import job is
 *                        still writing to the staging DB; the user should
 *                        wait and retry.
 * "staging_db_locked" — 409 staging_db_locked; the adopt_project move
 *                       raised OSError (file in use); wait and retry.
 * "invalid"           — 400; directory not writeable / slug invalid / etc.
 */
export interface CreateProjectResponse {
  outcome:
    | "created"
    | "conflict"
    | "import_in_progress"
    | "staging_db_locked"
    | "invalid";
  /** Human-readable message safe to render verbatim. */
  message: string;
  /** Slug of the created or conflicting project. */
  slug: string | null;
  /** Absolute path on disk for the created or conflicting project. */
  project_path: string | null;
  /** OS error string when outcome === "invalid". */
  os_error?: string | null;
  /** Backend job_id when outcome === "import_in_progress". */
  job_id?: string | null;
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

interface RawImportInProgressDetail {
  error: "import_in_progress";
  job_id?: string;
}

interface RawStagingDbLockedDetail {
  error: "staging_db_locked";
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
          detail?:
            | RawConflictDetail
            | RawImportInProgressDetail
            | RawStagingDbLockedDetail
            | RawInvalidDetail
            | string;
        }>(err.body);
        const detail = parsed?.detail;

        if (err.status === 409 && detail && typeof detail === "object") {
          if (detail.error === "slug_exists") {
            const conflict = detail as RawConflictDetail;
            return {
              outcome: "conflict",
              message:
                "A project with this slug already exists in that folder. Open it, or pick a different slug.",
              slug,
              project_path: conflict.existing_db_path,
            };
          }

          if (detail.error === "import_in_progress") {
            const inProgress = detail as RawImportInProgressDetail;
            return {
              outcome: "import_in_progress",
              message:
                "We're still importing your documents. Wait a moment and try again.",
              slug,
              project_path: null,
              job_id: inProgress.job_id ?? null,
            };
          }

          if (detail.error === "staging_db_locked") {
            return {
              outcome: "staging_db_locked",
              message:
                "Could not move the project database — it may still be in use. Wait a few seconds and try again.",
              slug,
              project_path: null,
            };
          }
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

  /* ---------- folder-pick (alpha-2 reframe — Stream A endpoints) ---------- */

  /**
   * Scan a folder for ingestable documents. The backend walks the folder
   * (no recursion limits beyond what Stream A enforces), buckets files by
   * kind, and returns a manifest the wizard renders as a preview.
   */
  scanFolder(folderPath: string): Promise<FolderScanResponse> {
    return apiFetch<FolderScanResponse>("/setup/import-folder/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: folderPath }),
    });
  },

  /**
   * Kick off a folder-import job. Returns a job id the wizard polls via
   * `folderImportStatus`. `project_name` becomes the project's display
   * name; the backend derives the slug.
   *
   * Alpha-24 item #4: pass a UUIDv4 `idempotencyKey` (one per user
   * click). Two POSTs with the same key within the server-side TTL
   * (15 minutes) return the same `job_id`. This closes the
   * double-submission class even when the page-level guards (L1+L2)
   * are bypassed by browser-layer retries.
   */
  importFolder(
    folderPath: string,
    projectName: string,
    idempotencyKey: string,
  ): Promise<ImportResponse> {
    return apiFetch<ImportResponse>("/setup/import-folder", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        folder_path: folderPath,
        project_name: projectName,
      }),
    });
  },

  /** Poll a folder-import job for progress + terminal state. */
  folderImportStatus(jobId: string): Promise<FolderImportJobStatus> {
    return apiFetch<FolderImportJobStatus>(
      `/setup/import-folder/${encodeURIComponent(jobId)}`,
    );
  },

  /**
   * Suggest a project name from a folder path. Backend derives a sensible
   * default from the folder name and bumps with `-2`, `-3`, … if a
   * project already uses that slug.
   */
  suggestProjectName(folderPath: string): Promise<SuggestNameResponse> {
    return apiFetch<SuggestNameResponse>("/setup/projects/suggest-name", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: folderPath }),
    });
  },

  /**
   * Idempotent — safe to call from the ready page on every mount. Returns
   * the full state object so the caller can verify `complete: true`.
   */
  complete(): Promise<SetupState> {
    return apiFetch<SetupState>("/setup/complete", { method: "POST" });
  },

  /**
   * Sensible per-machine defaults the wizard should pre-fill into inputs
   * BEFORE the user has saved anything. Currently only used for
   * `projects_dir` — the backend resolves `MERIDIAN_HOME` (env override)
   * → `C:\Meridian\projects` on Windows installer hosts → `~/Meridian/projects`
   * elsewhere, producing a real path the user can submit unedited.
   *
   * If the backend hasn't shipped this endpoint yet (404), the caller
   * receives `null` and falls back to a hardcoded sensible value so the
   * wizard never freezes on a missing endpoint.
   */
  async defaults(): Promise<SetupDefaults | null> {
    try {
      return await apiFetch<SetupDefaults>("/setup/defaults");
    } catch (err) {
      if (err instanceof MeridianApiError && err.status === 404) {
        return null;
      }
      // Other errors (network, 500) — also non-fatal; the wizard still
      // works with the hardcoded fallback. Log so the dev console
      // surfaces the issue.
      // eslint-disable-next-line no-console
      console.warn("[setupApi.defaults] failed, using fallback", err);
      return null;
    }
  },
};

/* ---------- folder-pick types (alpha-2 reframe — Stream A shapes) ---------- */

/**
 * Stable error codes the backend tags every per-file failure with.
 * Adding a new code is a scoped change here AND in
 * `meridian.wizard.models.ImportErrorCode` AND in
 * `components/setup/copy.ts` (where the per-code remediation copy
 * lives). Removing one is a frontend-breaking change.
 */
export type ImportErrorCode =
  | "oda_missing"
  | "pdf_unreadable"
  | "file_locked"
  | "permission_denied"
  | "file_missing"
  | "unsupported_mime"
  | "worker_aborted"
  | "unknown";

/**
 * One coalesced same-cause error group from the backend's import-job
 * status. Replaces alpha-10's flat `string[]` of "{file}: {message}"
 * lines, which produced 18 identical rows for one corpus's 18 missing
 * ODA-converter cases.
 *
 * The backend also returns the legacy `failed: string[]` /
 * `errors: string[]` for one release of backward-compat (alpha-13
 * removes it); the frontend should always read the groups field and
 * ignore the flat list.
 */
export interface ImportErrorGroup {
  code: ImportErrorCode;
  count: number;
  /** PM-language summary, e.g. "18 AutoCAD drawings were skipped — ODA File Converter is not installed." */
  summary: string;
  /** Specific user action to fix this category. Empty string when no remediation applies. */
  remediation: string;
  /** Basenames of every file in this group, capped server-side at 100. */
  files: string[];
  /** True when the file list was truncated to the server-side cap. */
  truncated: boolean;
}

/**
 * ODA File Converter pre-flight detection. Surfaced on
 * `FolderScanResponse` (so the GUI can warn before import) and on
 * `FolderImportJobStatus` (so the post-import partial-success panel
 * can offer 'install ODA and re-scan' as remediation).
 */
export interface OdaStatus {
  available: boolean;
  /** Best-effort version string, e.g. "25.4.0". May be null. */
  version: string | null;
  /** Canonical installer URL — surfaced by the backend so the frontend doesn't hardcode. */
  install_url: string;
  /** Absolute path of the located binary, or null when unavailable. */
  binary_path: string | null;
}

/**
 * Manifest the scan endpoint returns. Each `files_by_kind` bucket is a
 * flat list of absolute file paths; `skipped` enumerates files the
 * backend chose to ignore (with a reason like "unsupported_extension"
 * or "too_large"); `total_ingestable` is the sum across buckets.
 *
 * Alpha-11: `oda` carries pre-flight ODA File Converter availability so
 * the GUI can surface "install ODA to ingest your N drawings" BEFORE
 * the user kicks off an import that will silently skip every drawing.
 */
export interface FolderScanResponse {
  folder_path: string;
  folder_name: string;
  files_by_kind: {
    pdf: string[];
    docx: string[];
    xlsx: string[];
    dwg: string[];
    eml: string[];
    msg: string[];
  };
  skipped: { path: string; reason: string }[];
  total_ingestable: number;
  oda: OdaStatus;
}

/**
 * Live status of a folder-import job. Alpha-11 corrects two contract
 * drift bugs against the backend:
 *
 *   1. `status` was typed as `"running" | "done" | "failed"` but the
 *      backend writes `"succeeded"` (not `"done"`). The page's pivot
 *      branch never fired — root cause of the alpha-10 "stuck at
 *      329 of 347" report.
 *
 *   2. `failed` was typed as `number` but the backend has always
 *      returned `list[str]`. JS `array > 0` is `false`, so the
 *      `s.failed > 0` partial-success branch never fired either.
 *
 * Plus the new structured fields (`failed_groups`, `oda`) so the
 * partial-success UI renders coalesced rows with remediation rather
 * than 18 identical "ODA not installed" strings.
 */
export interface FolderImportJobStatus {
  job_id: string;
  status: "pending" | "running" | "succeeded" | "failed";
  total: number;
  completed: number;
  imported: number;
  deduped: number;
  /**
   * DEPRECATED: legacy flat list of "{file}: {message}" strings; the
   * backend keeps populating this for one release of backward-compat
   * (alpha-13 removes it). Frontend code should read `failed_groups`
   * instead.
   */
  failed: string[];
  failed_groups: ImportErrorGroup[];
  current_file: string | null;
  oda: OdaStatus;
}

/**
 * Response from `/setup/defaults`. Returned by the backend so the wizard
 * can pre-fill OS-appropriate paths into its inputs without baking them
 * into frontend code.
 *
 * Contract (documented for backend):
 *   GET /setup/defaults → 200 { projects_dir: string, home_dir: string }
 *   - `projects_dir` is the absolute path the backend would default to
 *     for new project storage (resolves MERIDIAN_HOME env var, then
 *     `C:\Meridian\projects` on Windows installer hosts, then
 *     `~/Meridian/projects`). It must be a real path containing no
 *     placeholder tokens like `<you>` — the frontend submits it
 *     verbatim if the user doesn't edit it.
 *   - `home_dir` is the running OS user's home (str(Path.home())).
 *     Alpha-9 added this so first-documents page can build a smart
 *     pre-fill of <home_dir>/Documents/<folderName> for the typed-path
 *     input when the browser webkitdirectory picker only returns the
 *     folder name (browser hides absolute paths from JS for security).
 *   - 404 is acceptable; the frontend falls back to a hardcoded value
 *     and the user can still type their own path. No other status codes
 *     are expected for this endpoint.
 */
export interface SetupDefaults {
  projects_dir: string;
  /**
   * Optional in the TS contract even though alpha-9+ backends always
   * include it: a frontend bundle running against an alpha-8-or-earlier
   * backend will see this field absent. Marking optional keeps the TS
   * type honest about runtime reality. Frontend code uses `d?.home_dir`
   * and falls back to no smart-pre-fill when missing.
   */
  home_dir?: string;
}

/**
 * Response from `/setup/projects/suggest-name`. `is_available` is false
 * when the suggested name's slug is already taken; the backend has
 * already bumped (`shell-c-d` → `shell-c-d-2`) before returning, so the
 * page just renders the suggestion verbatim.
 */
export interface SuggestNameResponse {
  suggested_name: string;
  is_available: boolean;
}

/** Friendly label for each `files_by_kind` bucket — used in the manifest. */
export const FILE_KIND_LABELS: Record<keyof FolderScanResponse["files_by_kind"], string> = {
  pdf: "PDF",
  docx: "Word document",
  xlsx: "Excel spreadsheet",
  dwg: "AutoCAD drawing",
  eml: "Email (.eml)",
  msg: "Outlook email (.msg)",
};

/** Plural label for the manifest summary line ("Found 12 PDFs, 4 Word documents…"). */
export const FILE_KIND_LABELS_PLURAL: Record<keyof FolderScanResponse["files_by_kind"], string> = {
  pdf: "PDFs",
  docx: "Word documents",
  xlsx: "Excel spreadsheets",
  dwg: "AutoCAD drawings",
  eml: "emails (.eml)",
  msg: "Outlook emails (.msg)",
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

/* ---------- pipeline endpoints (alpha-25) ---------- */

export interface PipelineRequest {
  sample_size?: number;
  provider?: string;
  model?: string;
}

export interface PipelineResponse {
  job_id: string;
}

export type PipelinePhase =
  | "pending"
  | "bootstrap"
  | "extract"
  | "conflicts"
  | "done"
  | "failed";

export type PipelineBootstrapStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed";

export type PipelineConflictPassStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface PipelineStatus {
  job_id: string;
  phase: PipelinePhase;
  bootstrap_status: PipelineBootstrapStatus;
  conflict_pass_status: PipelineConflictPassStatus;
  extract_total: number;
  extract_completed: number;
  current_source_filename: string | null;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  holder_pid: number | null;
}

export const pipelineApi = {
  async start(
    slug: string,
    body: PipelineRequest,
    opts: { idempotencyKey?: string } = {},
  ): Promise<PipelineResponse> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
    return apiFetch<PipelineResponse>(
      `/projects/${encodeURIComponent(slug)}/pipeline`,
      { method: "POST", headers, body: JSON.stringify(body) },
    );
  },

  async status(slug: string, jobId: string): Promise<PipelineStatus> {
    return apiFetch<PipelineStatus>(
      `/projects/${encodeURIComponent(slug)}/pipeline/${encodeURIComponent(jobId)}`,
    );
  },

  async latest(slug: string): Promise<PipelineStatus> {
    return apiFetch<PipelineStatus>(
      `/projects/${encodeURIComponent(slug)}/pipeline`,
    );
  },
};
