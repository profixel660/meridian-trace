"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { Tooltip } from "@/components/review/Tooltip";
import { FolderManifestPreview } from "@/components/setup/FolderManifestPreview";
import { SetupShell } from "@/components/setup/SetupShell";
import { FIRST_DOCS_COPY } from "@/components/setup/copy";
import { MeridianApiError } from "@/lib/api";
import {
  setupApi,
  type FolderImportJobStatus,
  type FolderScanResponse,
  type ImportErrorGroup,
} from "@/lib/setupClient";
import {
  buildPrefilledPath,
  looksAbsolute,
  stripSurroundingQuotes,
} from "@/lib/setupPaths";
import { isInTauri, pickFolderWithFallback } from "@/lib/tauri";

/**
 * Step 2 (alpha-2 reframe) — Where are your project documents?
 *
 * The SME complaint that triggered this rewrite: alpha-1 dropped users into
 * a single-file picker labelled "source document". Construction PMs don't
 * think in files; they think "here's my project folder, find the relevant
 * stuff in it" — same mental model as Procore / Revizto. So this page
 * asks for a FOLDER, scans it server-side, shows a manifest, and on
 * confirm hands the folder + the auto-suggested project name to
 * `/setup/import-folder`.
 *
 * Tauri vs browser:
 *   - Inside Tauri (`isInTauri()`), `pickFolderWithFallback` returns the
 *     real absolute path from the OS folder dialog. We pass that straight
 *     into `/setup/import-folder/scan`.
 *   - In a plain browser the OS hides the absolute path from the
 *     `webkitdirectory` input, so we capture the folder NAME from the
 *     picker and prompt the user to type/paste the absolute path with
 *     the folder name pre-filled as a hint. (Decision: typed-input
 *     fallback is more PM-friendly than blindly asking — they've
 *     literally just clicked the folder, the picker confirmed its name,
 *     so finishing with "now type the path" is a far smaller cognitive
 *     load than presenting an empty box and saying "tell us where it is".)
 *
 * Three-outcome validation:
 *   - valid path / scan succeeds → manifest + Import button
 *   - invalid path (non-existent / not a folder) → red panel with
 *     "did you mean the parent directory?" hint and a retry affordance
 *   - unable_to_verify (network down) → amber panel with "skip for now"
 *     option that fires through `/setup/import/skip` per the existing
 *     skip flow
 *
 * The picked folder path is persisted to sessionStorage so the next page
 * (`/setup/first-project`) can call `/setup/projects/suggest-name` and
 * pre-fill the name input.
 */

type Phase =
  | { kind: "idle" }
  | { kind: "browser_path_prompt"; folderName: string | null }
  | { kind: "scanning"; folderPath: string }
  | { kind: "scanned"; manifest: FolderScanResponse }
  | { kind: "scan_invalid"; folderPath: string; message: string }
  | { kind: "scan_unable"; folderPath: string; message: string }
  | { kind: "importing"; jobId: string; status: FolderImportJobStatus | null }
  | { kind: "imported"; status: FolderImportJobStatus }
  | { kind: "partial"; status: FolderImportJobStatus }
  | { kind: "failed"; status: FolderImportJobStatus | null; message: string }
  | { kind: "skipped" };

// Backend error codes from /setup/import-folder/scan 400 responses.
// See `_validate_folder_path` in src/meridian/wizard/api.py.
type ScanBackendError =
  | "folder_not_found"
  | "folder_not_a_directory"
  | "folder_access_denied"
  | "unknown";

interface ScanErrorClassification {
  kind: "invalid" | "unable";
  backendError: ScanBackendError;
  serverMessage: string | null;
}

const SCAN_INVALID_HINT_MARKERS = [
  "not a directory",
  "not a folder",
  "not_a_directory",
  "no such file or directory",
  "enoent",
  "does not exist",
];

