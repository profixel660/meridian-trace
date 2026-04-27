"use client";

import Link from "next/link";

export type SetupStepKey =
  | "welcome"
  | "api-key"
  | "first-project"
  | "first-documents"
  | "ready";

interface StepDef {
  key: SetupStepKey;
  label: string;
  /** 1-based position in the visible progress strip. */
  n: number;
  href: string;
}

/**
 * Five named segments in wizard order. Welcome is step 0 (the entry tile)
 * and the four numbered steps are 1..4 — matching the four-step preview
 * on the welcome page.
 *
 * Alpha-2 reframe (folder-pick): documents come BEFORE project so the
 * folder name can pre-fill the project-name input on the next page.
 * The natural PM mental model is "pick folder → name appears → review
 * → done", and `/setup/projects/suggest-name` (Stream A) pre-fills the
 * name from the folder path captured on the documents step.
 */
const STEPS: StepDef[] = [
  { key: "welcome", label: "Start", n: 0, href: "/setup" },
  { key: "api-key", label: "Claude key", n: 1, href: "/setup/api-key" },
  {
    key: "first-documents",
    label: "Project folder",
    n: 2,
    href: "/setup/first-documents",
  },
  {
    key: "first-project",
    label: "Project name",
    n: 3,
    href: "/setup/first-project",
  },
  { key: "ready", label: "Ready", n: 4, href: "/setup/ready" },
];

interface StepNavProps {
  /** Which step is currently active. */
  current: SetupStepKey;
  /** Steps the user has already completed (renders a tick). */
  completed?: SetupStepKey[];
}

/**
 * Visual progress strip across the top of every wizard page.
 *
 * Discoverability rule (action affordances): each completed step is a
 * link back to that page; the current step is highlighted; future steps
 * are dimmed and not clickable (no jumping ahead — the wizard requires
 * each step's data before advancing).
 */
export function StepNav({ current, completed = [] }: StepNavProps) {
  const activeIdx = STEPS.findIndex((s) => s.key === current);
  // Hide the welcome tile from the count strip — it's an entry, not a step.
  const numbered = STEPS.filter((s) => s.n > 0);

  return (
    <nav
      aria-label="Setup progress"
      className="flex items-center gap-2 text-xs"
    >
      {numbered.map((step, i) => {
        const isCurrent = step.key === current;
        const isComplete =
          completed.includes(step.key) ||
          STEPS.findIndex((s) => s.key === step.key) < activeIdx;
        const isFuture = !isCurrent && !isComplete;

        const dot = isComplete ? "✓" : String(step.n);
        const dotCls = isCurrent
          ? "bg-accent text-white"
          : isComplete
            ? "bg-emerald-600 text-white"
            : "bg-surface-elevated text-text-muted border border-border";
        const labelCls = isCurrent
          ? "text-text-primary font-medium"
          : isComplete
            ? "text-text-primary"
            : "text-text-muted";

        const tile = (
          <span className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold ${dotCls}`}
            >
              {dot}
            </span>
            <span className={labelCls}>{step.label}</span>
          </span>
        );

        return (
          <span key={step.key} className="flex items-center gap-2">
            {isComplete && !isCurrent ? (
              <Link
                href={step.href}
                className="rounded px-1 py-0.5 hover:bg-surface-elevated"
                aria-label={`Step ${step.n}, ${step.label} (completed) — go back`}
              >
                {tile}
              </Link>
            ) : (
              <span
                aria-current={isCurrent ? "step" : undefined}
                aria-label={`Step ${step.n}, ${step.label}${
                  isCurrent ? " (current)" : isFuture ? " (upcoming)" : ""
                }`}
                className={isFuture ? "opacity-60" : undefined}
              >
                {tile}
              </span>
            )}
            {i < numbered.length - 1 ? (
              <span aria-hidden="true" className="text-text-muted">
                ─
              </span>
            ) : null}
          </span>
        );
      })}
    </nav>
  );
}
