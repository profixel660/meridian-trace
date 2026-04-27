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
}: EmptyStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface-elevated p-8 text-center">
      <h3 className="text-base font-semibold text-text-primary">{title}</h3>
      <div className="mx-auto mt-2 max-w-prose text-sm leading-relaxed text-text-muted">
        {body}
      </div>
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