/**
 * Pull the backend's structured error out of a 400 response body. Alpha-8
 * fix: the previous classifier only inspected `err.message` (which is the
 * generic "Meridian API 400 Bad Request for /setup/...") and routed every
 * 400 to the amber "transient network hiccup" panel — wrong direction
 * entirely. The actual reason lives in `MeridianApiError.body` as JSON
 * shaped `{"detail": {"error": "<code>", "message": "..."}}`.
 */
/**
 * One row in the partial-success panel — a coalesced error group with
 * a PM-language summary, the per-code remediation copy, and an
 * expandable list of the affected basenames. Alpha-11.
 */
function ImportErrorGroupRow({ group }: { group: ImportErrorGroup }) {
  const remediation = FIRST_DOCS_COPY.errorGroupRemediation[group.code] ?? "";
  return (
    <li className="rounded border border-amber-500/30 bg-surface-elevated p-3">
      <details>
        <summary className="cursor-pointer list-none">
          <span className="text-sm font-medium text-text-primary">
            {group.summary}
          </span>
        </summary>
        <div className="mt-2 space-y-2">
          {remediation ? (
            <p className="text-xs text-amber-200">
              <span className="font-medium">How to fix:</span> {remediation}
            </p>
          ) : null}
          <ul className="list-inside list-disc font-mono text-[11px] text-text-muted">
            {group.files.map((f) => (
              <li key={f} className="truncate" title={f}>
                {f}
              </li>
            ))}
            {group.truncated ? (
              <li className="italic">
                …and {group.count - group.files.length} more
              </li>
            ) : null}
          </ul>
        </div>
      </details>
    </li>
  );
}

function classifyScanError(err: unknown): ScanErrorClassification {
  // Default — no information beyond "something went wrong".
  let backendError: ScanBackendError = "unknown";
  let serverMessage: string | null = null;

  if (err instanceof MeridianApiError && err.status === 400) {
    try {
      const parsed = JSON.parse(err.body) as {
        detail?: { error?: string; message?: string } | string;
      };
      const detail = parsed.detail;
      if (detail && typeof detail === "object") {
        const code = detail.error;
        if (
          code === "folder_not_found" ||
          code === "folder_not_a_directory" ||
          code === "folder_access_denied"
        ) {
          backendError = code;
        }
        if (typeof detail.message === "string") {
          serverMessage = detail.message;
        }
      } else if (typeof detail === "string") {
        serverMessage = detail;
      }
    } catch {
      // body wasn't JSON — fall through to substring check below.
    }
  }

  // If we identified a structured backend error code, all three are
  // user-fixable -> "invalid" panel (red), not "unable" (amber).
  if (backendError !== "unknown") {
    return { kind: "invalid", backendError, serverMessage };
  }

  // Legacy fallback: substring-match the message. Catches cases where the
  // backend returns 400 with a non-structured detail or a different status
  // code path (e.g. uvicorn-level error before reaching the validator).
  const msg = err instanceof Error ? err.message.toLowerCase() : "";
  const hint = SCAN_INVALID_HINT_MARKERS.some((m) => msg.includes(m));
  return {
    kind: hint ? "invalid" : "unable",
    backendError,
    serverMessage,
  };
}

