import Link from "next/link";

import { FLAG_EXPLANATIONS } from "@/components/review/flagExplanations";

export const metadata = {
  title: "Glossary — Meridian",
  description:
    "Plain-English explanations of every domain term Meridian uses in its review queues.",
};

interface Term {
  /** URL slug (used by tooltip "More →" deep links). */
  id: string;
  term: string;
  body: string;
}

const TERMS: Term[] = [
  {
    id: "deliverable",
    term: "Deliverable",
    body: "A discrete obligation a contractor or trade must hand over — a duct run, a structural beam, a fire-rated penetration. Meridian extracts deliverables from heterogeneous source documents and assembles them into a master register.",
  },
  {
    id: "gate-outcome",
    term: "Gate outcome (INSIDE / OUTSIDE / BORDERLINE)",
    body: "Every candidate the AI extracts is judged against the deliverable definition. INSIDE means it cleanly matches and goes onto the master register. OUTSIDE means it doesn't match and is logged in Audit. BORDERLINE means the AI is unsure and lands in Quarantine for you to decide.",
  },
  {
    id: "three-outcome-gate",
    term: "Three-outcome gate",
    body: "The decision logic above (INSIDE / OUTSIDE / BORDERLINE). Forces the AI to surface ambiguity rather than guess — every BORDERLINE call becomes work for the reviewer instead of a silent error.",
  },
  {
    id: "warning-marker",
    term: "⚠ marker",
    body: "A visible warning prefix attached to deliverable summaries when one or more flags need a human glance. The summary still reads cleanly; the marker is your cue to expand and check the flags.",
  },
  {
    id: "audit",
    term: "Audit (OUTSIDE log)",
    body: "Every candidate the gate ruled OUTSIDE is logged here so nothing the AI considered is invisible. If a row should actually be on the master register, promote it from the Audit queue.",
  },
  {
    id: "demarcation-schedule",
    term: "Demarcation Schedule",
    body: "The authoritative source for trade / responsibility allocation in many AEC projects. When another source's responsibility allocation disagrees with the Demarcation Schedule, Meridian raises a responsibility_conflict flag.",
  },
  {
    id: "ose",
    term: "OSE (Owner-Supplied Equipment)",
    body: "Equipment supplied by the project owner rather than by the base-build contractor. Affects which trade is responsible for installation and connection.",
  },
  {
    id: "bod",
    term: "BOD (Basis of Design)",
    body: "The technical narrative document that captures design intent and engineering assumptions. Often used as a cross-check against drawings and schedules.",
  },
  {
    id: "nrc",
    term: "NRC (Non-Recoverable Costs / Customer Fit-out)",
    body: "Scope that's been shifted from the base-build contract to the customer fit-out package. The deliverable still exists; responsibility moves elsewhere.",
  },
  {
    id: "taxonomy-proposal",
    term: "Taxonomy proposal",
    body: "Trade / service / category values are project-extensible but controlled. When the AI hits a value not in the canonical list it enters as a proposal for you to confirm, merge, or reject.",
  },
  {
    id: "conflict",
    term: "Conflict",
    body: "Two or more sources disagree about the same item. Conflict kinds include: cross_source_content (same deliverable, different details), responsibility (which trade owns it), revision (which revision is authoritative), document_class (status of the source), and scope_demarcation (where one party's scope ends and another's begins).",
  },
  {
    id: "most-onerous",
    term: "Most onerous",
    body: "When sources conflict, Meridian flags the more demanding interpretation as the safer default — the side that costs more to comply with. You can still accept the other side; the reasoning is shown for transparency.",
  },
  {
    id: "not-directly-comparable",
    term: "Not directly comparable",
    body: "When two sides of a conflict cover overlapping ground but use different units, scopes, or framings, the AI marks the conflict as not directly comparable and asks you to decide rather than picking automatically.",
  },
  {
    id: "quarantine",
    term: "Quarantine",
    body: "The review queue for deliverables held back from the master register. Items land here when the gate said BORDERLINE, the confidence was low, or a flag was raised. Accept / Edit / Reject moves them out.",
  },
  {
    id: "master-register",
    term: "Master register",
    body: "The single source of truth for accepted deliverables in the project. Read-only in the UI; the Excel export is the share-able artefact.",
  },
  {
    id: "applicable-standards",
    term: "Applicable standards",
    body: "Codes, design guides, and standards the deliverable must comply with (e.g. AS 1668, NCC 2022). Captured per-row and surfaced on the master register.",
  },
  {
    id: "flag",
    term: "Flag",
    body: "A short coded marker the AI attaches to a deliverable to call out a specific concern — TBD values, inferred trades, conflicts, etc. Each flag has plain-English copy attached; hover any pill to read it.",
  },
  {
    id: "provenance",
    term: "Provenance",
    body: "Every deliverable links back to its source document, the chunk of text it came from, the extraction job that produced it, and the LLM call that generated it. Full provenance is required for the master register to be considered baseline trustworthy.",
  },
  {
    id: "coverage",
    term: "Coverage",
    body: "The dashboard that summarises every queue, the master register status, and provenance / cost completeness. Use it to judge how close the project is to baseline trustworthy.",
  },
  {
    id: "baseline-trustworthiness",
    term: "Baseline trustworthiness",
    body: "A project hits baseline trustworthy when every queue is empty, every source has been extracted, and provenance is complete. At that point the master register is safe to share with the project team without further qualification.",
  },
  {
    id: "auto-route-rate",
    term: "Auto-route rate",
    body: "The percentage of extracted deliverables the gate routed without needing human review. A high rate means the AI is well-calibrated for this project; a low rate means more reviewer effort per source.",
  },
  {
    id: "deliverable-status",
    term: "Deliverable status",
    body: "Lifecycle states for a single deliverable: auto_approved (gate cleared cleanly), quarantined (held for review), user_accepted / user_edited / user_promoted (acted by reviewer), user_rejected (removed from master).",
  },
  {
    id: "confidence",
    term: "Confidence",
    body: "The AI's self-rated confidence for a deliverable: high / medium / low. Low confidence is one of the triggers that lands a row in Quarantine.",
  },
  {
    id: "review",
    term: "Review",
    body: "Any human action on the queues — accept, reject, edit, promote, resolve, dismiss, confirm, merge. Every review action is recorded against the project for the audit trail.",
  },
  {
    id: "question",
    term: "Question",
    body: "An ambiguity the AI raised during extraction that it can't safely resolve on its own. You answer in plain English; the answer feeds back into future extractions.",
  },
  {
    id: "tender-package",
    term: "Tender Package",
    body: "A per-trade hand-off file (xlsx or md) issued to subcontractors during procurement. Built by the Tender Package Builder from accepted deliverables only — quarantined and rejected rows are excluded. Includes a cover sheet (project metadata, source documents, applicable standards, flag summary, items still needing review) and the deliverables grouped by service then category. Building a tender package is purely an assembly step: no LLM calls and no changes to deliverable status.",
  },
  {
    id: "legal-evidence-pack",
    term: "Legal Evidence Pack",
    body: "A defensible audit-trail bundle suitable for hand-off to construction lawyers, claims teams, or opposing counsel. The pack is a single timestamped zip containing the deliverables register, every LLM call (prompt + response + cost + model), every source document, full provenance links, schema notes, and a cover.md describing the contents. What it proves: that the deliverable register was assembled from these specific sources, with these specific LLM calls, on this date. What it does not prove: that the underlying source documents are authoritative or that the human reviewer's decisions were correct. Building a pack is purely an assembly step — no LLM calls, no DB writes to deliverable status.",
  },
  {
    id: "cross-reference-sweep",
    term: "Cross-reference sweep",
    body: "An exhaustive scan of every ingested document for textual references to other project sources — section numbers, standards, drawing IDs, equipment tags, owner IDs, spec references. Each anchor is resolved to another project source where possible. Outcomes are three-way: confirmed (anchor resolved to a known project source), borderline (ambiguous — added to the SME review queue when persisted), and external_reference (legitimately points outside the project, e.g. to a published standard). Rejected findings are noisy false positives the deterministic pass discards. Run dry-run for a preview without DB writes; persist to commit findings and queue borderlines for SME review. LLM-assist is opt-in for ambiguous anchors and adds per-anchor cost.",
  },
  {
    id: "routing-preset",
    term: "Routing preset",
    body: "A named bundle of provider + model choices for each extraction purpose (triage, text-spec extraction, BOD extraction, conflict pass, etc.). Apply one with `meridian routing apply <preset>`. Presets come in two name forms: operator-facing aliases (cloud-default, hybrid, air-gapped) that describe deployment intent, and technical names (cloud-sonnet-default, ollama-5090-balanced, ollama-air-gapped) that describe what the preset actually configures. Either form works at the CLI; aliases resolve to technical names at apply time.",
  },
  {
    id: "air-gap-mode",
    term: "Air-gap mode",
    body: "A per-project setting that forbids any LLM call to a non-local provider. When air-gap is on, calls to Anthropic / OpenAI / etc. fail loudly at call time rather than silently sending data over the network. Pair with the air-gapped routing preset for a fully-offline configuration (local Ollama for every purpose). Toggle via `meridian routing air-gap-on/-off`.",
  },
];

