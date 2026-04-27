"use client";

import { Tooltip, TooltipMore } from "./Tooltip";
import { explainFlag } from "./flagExplanations";

interface FlagPillProps {
  flag: string;
}

const ACCENT_BY_PREFIX: { match: (f: string) => boolean; cls: string }[] = [
  // Conflict-family flags get a red tint to draw the eye.
  {
    match: (f) =>
      f.startsWith("conflicts_with_source_") || f === "responsibility_conflict",
    cls: "border-red-500/40 bg-red-500/10 text-red-300",
  },
  // Borderline / definitional grey-area flags amber.
  {
    match: (f) =>
      f === "definition_borderline" ||
      f === "negotiated_response" ||
      f === "scope_shifted_to_nrc",
    cls: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  },
  // Neutral default.
  { match: () => true, cls: "border-border bg-surface-elevated text-text-muted" },
];

function classFor(flag: string): string {
  for (const rule of ACCENT_BY_PREFIX) if (rule.match(flag)) return rule.cls;
  return ACCENT_BY_PREFIX[ACCENT_BY_PREFIX.length - 1]!.cls;
}

/**
 * Renders a flag code as a small pill. On hover/focus shows a tooltip
 * with the plain-English explanation from `flagExplanations.ts`, plus
 * a "More →" deep-link to /glossary#flag-<code>.
 */
export function FlagPill({ flag }: FlagPillProps) {
  const exp = explainFlag(flag);
  return (
    <Tooltip
      content={
        <span>
          <strong className="block text-text-primary">{exp.title}</strong>
          <span className="mt-1 block text-text-muted">{exp.body}</span>
          <TooltipMore href={`/glossary#flag-${encodeURIComponent(flag)}`} />
        </span>
      }
    >
      <button
        type="button"
        className={`inline-flex cursor-help items-center rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-tight ${classFor(
          flag,
        )}`}
      >
        {flag}
      </button>
    </Tooltip>
  );
}
