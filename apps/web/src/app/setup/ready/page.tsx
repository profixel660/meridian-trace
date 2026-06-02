"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuthGate } from "@/components/AuthGate";
import { SetupShell } from "@/components/setup/SetupShell";
import { READY_COPY } from "@/components/setup/copy";
import { MeridianApiError } from "@/lib/api";
import {
  DEFAULT_SETUP_STATE,
  type SetupState,
  setupApi,
  pipelineApi,
} from "@/lib/setupClient";

/**
 * Shape of the structured 400 detail the backend returns when gates are unmet.
 * Contract pinned by test_complete_returns_400_with_next_step_when_gates_unmet.
 */
interface SetupIncompleteDetail {
  error: "setup_incomplete";
  next_step: string;
  message: string;
}

/**
 * Extract the structured 400 detail from a MeridianApiError thrown by
 * setupApi.complete(). Returns null for any other error shape.
 *
 * The body is a raw JSON string of the form:
 *   {"detail": {"error": "setup_incomplete", "next_step": "...", "message": "..."}}
 */
function extractSetupErrorDetail(
  err: unknown,
): SetupIncompleteDetail | null {
  if (!(err instanceof MeridianApiError) || err.status !== 400) {
    return null;
  }
  try {
    const parsed = JSON.parse(err.body) as {
      detail?: SetupIncompleteDetail | string;
    };
    const detail = parsed?.detail;
    if (
      detail &&
      typeof detail === "object" &&
      detail.error === "setup_incomplete" &&
      typeof detail.next_step === "string" &&
      typeof detail.message === "string"
    ) {
      return detail;
    }
  } catch {
    // Body wasn't valid JSON — not a structured error.
  }
  return null;
}

/**
 * Step 4 — Ready.
 *
 * On mount:
 *  1. Reads `/setup/state` to populate summary tiles
 *  2. POSTs `/setup/complete` (idempotent) so the next launch doesn't
 *     relaunch the wizard
 *
 * AuthGate is mounted here (not on earlier steps) — by this point the
 * user has been issued a session token by Stream C's project-creation
 * flow, so any 401 here means the token expired or was cleared and the
 * standard /login bounce is the right behaviour.
 *
 * The body is wrapped in <Suspense> because `useSearchParams` requires a
 * boundary under Next's static-export mode (see next.config.ts).
 */
export default function SetupReadyPage() {
  return (
    <Suspense
      fallback={
        <div className="py-10 text-sm text-text-muted">Loading…</div>
      }
    >
      <ReadyPageInner />
    </Suspense>
  );
}

