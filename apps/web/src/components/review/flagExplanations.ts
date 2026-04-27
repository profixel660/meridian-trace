/**
 * Plain-English explanations for every flag the extractor / reviewer can
 * emit on a deliverable or audit row. Single source of truth for the
 * tooltip text rendered by `<FlagPill>` and used by the glossary page.
 *
 * CONTEXT.md §8 enumerates the canonical flag vocabulary; this module is
 * the UI mirror.
 */

export interface FlagExplanation {
  /** Short PM-friendly title shown as the tooltip headline. */
  title: string;
  /** One-paragraph explanation, no jargon. */
  body: string;
}

/** Static map. `conflicts_with_source_<id>` is matched dynamically below. */
export const FLAG_EXPLANATIONS: Record<string, FlagExplanation> = {
  unclear_language: {
    title: "Unclear language",
    body: "The source wording is ambiguous; the AI made its best guess. Worth checking the source ref.",
  },
  tbd_placeholder: {
    title: "TBD / placeholder value",
    body: "The source contains 'TBD' / 'TBA' / similar — the deliverable exists but the value is pending.",
  },
  markup_present: {
    title: "Markup present",
    body: "The source has annotations or markups overlapping this content.",
  },
  outdated_reference: {
    title: "Outdated reference",
    body: "Refers to a drawing or section that's superseded or missing.",
  },
  drawing_unreadable: {
    title: "Drawing unreadable",
    body: "Visual quality of the source compromised extraction.",
  },
  revision_ambiguous: {
    title: "Revision ambiguous",
    body: "Multiple revisions of the source exist; which is authoritative isn't clear.",
  },
  trade_inferred: {
    title: "Trade inferred",
    body: "Trade was inferred rather than explicit in the source. Verify against your scope.",
  },
  service_inferred: {
    title: "Service inferred",
    body: "Service was inferred rather than explicit in the source.",
  },
  quantity_uncertain: {
    title: "Quantity uncertain",
    body: "Quantity is present but unclear or hard to extract.",
  },
  provisional_design_stage: {
    title: "Provisional design stage",
    body: "Source is at an early design stage — this deliverable is provisional.",
  },
  template_detected: {
    title: "Template detected",
    body: "Source appears to be an unfilled template; auto-excluded from extraction.",
  },
  responsibility_conflict: {
    title: "Responsibility conflict",
    body: "Trade/responsibility allocation disagrees with the Demarcation Schedule (or another source).",
  },
  scope_missing_from_demarcation: {
    title: "Missing from Demarcation Schedule",
    body: "Found in another source but not in the Demarcation Schedule's scope.",
  },
  scope_extra_to_demarcation: {
    title: "Extra to Demarcation Schedule",
    body: "Listed in the Demarcation Schedule but no other source corroborates.",
  },
  definition_borderline: {
    title: "Borderline against deliverable definition",
    body: "Genuinely ambiguous against the deliverable definition; the AI surfaced it for your decision.",
  },
  negotiated_response: {
    title: "Negotiated / qualified response",
    body: "Source row has a qualified compliance response (e.g. 'Comply with conditions'). Read the qualifier text in the row detail.",
  },
  scope_shifted_to_nrc: {
    title: "Shifted to NRC scope",
    body: "Deliverable exists, but delivery has shifted from base-build to customer fit-out NRC scope.",
  },
  taxonomy_new_value_proposed: {
    title: "New taxonomy value proposed",
    body: "The AI proposed a new taxonomy value (trade/service/category). Confirm or merge in Taxonomy review.",
  },
};

const CONFLICT_PREFIX = "conflicts_with_source_";

/**
 * Resolve the explanation for any flag string, including the dynamic
 * `conflicts_with_source_<id>` family. Returns a generic fallback for
 * unknown values rather than `undefined` — discoverability rule #1
 * (every flag pill must show *something* on hover).
 */
export function explainFlag(flag: string): FlagExplanation {
  if (flag.startsWith(CONFLICT_PREFIX)) {
    const sourceId = flag.slice(CONFLICT_PREFIX.length);
    return {
      title: "Conflicts with another source",
      body: `Disagrees with another source on the same deliverable (source id ${sourceId}). Open the conflict to see both sides.`,
    };
  }
  const known = FLAG_EXPLANATIONS[flag];
  if (known) return known;
  return {
    title: flag,
    body: "Unrecognised flag. Check CONTEXT.md §8 for the full vocabulary, or hover the source row for context.",
  };
}
