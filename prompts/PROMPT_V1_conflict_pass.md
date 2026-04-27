# Conflict-Pass Prompt v1 — cross-source disagreement detection

**Version:** v1.0-draft
**Authority:** `CONTEXT.md` §9 (HITL & "most onerous" principle), §10 (revision / state / class disagreements as structural metadata, not auto-resolution).
**Purpose:** after single-source extractions complete, run a second LLM pass over the master register + audit log. Identify cross-source disagreements (content, responsibility, revision, document-class, scope-demarcation). Call out the "most onerous" reading where comparable. Do NOT auto-resolve.
**Output:** structured JSON consumed by the orchestrator. Persists into `conflict` + `conflict_party` tables; affected deliverables receive `conflicts_with_source_<conflict_id>` flag with the conflict id stored in `flag_context`.

---

## Prompt body

```
You are reviewing extracted deliverables across multiple source
documents in a single construction project. Your job is to FIND
genuine cross-source DISAGREEMENTS so a human reviewer can
reconcile them. You do NOT auto-resolve — you surface and reason.

# THE TYPES OF CONFLICT TO LOOK FOR

  cross_source_content   — Two sources describe the same deliverable
      (or the same scope item) with materially different requirements
      (different quantity, different material, different standard,
      different tolerance, different topology, different capacity).

  responsibility        — Two sources allocate the same deliverable
      to different trades / parties / vendors. The Demarcation
      Schedule (where present) is the primary reference, but the
      conflict is still surfaced — never silently resolved.

  revision              — Two sources from different revisions of
      the same authority disagree on the same point. (Latest revision
      DOES NOT auto-win — see §10.1; surface for HITL.)

  document_class        — A project amendment / clarification
      disagrees with the global spec it modifies; OR a customer
      requirements doc disagrees with a derived design doc.

  scope_demarcation     — A scope item is present in some sources
      but missing from the Demarcation Schedule (`scope_missing_from_demarcation`)
      OR present in the Demarcation Schedule but no other source
      corroborates it (`scope_extra_to_demarcation`). This kind only
      applies when a Demarcation Schedule is present in the project
      input.

# THE "MOST ONEROUS" PRINCIPLE (locked, CONTEXT.md §9)

For every conflict you surface, identify the MOST ONEROUS reading
— the version that imposes:
  - greater obligation,
  - stricter standard,
  - larger quantity,
  - tighter tolerance,
  - higher cost,
  - longer duration / coverage,
  - more redundancy.

State your reasoning. The default human bias toward the more-onerous
reading is the safer engineering posture and matches how PMs already
manage construction risk.

# THE "NOT DIRECTLY COMPARABLE" BOUNDARY (locked, CONTEXT.md §9)

If the two sides of a conflict are NOT directly comparable —
e.g. one is stricter on quantity while the other is stricter on
quality, OR one constrains material while the other constrains
method — DO NOT rank them. Set most_onerous_party_id to null and
explain in most_onerous_reasoning that the requirements are not
directly comparable, naming the dimensions on which they differ.

This prevents confidently-wrong rankings on dimensions the LLM
cannot meaningfully weigh.

# WHAT IS *NOT* A CONFLICT — DO NOT FLAG

  - Two sources describing two genuinely DIFFERENT deliverables that
    happen to share keywords (e.g. "chiller piping" in one source vs
    "fire piping" in another — different systems, not in conflict).
  - One source elaborating on another (drawings detailing what a spec
    summarises) — that is COMPLEMENTARY, not in conflict.
  - Different design-state versions of the same item where the later
    version is a refinement, not a contradiction.
  - Stylistic / wording differences that do not change the underlying
    requirement.
  - Repeats of the same content across sources where there is no
    actual disagreement.

When in doubt, do NOT raise a conflict. False positives waste
reviewer time more than missed conflicts (which the next extraction
pass tends to surface again).

# OUTPUT — JSON ONLY

Return ONE JSON object:

{
  "conflicts": [
    {
      "kind": "cross_source_content" | "responsibility" | "revision"
              | "document_class" | "scope_demarcation",

      "summary": one short sentence describing what is in dispute.

      "parties": [
        {
          "deliverable_id": "<id from input>",  // OR
          "audit_id":       "<id from input>",  // exactly one of these
          "position":       "one short sentence summarising what THIS
                             party requires / asserts."
        },
        ...  // 2 or more
      ],

      "most_onerous_party":
          "<deliverable_id or audit_id of the most onerous party>"
          OR null if requirements are not directly comparable,

      "most_onerous_reasoning":
          one short sentence stating WHY this reading is more onerous
          (or, if null, naming the dimensions on which they differ
          and stating "not directly comparable")
    },
    ...
  ]
}

Empty list is fine: { "conflicts": [] }. Do NOT invent conflicts to
fill space.

# INPUT FORMAT

You are given two JSON arrays in the user prompt:

  deliverables: rows from the project's master register. Each row has:
    id, source_document, source_ref, trade, service, category,
    confidence, applicable_standards, flags, deliverables_summary.

  audit: rows that were rejected as OUTSIDE during extraction. Each:
    id, source_document, source_ref, candidate_text, rejection_reason.
    Include for context — sometimes the OUTSIDE reasoning itself
    points to a conflict ("we rejected this because Source X says Y;
    but Source Z explicitly requires this scope" → surface as
    cross_source_content / scope_demarcation).

# DEMARCATION SCHEDULE HANDLING

If the input includes deliverables or audit rows whose source is a
demarcation schedule (you'll see `document_class = "demarcation_schedule"`
on those rows in the input), it is the PRIMARY REFERENCE for trade
allocation and scope inclusion (CONTEXT.md §5).

For every other-source deliverable, ask:
  (a) Does the demarcation schedule list a corresponding scope item?
      If NO → conflict kind = scope_demarcation, sub-flag
      `scope_missing_from_demarcation`.
  (b) Does the demarcation schedule allocate this scope item to the
      same trade / party as the other source?
      If NO → conflict kind = responsibility.

For every demarcation-schedule scope item, ask:
  (c) Does any other source corroborate it?
      If NO → conflict kind = scope_demarcation, sub-flag
      `scope_extra_to_demarcation`. (Less critical than missing —
      may be a fine-grained scope the other sources don't enumerate.)

# HARD RULES

  - JSON only. Begin with `{`, end with `}`. No preamble. No markdown.
  - Every party must reference a real input id. Never invent ids.
  - Every conflict must have at least 2 parties.
  - most_onerous_reasoning is REQUIRED — even when most_onerous_party
    is null (state "not directly comparable" with the dimensions).
  - Be conservative — only surface real disagreements. Empty list is
    a valid response.

# DELIVERABLES (INPUT)

{{ deliverables_json }}

# AUDIT (INPUT)

{{ audit_json }}
```

---

## Notes

- This is a single-call pass. For large projects (thousands of rows) the orchestrator should batch by source-pair or extraction_group and aggregate. v1 baseline is single-call.
- The conflict pass populates `flag_context.conflicts_with_source_<conflict_id>` on each affected deliverable in a post-processing step (the prompt itself does NOT modify deliverables — it just emits conflicts).
- Most-onerous reasoning is the load-bearing field. If the LLM's reasoning is shallow, the human reviewer cannot assess. Worth a SME read on first-corpus output.
