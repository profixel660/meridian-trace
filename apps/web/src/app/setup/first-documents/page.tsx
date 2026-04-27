"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { EmptyState } from "@/components/review/EmptyState";
import { SetupShell } from "@/components/setup/SetupShell";
import { FIRST_DOCS_COPY } from "@/components/setup/copy";
import {
  setupApi,
  type ImportJobStatus,
} from "@/lib/setupClient";
import { isInTauri, pickFiles } from "@/lib/tauri";

interface PickedFile {
  path: string;
  /** Just the file name from the path, for display. */
  display: string;
}

type Phase =
  | { kind: "picking" }
  | { kind: "importing"; jobId: string; status: ImportJobStatus | null }
  | { kind: "imported"; status: ImportJobStatus }
  | { kind: "partial"; status: ImportJobStatus }
  | { kind: "failed"; status: ImportJobStatus | null; message: string }
  | { kind: "skipped" };

const TERMINAL_STATUSES = new Set([
  "imported_all",
  "imported_partial",
  "failed",
]);

function basename(path: string): string {
  const m = path.match(/[^/\\]+$/);
  return m ? m[0] : path;
}

/**
 * Step 3 — Import your first documents.
 *
 * Uses the Tauri native multi-file picker when available (returns real
 * absolute paths). In browser fallback the picker can't return paths at
 * all, so the page surfaces a clear "open the desktop app or skip"
 * message.
 *
 * Three-outcome on import:
 *  - imported_all → advance enabled
 *  - imported_partial → per-file table with retry; advance enabled
 *  - failed → ApiErrorPanel-equivalent block with log path; advance NOT
 *    enabled (user must retry or skip explicitly)
 */
