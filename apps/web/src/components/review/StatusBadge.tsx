"use client";

import { Tooltip, TooltipMore } from "./Tooltip";

type Variant =
  | "status"
  | "gate"
  | "confidence"
  | "review"
  | "neutral"
  | "warn";

interface StatusBadgeProps {
  /** Display text and tooltip lookup key. */
  label: string;
  /** Visual / semantic family — drives colour. */
  variant?: Variant;
  /** Override the auto-derived tooltip explanation. */
  explanation?: { title: string; body: string };
  /** Glossary anchor; defaults to a sensible per-variant target. */
  glossaryAnchor?: string;
}

/** Built-in plain-English copy for the strings backend produces. */
const COPY: Record<string, { title: string; body: string }> = {
  // deliverable.status
  quarantined: {
    title: "Quarantined",
    body: "Held back from the master register pending your review. Either the gate said BORDERLINE / OUTSIDE, the AI was uncertain, or a flag was raised. Action it from the Quarantine queue.",
  },
  auto_approved: {
    title: "Auto-approved",
    body: "Cleared the gate cleanly with high confidence — already on the master register. You can still edit if needed.",
  },
  user_accepted: {
    title: "Accepted by reviewer",
    body: "You (or another reviewer) explicitly accepted this deliverable.",
  },
  user_edited: {
    title: "Edited by reviewer",
    body: "A reviewer adjusted this deliverable; the original is preserved as its parent.",
  },
  user_promoted: {
    title: "Promoted from audit",
    body: "Started life as an OUTSIDE / rejected candidate; a reviewer promoted it back to the master register.",
  },
  user_rejected: {
    title: "Rejected by reviewer",
    body: "Removed from the master register; kept for audit trail.",
  },
  // gate_outcome
  INSIDE: {
    title: "Gate: INSIDE",
    body: "The deliverable definition matched cleanly. Goes straight to the master register.",
  },
  OUTSIDE: {
    title: "Gate: OUTSIDE",
    body: "Did not match the deliverable definition. Logged in the Audit queue, not the master.",
  },
  BORDERLINE: {
    title: "Gate: BORDERLINE",
    body: "Genuinely ambiguous — the AI surfaced it for your decision. Lives in Quarantine until you act.",
  },
  // confidence
  high: {
    title: "High confidence",
    body: "Source wording was clear and the gate aligned. Safe to skim-review.",
  },
  medium: {
    title: "Medium confidence",
    body: "The AI is reasonably sure. Worth a glance at the source ref.",
  },
  low: {
    title: "Low confidence",
    body: "Significant uncertainty — definitely review the source before accepting.",
  },
};

function styleFor(variant: Variant, label: string): string {
  if (variant === "warn") {
    return "border-red-500/40 bg-red-500/10 text-red-300";
  }
  if (variant === "gate") {
    if (label === "INSIDE")
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    if (label === "OUTSIDE")
      return "border-red-500/40 bg-red-500/10 text-red-300";
    if (label === "BORDERLINE")
      return "border-amber-500/40 bg-amber-500/10 text-amber-300";
  }
  if (variant === "confidence") {
    if (label === "high")
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    if (label === "medium")
      return "border-amber-500/40 bg-amber-500/10 text-amber-300";
    if (label === "low")
      return "border-red-500/40 bg-red-500/10 text-red-300";
  }
  if (variant === "status" && label === "quarantined") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-300";
  }
  return "border-border bg-surface-elevated text-text-muted";
}

const ANCHOR_BY_VARIANT: Record<Variant, string> = {
  status: "/glossary#deliverable-status",
  gate: "/glossary#gate-outcome",
  confidence: "/glossary#confidence",
  review: "/glossary#review",
  neutral: "/glossary",
  warn: "/glossary#warning-marker",
};

export function StatusBadge({
  label,
  variant = "status",
  explanation,
  glossaryAnchor,
}: StatusBadgeProps) {
  const exp = explanation ?? COPY[label] ?? {
    title: label,
    body: "Open the glossary for the full explanation.",
  };
  const anchor = glossaryAnchor ?? ANCHOR_BY_VARIANT[variant];
  return (
    <Tooltip
      content={
        <span>
          <strong className="block text-text-primary">{exp.title}</strong>
          <span className="mt-1 block text-text-muted">{exp.body}</span>
          <TooltipMore href={anchor} />
        </span>
      }
    >
      <button
        type="button"
        className={`inline-flex cursor-help items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${styleFor(
          variant,
          label,
        )}`}
      >
        {label}
      </button>
    </Tooltip>
  );
}
