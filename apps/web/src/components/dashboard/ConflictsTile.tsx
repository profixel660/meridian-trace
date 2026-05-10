"use client";

import Link from "next/link";

interface Props {
  projectSlug: string;
  pendingCount: number;
  resolvedCount: number;
  /**
   * Whether this tile gets the "Start here →" highlight.
   * Computed by the dashboard's counts-as-CTA logic (alpha-26 §7.3).
   */
  isPrimaryCTA: boolean;
}

export function ConflictsTile({
  projectSlug,
  pendingCount,
  resolvedCount,
  isPrimaryCTA,
}: Props) {
  const base = `/projects/${encodeURIComponent(projectSlug)}`;

  if (pendingCount > 0) {
    return (
      <section
        className={`rounded-lg border p-5 transition ${
          isPrimaryCTA
            ? "border-amber-500/60 bg-amber-500/5 shadow-[0_0_24px_rgba(251,191,36,0.15)]"
            : "border-amber-500/40 bg-amber-500/5"
        }`}
      >
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-3xl font-bold text-amber-300">
            {pendingCount}
          </span>
          <p className="text-sm font-medium text-text-primary">
            pending conflict{pendingCount === 1 ? "" : "s"} need a call from you
          </p>
        </div>
        <p className="mt-2 text-sm text-text-muted">
          Two sources disagree about the same item. Resolve in queue order
          or jump straight from the conflict register.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            href={`${base}/conflicts`}
            className={`rounded-full px-4 py-1.5 text-xs font-medium ${
              isPrimaryCTA
                ? "bg-amber-500 text-surface hover:opacity-90"
                : "bg-amber-500/15 text-amber-300 hover:bg-amber-500/25"
            }`}
          >
            {isPrimaryCTA ? "Start here →" : "Open queue →"}
          </Link>
          <Link
            href={`${base}#hierarchy`}
            className="rounded-full bg-accent/15 px-4 py-1.5 text-xs text-accent hover:bg-accent/25"
          >
            View hierarchy →
          </Link>
          <Link
            href={`${base}/conflict-register`}
            className="rounded-full bg-surface px-4 py-1.5 text-xs text-text-muted hover:text-text-primary"
          >
            Open register →
          </Link>
        </div>
      </section>
    );
  }

  if (resolvedCount > 0) {
    return (
      <section className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-5">
        <p className="text-sm font-medium text-emerald-300">
          ✓ All {resolvedCount} cross-source conflict
          {resolvedCount === 1 ? "" : "s"} {resolvedCount === 1 ? "has" : "have"} been resolved
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            href={`${base}#hierarchy`}
            className="rounded-full bg-accent/15 px-4 py-1.5 text-xs text-accent hover:bg-accent/25"
          >
            View hierarchy →
          </Link>
          <Link
            href={`${base}/conflict-register`}
            className="rounded-full bg-surface px-4 py-1.5 text-xs text-text-muted hover:text-text-primary"
          >
            Open register →
          </Link>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border bg-surface-elevated p-5">
      <p className="text-sm font-medium text-text-primary">
        No cross-source conflicts detected yet
      </p>
      <p className="mt-1 text-sm text-text-muted">
        Conflicts surface when two sources disagree about the same deliverable.
        They'll appear here as your sources build up.
      </p>
    </section>
  );
}