function ReadyPageInner() {
  const search = useSearchParams();
  const skippedHint = search?.get("skipped") === "1";

  const [state, setState] = useState<SetupState>(DEFAULT_SETUP_STATE);
  const [stateLoaded, setStateLoaded] = useState(false);

  type CompleteError =
    | { kind: "incomplete"; message: string; next_step: string }
    | { kind: "generic"; message: string };

  const [completeError, setCompleteError] = useState<CompleteError | null>(
    null,
  );
  // true from the moment stateLoaded fires (useEffect below) until the POST
  // resolves either way.  Initialised to true so the Open Project button is
  // disabled before the first render that kicks off the POST.
  const [completeInFlight, setCompleteInFlight] = useState(true);
  // Bumping this re-runs the complete() effect — used by the "Try again" button.
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await setupApi.state();
        if (!cancelled) setState(result);
      } catch {
        // Stream C may not have shipped yet — the page still renders the
        // skipped/imported summary based on the URL hint.
        if (!cancelled) {
          setState({
            ...DEFAULT_SETUP_STATE,
            documents_skipped: skippedHint,
          });
        }
      } finally {
        if (!cancelled) setStateLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [skippedHint]);

  // Idempotent — safe to call on every mount (and on retry). If the backend
  // returns 400 (gates unmet) we surface the reason instead of silently
  // swallowing it.  retryNonce allows the "Try again" button to re-trigger.
  useEffect(() => {
    if (!stateLoaded) return;
    let cancelled = false;
    setCompleteInFlight(true);
    void setupApi.complete().then(
      async () => {
        if (cancelled) return;
        setCompleteError(null);

        // Alpha-25: kick off bootstrap+extract immediately after the wizard
        // reports complete. The user opens the dashboard a second later; the
        // PipelineProgressTile picks up the stashed job_id and polls.
        if (state.first_project_slug) {
          const idempotencyKey = crypto.randomUUID();
          try {
            const res = await pipelineApi.start(state.first_project_slug, {}, { idempotencyKey });
            try {
              window.sessionStorage.setItem(
                "meridian.setup.pipeline_job_id",
                res.job_id,
              );
            } catch {
              // sessionStorage blocked — dashboard tile falls back to the
              // /pipeline (latest) GET path. No user action needed.
            }
          } catch (err) {
            // Silent — dashboard tile's fallback path covers this. We do NOT
            // block the wizard's open-project navigation on a kick-off failure.
            // eslint-disable-next-line no-console
            console.warn("[setup/ready] pipeline kick-off failed", err);
          }
        }

        setCompleteInFlight(false);
      },
      (err: unknown) => {
        if (cancelled) return;
        const detail = extractSetupErrorDetail(err);
        if (detail !== null) {
          setCompleteError({
            kind: "incomplete",
            message: detail.message,
            next_step: detail.next_step,
          });
        } else {
          setCompleteError({
            kind: "generic",
            message: "Could not finish setup. Please try again.",
          });
        }
        setCompleteInFlight(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [stateLoaded, retryNonce, state.first_project_slug]);

  const skipped = state.documents_skipped || skippedHint;
  const fileCount = state.documents_imported;
  const chunkCount = state.chunks_extracted;
  const projectSlug =
    state.first_project_slug ||
    (typeof window !== "undefined"
      ? window.sessionStorage.getItem("meridian.setup.project_slug")
      : null) ||
    "";
  const projectName =
    state.first_project_name ||
    (typeof window !== "undefined"
      ? window.sessionStorage.getItem("meridian.setup.project_name")
      : null) ||
    projectSlug;

  const openHref = projectSlug
    ? `/projects/${encodeURIComponent(projectSlug)}`
    : "/";

  return (
    <>
      <AuthGate />
      <SetupShell
        step="ready"
        backHref="/setup/first-project"
        completed={[
          "welcome",
          "api-key",
          "first-documents",
          "first-project",
        ]}
      >
        <div className="space-y-8">
          {completeError ? (
            <div
              id="setup-incomplete-alert"
              role="alert"
              className="rounded-lg border border-red-500/40 bg-red-950/40 p-4 text-sm text-red-200"
            >
              <p className="font-semibold">Setup did not complete</p>
              <p className="mt-1">{completeError.message}</p>
              {completeError.kind === "incomplete" ? (
                <Link
                  href={`/setup/${completeError.next_step.replace(/_/g, "-")}`}
                  className="mt-3 inline-block rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white"
                >
                  Continue: {completeError.next_step.replace(/_/g, " ")} →
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => setRetryNonce((n) => n + 1)}
                  className="mt-3 inline-block rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white"
                >
                  Try again
                </button>
              )}
            </div>
          ) : null}

          <header className="space-y-3">
            <p className="text-xs uppercase tracking-wide text-emerald-300">
              ✓ Setup complete
            </p>
            <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
              {READY_COPY.title}
            </h1>
            {READY_COPY.hero({ docCount: fileCount, skipped })}
          </header>

          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SummaryTile
              label={READY_COPY.summaryLabels.apiKey}
              value={state.api_key_set ? "Connected ✓" : "Not yet"}
            />
            <SummaryTile
              label={READY_COPY.summaryLabels.project}
              value={projectName || "—"}
              hint={projectSlug ? `slug: ${projectSlug}` : undefined}
            />
            <SummaryTile
              label={READY_COPY.summaryLabels.docs}
              value={
                skipped
                  ? "None yet — import from the Sources screen"
                  : fileCount === 0 && chunkCount === 0
                    ? "Queued"
                    : chunkCount === 0
                      ? `${fileCount} ${fileCount === 1 ? "document" : "documents"} imported`
                      : `${chunkCount} chunks from ${fileCount} ${fileCount === 1 ? "document" : "documents"}`
              }
            />
            <SummaryTile
              label={READY_COPY.summaryLabels.folder}
              value={state.first_project_dir ?? "Default Meridian folder"}
              mono
            />
          </dl>

          <div className="flex flex-wrap gap-3 pt-2">
            {completeInFlight || completeError ? (
              <button
                type="button"
                disabled
                aria-describedby={
                  completeError ? "setup-incomplete-alert" : undefined
                }
                className="cursor-not-allowed rounded-full bg-text-muted/20 px-5 py-2.5 text-sm font-medium text-text-muted"
                title={
                  completeInFlight
                    ? "Verifying setup…"
                    : completeError?.kind === "generic"
                      ? "Meridian stopped responding — use 'My projects' to navigate away, or try again"
                      : "Finish setup before opening the project"
                }
              >
                {READY_COPY.ctas.open} →
              </button>
            ) : (
              <Link
                href={openHref}
                className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90"
              >
                {READY_COPY.ctas.open} →
              </Link>
            )}
            <Link
              href="/onboarding"
              className="rounded-full border border-border px-5 py-2.5 text-sm text-text-primary hover:border-accent"
            >
              {READY_COPY.ctas.tour}
            </Link>
            <Link
              href="/glossary"
              className="rounded-full border border-border px-5 py-2.5 text-sm text-text-primary hover:border-accent"
            >
              {READY_COPY.ctas.glossary}
            </Link>
          </div>

          <section className="border-t border-border pt-6">
            <h2 className="text-sm font-semibold text-text-primary">
              {READY_COPY.whatsNextTitle}
            </h2>
            <ul className="mt-3 space-y-2 text-sm text-text-muted">
              {READY_COPY.whatsNext.map((line) => (
                <li key={line} className="flex gap-2">
                  <span aria-hidden="true" className="text-accent">
                    •
                  </span>
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </section>

          <p className="text-xs text-text-muted">
            Press{" "}
            <kbd className="rounded border border-border px-1">Enter</kbd> to
            open your project, or{" "}
            <kbd className="rounded border border-border px-1">?</kbd> for
            keyboard shortcuts.
          </p>
        </div>
      </SetupShell>
    </>
  );
}

function SummaryTile({
  label,
  value,
  hint,
  mono = false,
}: {
  label: string;
  value: string;
  hint?: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-elevated p-4">
      <dt className="text-xs uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd
        className={`mt-1 truncate text-sm text-text-primary ${
          mono ? "font-mono text-xs" : ""
        }`}
        title={value}
      >
        {value}
      </dd>
      {hint ? (
        <p className="mt-1 truncate font-mono text-[10px] text-text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