export default function SetupFirstDocumentsPage() {
  const router = useRouter();
  const [tauri, setTauri] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [manualPath, setManualPath] = useState("");
  const [confirmImport, setConfirmImport] = useState(false);
  const [confirmSkip, setConfirmSkip] = useState(false);
  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setTauri(isInTauri());
  }, []);

  // Alpha-9: fetch the OS user's home dir from /setup/defaults so the
  // browser-fallback typed-path input can pre-fill with
  // <home>\Documents\<folderName> instead of just <folderName>. Browser
  // webkitdirectory only exposes the folder name (no absolute path) for
  // security; this gets us a smart guess that works for ~90% of users
  // (Documents-rooted projects). Tauri MSI eliminates the need entirely.
  const [homeDir, setHomeDir] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setupApi
      .defaults()
      .then((d) => {
        if (!cancelled && d?.home_dir) setHomeDir(d.home_dir);
      })
      .catch((err) => {
        // 404 / network — frontend falls back to no-pre-fill behaviour.
        // Alpha-10 grid finding: alpha-9 swallowed silently which left
        // the operator no way to diagnose why the smart pre-fill wasn't
        // appearing. console.warn surfaces it in DevTools.
        // eslint-disable-next-line no-console
        console.warn(
          "[setup/first-documents] /setup/defaults failed; smart pre-fill disabled.",
          err,
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Cleanup any in-flight polling timer on unmount.
  useEffect(
    () => () => {
      if (pollHandle.current) clearInterval(pollHandle.current);
    },
    [],
  );

  // Alpha-7: elapsed-time counter while scanning. Without this, a slow
  // scan (large folder + spinning disk) looks identical to a hang.
  // Restarts on every entry to the scanning phase.
  const [scanElapsedSec, setScanElapsedSec] = useState(0);
  useEffect(() => {
    if (phase.kind !== "scanning") {
      setScanElapsedSec(0);
      return;
    }
    setScanElapsedSec(0);
    const start = Date.now();
    const handle = window.setInterval(() => {
      setScanElapsedSec(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => window.clearInterval(handle);
  }, [phase.kind]);

  /* ---------------------------- folder picking ---------------------------- */

  const scanFolder = useCallback(async (folderPath: string) => {
    setPhase({ kind: "scanning", folderPath });
    try {
      const manifest = await setupApi.scanFolder(folderPath);
      // Persist folder path so the next page can call /suggest-name.
      try {
        window.sessionStorage.setItem(
          "meridian.setup.folder_path",
          manifest.folder_path,
        );
        window.sessionStorage.setItem(
          "meridian.setup.folder_name",
          manifest.folder_name,
        );
      } catch {
        // sessionStorage blocked — auto-name will silently fall back to
        // the existing manual flow on the next page.
      }
      setPhase({ kind: "scanned", manifest });
    } catch (err) {
      const cls = classifyScanError(err);
      // Prefer the backend's specific message (e.g. "Folder does not
      // exist: C:\\Users\\Foo\\..."); fall back to the generic "Meridian
      // API 400 Bad Request..." string only when the body wasn't the
      // expected structured shape.
      const message =
        cls.serverMessage ??
        (err instanceof Error ? err.message : "Unknown error");
      if (cls.kind === "invalid") {
        setPhase({ kind: "scan_invalid", folderPath, message });
      } else {
        setPhase({ kind: "scan_unable", folderPath, message });
      }
    }
  }, []);

  const [pickerError, setPickerError] = useState<string | null>(null);

  const handlePickFolder = useCallback(async () => {
    setPickerError(null);
    try {
      const result = await pickFolderWithFallback({
        title: "Choose your project folder",
      });
      if (result.kind === "cancelled") {
        // Stay on idle — the page already invites them to try again.
        // Don't surface an error: cancel is a legitimate user action.
        return;
      }
      if (result.kind === "native") {
        void scanFolder(result.path);
        return;
      }
      // Browser fallback — pre-fill via the extracted pure helper
      // (alpha-10: extracted from inline construction so it's
      // unit-testable and consistent across edge cases like trailing
      // separators on homeDir, mixed separators, empty folderName).
      const folderName = result.folderName ?? "";
      setManualPath(buildPrefilledPath(homeDir, folderName));
      setPhase({ kind: "browser_path_prompt", folderName: result.folderName });
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("[setup/first-documents] folder picker failed", err);
      setPickerError(
        err instanceof Error
          ? `Folder picker failed: ${err.message}`
          : "Folder picker failed for an unknown reason — please try again or refresh the page.",
      );
    }
  }, [scanFolder]);

  // Alpha-9: client-side path-shape validation. Browser webkitdirectory
  // only gives us the folder name; the typed-path input is the only
  // way to get a real absolute path into the wizard. If the user
  // submits something that's clearly NOT an absolute path (e.g. just
  // the folder name), the backend will 400 with folder_not_found —
  // refuse here with a clear inline message instead of sending a
  // doomed request.
  //
  // Alpha-10: helpers extracted to lib/setupPaths.ts and made unit-
  // testable. submitManualPath now also strips surrounding double-
  // quotes BEFORE the looksAbsolute check, because Windows 11's
  // "Copy as path" (Win+Shift+C) wraps copied paths in double quotes
  // — alpha-9 users who pasted a copied-from-Explorer path saw a
  // confusing "doesn't look like a full path" error.
  const [pathError, setPathError] = useState<string | null>(null);

  const submitManualPath = () => {
    const cleaned = stripSurroundingQuotes(manualPath.trim());
    if (!cleaned) {
      setPathError("Please enter the folder path.");
      return;
    }
    if (!looksAbsolute(cleaned)) {
      setPathError(
        `That doesn't look like a full path. Add the drive letter (e.g. C:\\Users\\...). Your browser only gave us the folder NAME — not the full path — so we need you to add the prefix.`,
      );
      return;
    }
    setPathError(null);
    void scanFolder(cleaned);
  };

  /* ------------------------------ import flow ----------------------------- */

  const triggerImport = useCallback(async () => {
    if (phase.kind !== "scanned") return;
    setConfirmImport(false);
    const folderPath = phase.manifest.folder_path;
    // Project name comes from the folder name; the next page lets the
    // user edit it before /setup/projects is called. The folder-import
    // backend (Stream A) creates the project itself using this name.
    const projectName = phase.manifest.folder_name;
    try {
      const res = await setupApi.importFolder(folderPath, projectName);
      // Persist suggested project name + slug stub so the next page can
      // resume cleanly even if the user navigates away mid-import.
      try {
        window.sessionStorage.setItem("meridian.setup.project_name", projectName);
      } catch {
        // ignore
      }
      setPhase({ kind: "importing", jobId: res.job_id, status: null });
      pollHandle.current = setInterval(async () => {
        try {
          const s = await setupApi.folderImportStatus(res.job_id);
          setPhase((prev) =>
            prev.kind === "importing"
              ? { kind: "importing", jobId: prev.jobId, status: s }
              : prev,
          );
          // Alpha-11 pivot fix: backend returns "succeeded" / "failed",
          // never "done" — alpha-10 typed-it-wrong so the polling loop
          // never terminated for a real corpus that completed with any
          // failures. Pivoting on the canonical strings here aligns
          // with the backend's _run_import_job state machine.
          if (s.status === "succeeded" || s.status === "failed") {
            if (pollHandle.current) {
              clearInterval(pollHandle.current);
              pollHandle.current = null;
            }
            const failedCount = s.failed_groups.reduce(
              (acc, g) => acc + g.count,
              0,
            );
            // Alpha-11 reviewer-finding fix: alpha-10 routed all
            // `imported === 0` outcomes to "failed" — but a re-import
            // where every file de-duplicates is `imported=0`,
            // `deduped>0` and shouldn't surface the red "no files
            // imported" panel. Treat the all-deduped case as
            // succeeded; only fail when there is genuinely no
            // progress at all (no imports AND no dedups).
            if (
              s.status === "failed" ||
              (s.imported === 0 && s.deduped === 0)
            ) {
              setPhase({
                kind: "failed",
                status: s,
                message:
                  s.status === "failed"
                    ? "Import job failed — see details below."
                    : "No files were imported successfully.",
              });
            } else if (failedCount > 0) {
              // Partial: some files imported, some failed. Show
              // coalesced groups with remediation per-category.
              setPhase({ kind: "partial", status: s });
            } else {
              setPhase({ kind: "imported", status: s });
            }
          }
        } catch (err) {
          if (pollHandle.current) {
            clearInterval(pollHandle.current);
            pollHandle.current = null;
          }
          setPhase({
            kind: "failed",
            status: null,
            message:
              err instanceof Error
                ? err.message
                : "Lost contact with the import job.",
          });
        }
      }, 1000);
    } catch (err) {
      setPhase({
        kind: "failed",
        status: null,
        message:
          err instanceof Error ? err.message : "Could not start the import.",
      });
    }
  }, [phase]);

  /* ------------------------------ skip flow ------------------------------- */

  const handleSkip = () => setConfirmSkip(true);

  const confirmSkipNow = async () => {
    setConfirmSkip(false);
    setPhase({ kind: "skipped" });
    // Best-effort skip notification — there's no project_slug yet (folder
    // pick is the first step that creates one), so we just mark and move on.
    // The /setup/complete idempotent call on the ready page accepts a
    // documents-skipped state regardless of whether /skip was hit.
    router.push("/setup/first-project?skipped=1");
  };

  /* ------------------------------- continue ------------------------------- */

  const handleContinue = () => router.push("/setup/first-project");

  const canContinue =
    phase.kind === "imported" ||
    phase.kind === "partial" ||
    phase.kind === "skipped";
  const isBusy =
    phase.kind === "scanning" || phase.kind === "importing";

  return (
    <SetupShell
      step="first-documents"
      backHref="/setup/api-key"
      onContinue={handleContinue}
      continueDisabled={!canContinue}
      busy={isBusy}
    >
      <div className="space-y-8">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            {FIRST_DOCS_COPY.title}
          </h1>
          <p className="text-sm text-text-muted">{FIRST_DOCS_COPY.subtitle}</p>
        </header>

        <FirstUseCallout
          routeKey="setup/first-documents"
          title={FIRST_DOCS_COPY.firstUseTitle}
        >
          {FIRST_DOCS_COPY.firstUseBody}
        </FirstUseCallout>

        <section
          aria-label="Why this step"
          className="rounded-lg border border-accent/30 bg-accent/5 p-4 text-sm text-text-primary"
        >
          {FIRST_DOCS_COPY.why()}
        </section>

        {/* Idle / pick-folder UI. Always available so the user can re-pick. */}
        {(phase.kind === "idle" ||
          phase.kind === "scan_invalid" ||
          phase.kind === "scan_unable") ? (
          <div className="space-y-3">
            {/*
              The folder-pick button must NOT be wrapped in a `<Tooltip>` —
              Tooltip uses cloneElement to inject its own onClick, which
              silently clobbers the child's onClick handler. That was the
              alpha-5 "Choose project folder does nothing" bug. Keep the
              tooltip on a separate "what does this do?" affordance below
              the button instead. The button itself uses the native `title`
              attribute for hover discoverability — clunkier but reliable.
            */}
            <button
              type="button"
              onClick={() => void handlePickFolder()}
              disabled={isBusy}
              title={FIRST_DOCS_COPY.pickFolderTooltip}
              className="w-full rounded-xl border-2 border-accent/60 bg-accent/10 px-6 py-6 text-left text-lg font-medium text-text-primary transition hover:border-accent hover:bg-accent/15 disabled:opacity-40"
            >
              <span className="block">{FIRST_DOCS_COPY.pickFolderButton}</span>
              <span className="mt-1 block text-xs font-normal text-text-muted">
                {tauri
                  ? "Opens your operating system's folder picker."
                  : "Browser preview — we'll ask for the path after you pick."}
              </span>
            </button>

            <p className="text-xs text-text-muted">
              <Tooltip
                content={FIRST_DOCS_COPY.pickFolderTooltip}
                widthClass="w-80"
              >
                <span className="cursor-help underline decoration-dotted">
                  What goes in the folder?
                </span>
              </Tooltip>
            </p>

            {pickerError ? (
              <p
                role="alert"
                className="rounded border border-red-500/40 bg-red-500/5 p-2 text-xs text-red-300"
              >
                {pickerError}
              </p>
            ) : null}

            <p className="text-xs text-text-muted">
              Or{" "}
              <button
                type="button"
                onClick={handleSkip}
                className="text-accent underline-offset-2 hover:underline"
              >
                {FIRST_DOCS_COPY.skipLabel}
              </button>
              .
            </p>
          </div>
        ) : null}

        {/* Browser-fallback typed-path prompt. Alpha-9: amber callout
            explains why the typing step exists at all (browser security
            hides the absolute path from JS). Smart pre-fill from
            /setup/defaults' home_dir means most users just press Enter. */}
        {phase.kind === "browser_path_prompt" ? (
          <div className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4">
            <div className="rounded border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-amber-200">
              <p className="font-medium">
                ⚠ Browser security: we only got the folder NAME from your
                pick.
              </p>
              <p className="mt-1 text-amber-200/80">
                Browsers (Chrome, Edge, Firefox) hide the full filesystem
                path from web pages by design. We&apos;ve guessed the most
                likely full path below — check it&apos;s right and press
                Enter, or edit it. The Tauri desktop build (coming soon)
                gives us the full path directly and removes this step.
              </p>
            </div>
            <label
              htmlFor="folder-path-input"
              className="block text-sm font-medium text-text-primary"
            >
              {FIRST_DOCS_COPY.browserPathPromptLabel}
            </label>
            <input
              id="folder-path-input"
              type="text"
              value={manualPath}
              onChange={(e) => {
                setManualPath(e.target.value);
                if (pathError) setPathError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submitManualPath();
                }
              }}
              placeholder={FIRST_DOCS_COPY.browserPathPromptPlaceholder}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-xs text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none"
              autoFocus
            />
            {pathError ? (
              <p
                role="alert"
                className="rounded border border-red-500/40 bg-red-500/5 p-2 text-xs text-red-300"
              >
                {pathError}
              </p>
            ) : (
              <p className="text-xs text-text-muted">
                {FIRST_DOCS_COPY.browserPathPromptHelper}
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={submitManualPath}
                disabled={!manualPath.trim()}
                className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                Scan this folder
              </button>
              <button
                type="button"
                onClick={() => {
                  setPhase({ kind: "idle" });
                  setPathError(null);
                }}
                className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : null}

        {/* Scanning spinner with elapsed-time + hang-detection callout. */}
        {phase.kind === "scanning" ? (
          <div className="rounded-lg border border-accent/40 bg-surface-elevated p-4">
            <p className="text-sm font-medium text-text-primary">
              <span
                className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-accent align-middle"
                aria-hidden
              />
              Scanning folder
              <span className="ml-1 inline-block w-6 text-text-muted">
                {".".repeat((scanElapsedSec % 3) + 1)}
              </span>
              <span className="ml-2 text-xs text-text-muted">
                ({scanElapsedSec}s)
              </span>
            </p>
            <p
              className="mt-1 truncate font-mono text-[11px] text-text-muted"
              title={phase.folderPath}
            >
              {phase.folderPath}
            </p>
            {scanElapsedSec >= 15 ? (
              <p className="mt-3 rounded border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-200">
                Still scanning — large folders with thousands of files can
                take a minute or two on a spinning disk. If you suspect it&apos;s
                hung, you can refresh the page and pick a smaller folder.
              </p>
            ) : null}
          </div>
        ) : null}

        {/* Invalid path — red, with a hint. */}
        {phase.kind === "scan_invalid" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              ✗ {FIRST_DOCS_COPY.outcomes.invalidPath.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {FIRST_DOCS_COPY.outcomes.invalidPath.body}
            </p>
            <p
              className="mt-2 truncate font-mono text-[11px] text-text-muted"
              title={phase.folderPath}
            >
              You typed: {phase.folderPath}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handlePickFolder()}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-primary hover:border-accent"
              >
                Pick a different folder
              </button>
              <button
                type="button"
                onClick={() => {
                  setManualPath(phase.folderPath);
                  setPhase({ kind: "browser_path_prompt", folderName: null });
                }}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                Edit the path
              </button>
            </div>
          </div>
        ) : null}

        {/* Network down — amber, with skip-for-now option. */}
        {phase.kind === "scan_unable" ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <p className="text-sm font-medium text-amber-300">
              ⚠ {FIRST_DOCS_COPY.outcomes.networkDown.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {FIRST_DOCS_COPY.outcomes.networkDown.body}
            </p>
            <p className="mt-2 text-xs text-text-muted">
              Underlying error: <code className="rounded bg-surface px-1">{phase.message}</code>
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void scanFolder(phase.folderPath)}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-primary hover:border-accent"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={handleSkip}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                {FIRST_DOCS_COPY.skipLabel}
              </button>
            </div>
          </div>
        ) : null}

        {/* Manifest preview + confirm/import button. */}
        {phase.kind === "scanned" ? (
          <>
            {phase.manifest.total_ingestable === 0 ? (
              <EmptyState
                title={FIRST_DOCS_COPY.emptyManifestTitle}
                body={FIRST_DOCS_COPY.emptyManifestBody}
              />
            ) : (
              <FolderManifestPreview manifest={phase.manifest} />
            )}

            {/* Alpha-11: pre-flight ODA-missing callout.
                Surfaces BEFORE import when the manifest has any DWG
                files but the converter isn't installed — without this,
                drawings silently fail mid-import and users see 18
                identical "ODA not installed" errors at the end. */}
            {phase.manifest.files_by_kind.dwg.length > 0 &&
            !phase.manifest.oda.available ? (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
                <p className="text-sm font-medium text-amber-300">
                  ⚠ {FIRST_DOCS_COPY.odaMissingCallout.headline(
                    phase.manifest.files_by_kind.dwg.length,
                  )}
                </p>
                <p className="mt-1 text-sm text-text-muted">
                  {FIRST_DOCS_COPY.odaMissingCallout.body}
                </p>
                <p className="mt-2 text-xs text-text-muted">
                  <a
                    href={phase.manifest.oda.install_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent underline-offset-2 hover:underline"
                  >
                    {FIRST_DOCS_COPY.odaMissingCallout.installLinkLabel} →
                  </a>
                </p>
                <p className="mt-1 text-xs text-text-muted">
                  {FIRST_DOCS_COPY.odaMissingCallout.skipNote}
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-3 pt-2">
              {phase.manifest.total_ingestable > 0 ? (
                <button
                  type="button"
                  onClick={() => setConfirmImport(true)}
                  className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90"
                >
                  {FIRST_DOCS_COPY.confirmImportLabel}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void handlePickFolder()}
                className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                {phase.manifest.total_ingestable === 0
                  ? FIRST_DOCS_COPY.emptyManifestRetryLabel
                  : "Pick a different folder"}
              </button>
            </div>
          </>
        ) : null}

        {/* Live import progress. */}
        {phase.kind === "importing" ? (
          <div className="rounded-lg border border-border bg-surface-elevated p-4">
            <p className="text-sm font-medium text-text-primary">
              {phase.status
                ? FIRST_DOCS_COPY.progressHeader(
                    phase.status.imported,
                    phase.status.total,
                  )
                : "Starting import…"}
            </p>
            {phase.status?.current_file ? (
              <p
                className="mt-1 truncate font-mono text-[11px] text-text-muted"
                title={phase.status.current_file}
              >
                {FIRST_DOCS_COPY.progressCurrentFile(phase.status.current_file)}
              </p>
            ) : null}
            {(() => {
              // Alpha-11 progress fix: alpha-10 derived progress from
              // `imported + deduped + failed`, but `failed` is a
              // string[] not a number — adding it to a number yielded
              // NaN and the bar never ticked. The backend's
              // `completed` field is ground truth (incremented in the
              // worker's per-file `finally`); use it directly.
              const pct =
                phase.status && phase.status.total > 0
                  ? Math.round(
                      (phase.status.completed / phase.status.total) * 100,
                    )
                  : 0;
              return (
                <div
                  className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={pct}
                >
                  <div
                    className="h-full bg-accent transition-[width]"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              );
            })()}
            <p className="mt-2 text-xs text-text-muted">
              {FIRST_DOCS_COPY.progressBackgroundNote}
            </p>
          </div>
        ) : null}

        {/* All done — green. */}
        {phase.kind === "imported" ? (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4">
            <p className="text-sm font-medium text-emerald-300">
              ✓ {FIRST_DOCS_COPY.outcomes.succeeded.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {FIRST_DOCS_COPY.outcomes.succeeded.body(
                phase.status.imported,
                phase.status.deduped,
              )}
            </p>
          </div>
        ) : null}

        {/* Partial — amber. Coalesced error groups with per-code
            remediation. Alpha-11: this used to render "{phase.status.failed} failed"
            where `failed` was an array (rendered as comma-joined strings, or
            "[object Object]" depending on shape) — now it groups by error code
            and shows one row per category with an actionable next step. */}
        {phase.kind === "partial" ? (
          <div className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <div>
              <p className="text-sm font-medium text-amber-300">
                ⚠ {FIRST_DOCS_COPY.outcomes.partial.headline}
              </p>
              <p className="mt-1 text-sm text-text-muted">
                {phase.status.imported} of {phase.status.total} files
                imported.{" "}
                {phase.status.failed_groups.reduce(
                  (acc, g) => acc + g.count,
                  0,
                )}{" "}
                failed.
              </p>
              <p className="mt-2 text-xs text-text-muted">
                {FIRST_DOCS_COPY.outcomes.partial.body}
              </p>
            </div>
            <ul className="space-y-2">
              {phase.status.failed_groups.map((g) => (
                <ImportErrorGroupRow key={g.code} group={g} />
              ))}
            </ul>
            <p className="mt-1 text-sm text-text-muted">
              Press Continue to confirm your project name — you can fix the
              skipped files later from your project's Sources screen.
            </p>
          </div>
        ) : null}

        {/* Failed — red, with retry. */}
        {phase.kind === "failed" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              ✗ {FIRST_DOCS_COPY.outcomes.failed.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">{phase.message}</p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={() => setPhase({ kind: "idle" })}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={handleSkip}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                {FIRST_DOCS_COPY.skipLabel}
              </button>
            </div>
          </div>
        ) : null}

        {/* Skipped acknowledgement. */}
        {phase.kind === "skipped" ? (
          <div className="rounded-lg border border-border bg-surface-elevated p-4">
            <p className="text-sm text-text-primary">
              Skipped for now. You can add documents later from the Sources
              screen on your project dashboard.
            </p>
          </div>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmImport}
        title={FIRST_DOCS_COPY.confirmImportDialog.title}
        body={FIRST_DOCS_COPY.confirmImportDialog.body}
        confirmLabel={FIRST_DOCS_COPY.confirmImportDialog.confirm}
        cancelLabel={FIRST_DOCS_COPY.confirmImportDialog.cancel}
        destructive={false}
        onConfirm={() => void triggerImport()}
        onCancel={() => setConfirmImport(false)}
      />

      <ConfirmDialog
        open={confirmSkip}
        title={FIRST_DOCS_COPY.skipConfirm.title}
        body={FIRST_DOCS_COPY.skipConfirm.body}
        confirmLabel={FIRST_DOCS_COPY.skipConfirm.confirm}
        cancelLabel={FIRST_DOCS_COPY.skipConfirm.cancel}
        destructive={false}
        onConfirm={() => void confirmSkipNow()}
        onCancel={() => setConfirmSkip(false)}
      />
    </SetupShell>
  );
}
