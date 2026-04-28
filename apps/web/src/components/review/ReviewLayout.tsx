import Link from "next/link";
import type { ReactNode } from "react";

interface QueueCounts {
  quarantine?: number | null;
  audit?: number | null;
  questions?: number | null;
  conflicts?: number | null;
  taxonomy?: number | null;
}

interface ReviewLayoutProps {
  projectName: string;
  /** Heading for the page (page-specific, not the project name). */
  title: string;
  /** Optional one-line subtitle under the title. */
  subtitle?: ReactNode;
  /** Pending counts to render as badges next to each nav link. */
  counts?: QueueCounts;
  /** Optional right-aligned action slot (e.g. "Download Excel"). */
  actions?: ReactNode;
  children: ReactNode;
}

// Alpha-16: pruned to the core review surface. The other queues
// (audit / questions / conflicts / taxonomy) still exist as routes and
// can be reached by URL; they're hidden from the nav strip until the
// core deliverables-extraction loop has been validated end-to-end.
const QUEUES: Array<{ key: keyof QueueCounts; label: string; href: string }> = [
  { key: "quarantine", label: "Quarantine", href: "quarantine" },
];

/**
 * Common shell for every page under `/projects/[name]/...`.
 *
 * Renders:
 *   - "← Back to dashboard" link
 *   - the project slug as the section identifier
 *   - the queue nav strip with pending-count badges
 *   - the page-specific title/subtitle/actions header
 *   - a slot for the page content
 */
export function ReviewLayout({
  projectName,
  title,
  subtitle,
  counts,
  actions,
  children,
}: ReviewLayoutProps) {
  const base = `/projects/${encodeURIComponent(projectName)}`;
  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-muted">
        <Link href={base} className="hover:text-text-primary">
          ← {projectName}
        </Link>
        <span aria-hidden="true">•</span>
        <Link href="/" className="hover:text-text-primary">
          All projects
        </Link>
      </div>

      <nav
        aria-label="Review queues"
        className="flex flex-wrap gap-2 rounded-lg border border-border bg-surface-elevated p-2"
      >
        {QUEUES.map((q) => {
          const count = counts?.[q.key];
          return (
            <Link
              key={q.key}
              href={`${base}/${q.href}`}
              className="group flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-text-muted hover:bg-surface hover:text-text-primary"
            >
              <span>{q.label}</span>
              {typeof count === "number" ? (
                <span
                  className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${
                    count > 0
                      ? "bg-accent/20 text-accent"
                      : "bg-surface text-text-muted"
                  }`}
                  aria-label={`${count} pending`}
                >
                  {count}
                </span>
              ) : null}
            </Link>
          );
        })}
        <span className="ml-auto flex items-center gap-2">
          <Link
            href={`${base}/sources`}
            className="rounded-md px-3 py-1.5 text-sm text-text-muted hover:bg-surface hover:text-text-primary"
          >
            Sources
          </Link>
          <Link
            href={`${base}/master`}
            className="rounded-md px-3 py-1.5 text-sm text-text-muted hover:bg-surface hover:text-text-primary"
          >
            Master register
          </Link>
        </span>
      </nav>

      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            {title}
          </h1>
          {subtitle ? (
            <div className="text-sm text-text-muted">{subtitle}</div>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>

      {children}
    </div>
  );
}
