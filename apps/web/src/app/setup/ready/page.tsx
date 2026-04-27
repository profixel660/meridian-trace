"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AuthGate } from "@/components/AuthGate";
import { SetupShell } from "@/components/setup/SetupShell";
import { READY_COPY } from "@/components/setup/copy";
import {
  DEFAULT_SETUP_STATE,
  type SetupState,
  setupApi,
} from "@/lib/setupClient";

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

  // Idempotent — safe to call on every mount.
  useEffect(() => {
    if (!stateLoaded) return;
    void setupApi.complete().catch(() => {
      // Silent — this is a fire-and-forget marker. The user already
      // sees the "you're ready" state; if the marker fails to land the
      // worst case is the wizard offers itself again next launch, which
      // is fine.
    });
  }, [stateLoaded]);

  const skipped = state.documents_skipped || skippedHint;
  const docCount = state.documents_imported;
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
        backHref="/setup/first-documents"
        completed={[
          "welcome",
          "api-key",
          "first-project",
          "first-documents",
        ]}
      >
        <div className="space-y-8">
          <header className="space-y-3">
            <p className="text-xs uppercase tracking-wide text-emerald-300">
              ✓ Setup complete
            </p>
            <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
              {READY_COPY.title}
            </h1>
            {READY_COPY.hero({ docCount, skipped })}
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
                  : docCount === 0
                    ? "Queued"
                    : `${docCount} imported`
              }
            />
            <SummaryTile
              label={READY_COPY.summaryLabels.folder}
              value={state.first_project_dir ?? "Default Meridian folder"}
              mono
            />
          </dl>

          <div className="flex flex-wrap gap-3 pt-2">
            <Link
              href={openHref}
              className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90"
            >
              {READY_COPY.ctas.open} →
            </Link>
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
