import Link from "next/link";
import type { ReactNode } from "react";

interface EmptyStateProps {
  /** Headline e.g. "No quarantined items". */
  title: string;
  /** Plain-English explanation of what this queue is for and what
   *  triggers an item to land here. */
  body: ReactNode;
  /** Optional glossary link for the term that defines this queue. */
  learnMoreHref?: string;
  learnMoreLabel?: string;
  /** Optional primary CTA rendered as an accent button above the learn-more link. */
  ctaHref?: string;
  ctaLabel?: string;
}

/**
 * Empty states are tutorials (Discoverability rule #3). Never render a
 * bare "no data" — always explain the queue + the action that would
 * populate it.
 */
export function EmptyState({
  title,
  body,
  learnMoreHref,
  learnMoreLabel = "What is this queue?",
  ctaHref,
  ctaLabel = "Add documents",
}: EmptyStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface-elevated p-8 text-center">
      <h3 className="text-base font-semibold text-text-primary">{title}</h3>
      <div className="mx-auto mt-2 max-w-prose text-sm leading-relaxed text-text-muted">
        {body}
      </div>
      {ctaHref ? (
        <Link
          href={ctaHref}
          className="mt-4 inline-block rounded-full bg-accent px-5 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {ctaLabel} →
        </Link>
      ) : null}
      {learnMoreHref ? (
        <Link
          href={learnMoreHref}
          className="mt-4 inline-block text-sm font-medium text-accent hover:underline"
        >
          {learnMoreLabel} →
        </Link>
      ) : null}
    </div>
  );
}
