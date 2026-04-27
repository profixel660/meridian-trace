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
import {
  setupApi,
  type FolderImportJobStatus,
  type FolderScanResponse,
} from "@/lib/setupClient";
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

const SCAN_INVALID_HINT_MARKERS = [
  "not a directory",
  "not a folder",
  "not_a_directory",
  "no such file or directory",
  "enoent",
  "does not exist",
];

function classifyScanError(err: unknown): "invalid" | "unable" {
  const msg = err instanceof Error ? err.message.toLowerCase() : "";
  if (SCAN_INVALID_HINT_MARKERS.some((m) => msg.includes(m))) return "invalid";
  return "unable";
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

  // Cleanup any in-flight polling timer on unmount.
  useEffect(
    () => () => {
      if (pollHandle.current) clearInterval(pollHandle.current);
    },
    [],
  );

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
      const message = err instanceof Error ? err.message : "Unknown error";
      if (cls === "invalid") {
        setPhase({ kind: "scan_invalid", folderPath, message });
      } else {
        setPhase({ kind: "scan_unable", folderPath, message });
      }
    }
  }, []);

  const handlePickFolder = useCallback(async () => {
    const result = await pickFolderWithFallback({
      title: "Choose your project folder",
    });
    if (result.kind === "cancelled") {
      // Stay on idle — the page already invites them to try again.
      return;
    }
    if (result.kind === "native") {
      void scanFolder(result.path);
      return;
    }
    // Browser fallback — pre-fill the manual-path input with whatever
    // hint we got and switch to the typed-path prompt.
    setManualPath(result.folderName ? result.folderName : "");
    setPhase({ kind: "browser_path_prompt", folderName: result.folderName });
  }, [scanFolder]);

  const submitManualPath = () => {
    const trimmed = manualPath.trim();
    if (!trimmed) return;
    void scanFolder(trimmed);
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
          if (s.status === "done" || s.status === "failed") {
            if (pollHandle.current) {
              clearInterval(pollHandle.current);
              pollHandle.current = null;
            }
            if (s.status === "failed" || s.imported === 0) {
              setPhase({
                kind: "failed",
                status: s,
                message:
                  s.status === "failed"
                    ? "Import job failed — see details below."
                    : "No files were imported successfully.",
              });
            } else if (s.failed > 0) {
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
            <Tooltip
              content={FIRST_DOCS_COPY.pickFolderTooltip}
              widthClass="w-80"
            >
              <button
                type="button"
                onClick={() => void handlePickFolder()}
                disabled={isBusy}
                className="w-full rounded-xl border-2 border-accent/60 bg-accent/10 px-6 py-6 text-left text-lg font-medium text-text-primary transition hover:border-accent hover:bg-accent/15 disabled:opacity-40"
              >
                <span className="block">{FIRST_DOCS_COPY.pickFolderButton}</span>
                <span className="mt-1 block text-xs font-normal text-text-muted">
                  {tauri
                    ? "Opens your operating system's folder picker."
                    : "Browser preview — we'll ask for the path after you pick."}
                </span>
              </button>
            </Tooltip>

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

        {/* Browser-fallback typed-path prompt. */}
        {phase.kind === "browser_path_prompt" ? (
          <div className="space-y-3 rounded-lg border border-border bg-surface-elevated p-4">
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
              onChange={(e) => setManualPath(e.target.value)}
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
            <p className="text-xs text-text-muted">
              {FIRST_DOCS_COPY.browserPathPromptHelper}
            </p>
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
                onClick={() => setPhase({ kind: "idle" })}
                className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : null}

        {/* Scanning spinner. */}
        {phase.kind === "scanning" ? (
          <div className="rounded-lg border border-border bg-surface-elevated p-4">
            <p className="text-sm font-medium text-text-primary">
              Scanning folder…
            </p>
            <p
              className="mt-1 truncate font-mono text-[11px] text-text-muted"
              title={phase.folderPath}
            >
              {phase.folderPath}
            </p>
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
            <div
              className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={
                phase.status && phase.status.total > 0
                  ? Math.round(
                      ((phase.status.imported + phase.status.deduped + phase.status.failed) /
                        phase.status.total) *
                        100,
                    )
                  : 0
              }
            >
              <div
                className="h-full bg-accent transition-[width]"
                style={{
                  width: `${
                    phase.status && phase.status.total > 0
                      ? Math.round(
                          ((phase.status.imported + phase.status.deduped + phase.status.failed) /
                            phase.status.total) *
                            100,
                        )
                      : 0
                  }%`,
                }}
              />
            </div>
            <p className="mt-2 text-xs text-text-muted">
              {FIRST_DOCS_COPY.progressBackgroundNote}
            </p>
          </div>
        ) : null}

        {/* All done — green. */}
        {phase.kind === "imported" ? (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4">
            <p className="text-sm font-medium text-emerald-300">
              ✓ {phase.status.imported} of {phase.status.total} files imported
              {phase.status.deduped > 0
                ? ` (${phase.status.deduped} already in the project — skipped)`
                : ""}
              .
            </p>
            <p className="mt-1 text-sm text-text-muted">
              Press Continue to confirm your project name.
            </p>
          </div>
        ) : null}

        {/* Partial — amber. */}
        {phase.kind === "partial" ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <p className="text-sm font-medium text-amber-300">
              ⚠ {FIRST_DOCS_COPY.outcomes.partial.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {phase.status.imported} of {phase.status.total} files imported.{" "}
              {phase.status.failed} failed.
            </p>
            <p className="mt-2 text-xs text-text-muted">
              {FIRST_DOCS_COPY.outcomes.partial.body}
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
