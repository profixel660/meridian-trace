import Link from "next/link";
import type { ReactNode } from "react";

interface OnboardingShellProps {
  /** 1-based step number (1, 2, or 3). */
  step: 1 | 2 | 3;
  /** Page title rendered as <h1>. */
  title: string;
  /** Optional one-line subtitle under the title. */
  subtitle?: string;
  /** Main page content. */
  children: ReactNode;
  /** Where the "Back" button links. */
  backHref: string;
  /** Where the primary forward button links. */
  nextHref: string;
  /** Label for the primary forward button. Defaults to "Continue →". */
  nextLabel?: string;
}

/**
 * Shared shell for the four onboarding screens. Renders a "Setup • step N of 3"
 * strip, the prose-width content area, and a Back / Continue button bar.
 *
 * Server-rendered — no client interactivity required.
 */
export function OnboardingShell({
  step,
  title,
  subtitle,
  children,
  backHref,
  nextHref,
  nextLabel = "Continue →",
}: OnboardingShellProps) {
  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-text-muted">
        <span>Setup</span>
        <span aria-hidden="true">•</span>
        <span>Step {step} of 3</span>
      </div>

      <header className="max-w-[70ch] space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          {title}
        </h1>
        {subtitle ? (
          <p className="text-sm text-text-muted">{subtitle}</p>
        ) : null}
      </header>

      <section className="prose-onboarding max-w-[70ch] space-y-6 text-sm leading-relaxed text-text-primary">
        {children}
      </section>

      <nav
        aria-label="Onboarding navigation"
        className="flex max-w-[70ch] items-center justify-between border-t border-border pt-6"
      >
        <Link
          href={backHref}
          className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary"
        >
          ← Back
        </Link>
        <Link
          href={nextHref}
          className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {nextLabel}
        </Link>
      </nav>
    </div>
  );
}