export default function SetupFirstDocumentsPage() {
  const router = useRouter();
  const [tauri, setTauri] = useState(false);
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [phase, setPhase] = useState<Phase>({ kind: "picking" });
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [projectSlug, setProjectSlug] = useState<string>("");
  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setTauri(isInTauri());
    try {
      setProjectSlug(
        window.sessionStorage.getItem("meridian.setup.project_slug") ?? "",
      );
    } catch {
      setProjectSlug("");
    }
  }, []);

  // Cleanup polling on unmount
  useEffect(
    () => () => {
      if (pollHandle.current) clearInterval(pollHandle.current);
    },
    [],
  );

  const pickHandler = async () => {
    const picked = await pickFiles({
      extensions: ["pdf", "doc", "docx"],
      multiple: true,
      title: "Pick documents to import",
    });
    if (picked.length) {
      setFiles((cur) => {
        // Dedupe — adding same path twice is a no-op.
        const seen = new Set(cur.map((f) => f.path));
        const additions = picked
          .filter((p) => !seen.has(p))
          .map((p) => ({ path: p, display: basename(p) }));
        return [...cur, ...additions];
      });
    }
  };

  const removeFile = (path: string) =>
    setFiles((cur) => cur.filter((f) => f.path !== path));

  const startImport = useCallback(async () => {
    if (files.length === 0 || !projectSlug) return;
    try {
      const res = await setupApi.importDocuments(
        projectSlug,
        files.map((f) => f.path),
      );
      setPhase({ kind: "importing", jobId: res.job_id, status: null });
      // Poll every 1s until terminal
      pollHandle.current = setInterval(async () => {
        try {
          const s = await setupApi.importStatus(res.job_id);
          setPhase((prev) =>
            prev.kind === "importing"
              ? { kind: "importing", jobId: prev.jobId, status: s }
              : prev,
          );
          if (TERMINAL_STATUSES.has(s.status)) {
            if (pollHandle.current) {
              clearInterval(pollHandle.current);
              pollHandle.current = null;
            }
            if (s.status === "imported_all") {
              setPhase({ kind: "imported", status: s });
            } else if (s.status === "imported_partial") {
              setPhase({ kind: "partial", status: s });
            } else {
              setPhase({
                kind: "failed",
                status: s,
                message: "Import failed — see log path below.",
              });
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
  }, [files, projectSlug]);

  const handleSkip = () => setConfirmSkip(true);

  const confirmSkipNow = async () => {
    setConfirmSkip(false);
    setPhase({ kind: "skipped" });
    // Tell the backend so /setup/complete's documents_skipped gate is met.
    // Best-effort: even if this fails (no project slug yet, network blip),
    // the /setup/complete call on the ready page will surface a clear error.
    if (projectSlug) {
      try {
        await setupApi.skipImport(projectSlug);
      } catch {
        // ignore — ready page surfaces gate failures
      }
    }
    router.push("/setup/ready?skipped=1");
  };

  const handleContinue = () => router.push("/setup/ready");

  const canContinue =
    phase.kind === "imported" ||
    phase.kind === "partial" ||
    phase.kind === "skipped";
  const isImporting = phase.kind === "importing";

  return (
    <SetupShell
      step="first-documents"
      backHref="/setup/first-project"
      onContinue={handleContinue}
      continueDisabled={!canContinue}
      busy={isImporting}
    >
      <div className="space-y-8">
        <header>
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            {FIRST_DOCS_COPY.title}
          </h1>
        </header>

        <section
          aria-label="Why this step"
          className="rounded-lg border border-accent/30 bg-accent/5 p-4 text-sm text-text-primary"
        >
          {FIRST_DOCS_COPY.why()}
        </section>

        {!tauri ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <p className="text-sm font-medium text-amber-300">
              {FIRST_DOCS_COPY.browserModeBlock.title}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {FIRST_DOCS_COPY.browserModeBlock.body}
            </p>
            <button
              type="button"
              onClick={handleSkip}
              className="mt-3 rounded-full border border-amber-500/50 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-500/10"
            >
              {FIRST_DOCS_COPY.skipLabel}
            </button>
          </div>
        ) : null}

        {tauri ? (
          <>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => void pickHandler()}
                disabled={isImporting}
                className="rounded-full border border-border bg-surface-elevated px-4 py-2 text-sm text-text-primary hover:border-accent disabled:opacity-40"
              >
                {FIRST_DOCS_COPY.pickButton}
              </button>
              <p className="text-xs text-text-muted">
                {FIRST_DOCS_COPY.pickHelper}
              </p>
            </div>

            {files.length === 0 ? (
              <EmptyState
                title={FIRST_DOCS_COPY.emptyTitle}
                body={FIRST_DOCS_COPY.emptyBody}
              />
            ) : (
              <ul className="divide-y divide-border rounded-lg border border-border bg-surface-elevated">
                {files.map((f) => (
                  <li
                    key={f.path}
                    className="flex items-center justify-between gap-3 px-4 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <p
                        className="truncate text-sm text-text-primary"
                        title={f.path}
                      >
                        {f.display}
                      </p>
                      <p className="truncate font-mono text-[10px] text-text-muted">
                        {f.path}
                      </p>
                    </div>
                    {phase.kind === "picking" ? (
                      <button
                        type="button"
                        onClick={() => removeFile(f.path)}
                        className="text-xs text-text-muted hover:text-red-300"
                      >
                        {FIRST_DOCS_COPY.removeLabel}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}

            {files.length > 0 && phase.kind === "picking" ? (
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => void startImport()}
                  disabled={!projectSlug}
                  className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
                >
                  {FIRST_DOCS_COPY.importLabel(files.length)}
                </button>
                <button
                  type="button"
                  onClick={handleSkip}
                  className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary"
                >
                  {FIRST_DOCS_COPY.skipLabel}
                </button>
                {!projectSlug ? (
                  <p className="text-xs text-amber-300">
                    No project slug found in this session — go back to step 2 and
                    create the project first.
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}

        {/* Progress bar */}
        {phase.kind === "importing" ? (
          <div className="rounded-lg border border-border bg-surface-elevated p-4">
            <p className="text-sm font-medium text-text-primary">
              Importing…{" "}
              {phase.status
                ? `${phase.status.completed} of ${phase.status.total}`
                : ""}
            </p>
            <div
              className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round((phase.status?.progress ?? 0) * 100)}
            >
              <div
                className="h-full bg-accent transition-[width]"
                style={{
                  width: `${Math.round((phase.status?.progress ?? 0) * 100)}%`,
                }}
              />
            </div>
            <p className="mt-2 text-xs text-text-muted">
              You can leave this screen — the import keeps running in the
              background. Status will be on your project dashboard.
            </p>
          </div>
        ) : null}

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
              Press Continue to finish setup.
            </p>
          </div>
        ) : null}

        {phase.kind === "partial" ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <p className="text-sm font-medium text-amber-300">
              ⚠ {FIRST_DOCS_COPY.outcomes.partial.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {phase.status.imported} of {phase.status.total} files imported.{" "}
              {phase.status.errors.length} failed.
            </p>
            {phase.status.errors.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-text-muted">
                {phase.status.errors.slice(0, 5).map((e, i) => (
                  <li key={i} className="break-words">
                    {e}
                  </li>
                ))}
                {phase.status.errors.length > 5 ? (
                  <li className="italic">
                    …and {phase.status.errors.length - 5} more — see the
                    Sources screen for the full list.
                  </li>
                ) : null}
              </ul>
            ) : null}
            <p className="mt-2 text-xs text-text-muted">
              {FIRST_DOCS_COPY.outcomes.partial.body} You can continue now and
              revisit failed files from the Sources screen.
            </p>
          </div>
        ) : null}

        {phase.kind === "failed" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              ✗ {FIRST_DOCS_COPY.outcomes.failed.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">{phase.message}</p>
            {phase.status && phase.status.errors.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-text-muted">
                {phase.status.errors.slice(0, 5).map((e, i) => (
                  <li key={i} className="break-words">
                    {e}
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={() => setPhase({ kind: "picking" })}
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
      </div>

      <ConfirmDialog
        open={confirmSkip}
        title={FIRST_DOCS_COPY.skipConfirm.title}
        body={FIRST_DOCS_COPY.skipConfirm.body}
        confirmLabel={FIRST_DOCS_COPY.skipConfirm.confirm}
        cancelLabel={FIRST_DOCS_COPY.skipConfirm.cancel}
        destructive={false}
        onConfirm={confirmSkipNow}
        onCancel={() => setConfirmSkip(false)}
      />
    </SetupShell>
  );
}