export default function GlossaryPage() {
  return (
    <article className="space-y-10">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Glossary
        </h1>
        <p className="max-w-prose text-sm text-text-muted">
          Plain-English definitions for every domain term Meridian uses. Linked
          from every tooltip — if you ever land on a term that isn&apos;t here,
          ping the team and we&apos;ll add it.
        </p>
      </header>

      <section className="space-y-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Core terms
        </h2>
        <dl className="space-y-6">
          {TERMS.map((t) => (
            <div key={t.id} id={t.id} className="scroll-mt-24">
              <dt className="text-base font-semibold text-text-primary">
                {t.term}
              </dt>
              <dd className="mt-1 max-w-prose text-sm leading-relaxed text-text-muted">
                {t.body}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="space-y-4">
        <h2
          id="flag-vocabulary"
          className="scroll-mt-24 text-sm font-semibold uppercase tracking-wide text-text-muted"
        >
          Flag vocabulary
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          The full set of flag codes the AI may attach to a deliverable.
          Conflict-family flags follow the dynamic{" "}
          <code>conflicts_with_source_&lt;id&gt;</code> form — open the
          relevant Conflicts queue row to see both sides.
        </p>
        <dl className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {Object.entries(FLAG_EXPLANATIONS).map(([code, exp]) => (
            <div
              key={code}
              id={`flag-${code}`}
              className="scroll-mt-24 rounded-md border border-border bg-surface-elevated p-4"
            >
              <dt>
                <code className="font-mono text-xs text-text-primary">
                  {code}
                </code>
                <span className="ml-2 text-sm font-semibold text-text-primary">
                  {exp.title}
                </span>
              </dt>
              <dd className="mt-1 text-sm leading-relaxed text-text-muted">
                {exp.body}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <footer className="border-t border-border pt-6 text-xs text-text-muted">
        <Link href="/" className="hover:text-text-primary">
          ← Back to projects
        </Link>
      </footer>
    </article>
  );
}
