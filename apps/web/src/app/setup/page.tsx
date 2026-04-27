"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { SetupShell } from "@/components/setup/SetupShell";
import { WELCOME_COPY } from "@/components/setup/copy";
import {
  DEFAULT_SETUP_STATE,
  type SetupState,
  setupApi,
} from "@/lib/setupClient";

/**
 * Wizard entry / welcome page.
 *
 * Loads `/setup/state` to decide whether to show the standard four-tile
 * welcome or the "you're already set up" panel. If Stream C's endpoint
 * isn't yet shipped, the catch falls back to `DEFAULT_SETUP_STATE` so
 * the wizard still renders meaningful content during local development.
 */
export default function SetupWelcomePage() {
  const router = useRouter();
  const [state, setState] = useState<SetupState | null>(null);
  const [stateError, setStateError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await setupApi.state();
        if (!cancelled) setState(result);
      } catch (err) {
        // Until Stream C lands the endpoint, gracefully fall back to a
        // default "not yet set up" state so the wizard is still usable.
        if (!cancelled) {
          setState(DEFAULT_SETUP_STATE);
          setStateError(err);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const start = () => router.push("/setup/api-key");

  return (
    <SetupShell
      step="welcome"
      onContinue={start}
      continueLabel="Get started →"
      continueDisabled={loading}
    >
      {loading ? (
        <p className="text-sm text-text-muted">Checking setup status…</p>
      ) : (
        <div className="space-y-8">
          <header className="space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
              {WELCOME_COPY.title}
            </h1>
            <p className="text-sm text-text-muted">{WELCOME_COPY.subtitle}</p>
          </header>

          {stateError ? (
            <details className="rounded border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-text-muted">
              <summary className="cursor-pointer text-amber-300">
                Couldn&apos;t reach the Meridian API yet — running in offline
                preview mode.
              </summary>
              <div className="mt-2">
                <ApiErrorPanel error={stateError} />
              </div>
            </details>
          ) : null}

          {state?.complete ? (
            <AlreadySetUpPanel />
          ) : (
            <>
              <div className="text-text-primary">{WELCOME_COPY.hero()}</div>

              <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {WELCOME_COPY.steps.map((s) => (
                  <li
                    key={s.n}
                    className="rounded-lg border border-border bg-surface-elevated p-4"
                  >
                    <div className="flex items-center gap-3">
                      <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent/20 text-sm font-semibold text-accent">
                        {s.n}
                      </span>
                      <span className="text-sm font-medium text-text-primary">
                        {s.title}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-text-muted">{s.blurb}</p>
                    <p className="mt-2 text-[11px] uppercase tracking-wide text-text-muted">
                      {s.time}
                    </p>
                  </li>
                ))}
              </ol>

              <button
                type="button"
                onClick={start}
                className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90"
              >
                Get started →
              </button>

              <section className="border-t border-border pt-6">
                <h2 className="text-sm font-semibold text-text-primary">
                  {WELCOME_COPY.readMoreHeader}
                </h2>
                <ul className="mt-3 space-y-2">
                  {WELCOME_COPY.readMoreLinks.map((l) => (
                    <li key={l.href}>
                      <Link
                        href={l.href}
                        className="block rounded border border-border bg-surface p-3 hover:border-accent/60"
                      >
                        <span className="text-sm font-medium text-text-primary">
                          {l.label} →
                        </span>
                        <p className="mt-1 text-xs text-text-muted">
                          {l.blurb}
                        </p>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          )}
        </div>
      )}
    </SetupShell>
  );
}

function AlreadySetUpPanel() {
  return (
    <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-6">
      <h2 className="text-base font-semibold text-text-primary">
        {WELCOME_COPY.alreadySetUp.title}
      </h2>
      <p className="mt-2 text-sm text-text-muted">
        {WELCOME_COPY.alreadySetUp.body}
      </p>
      <div className="mt-4 flex flex-wrap gap-3">
        <Link
          href="/"
          className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {WELCOME_COPY.alreadySetUp.primaryLabel}
        </Link>
        <Link
          href="/setup/api-key"
          className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary"
        >
          {WELCOME_COPY.alreadySetUp.secondaryLabel}
        </Link>
      </div>
    </div>
  );
}
