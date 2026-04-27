# Final Pre-Build Review — Feedback (delivered inline)

Source: collaborator review of CONTEXT.md v3 (post-walkthrough), 2026-04-26.

## Critical pre-build flags

### 1. Deliverable definition has hidden exceptions

Currently:
- strict definition ✅
- plus documentation inclusion ✅
- plus temporary works carve-out ⚠️

**Problem:** Justified differently and not unified.

**Action (1 sentence fix):** Add a unifying rule that deliverables include:
- items that form part of the building, or
- physical works required to realise it (including temporary works)

Without this, edge cases will creep in immediately during extraction.

### 2. "Most onerous" rule is unsafe without a boundary

LLM is required to pick the "most onerous" requirement.

**Problem:** Some conflicts are not comparable.

**Action:** Add — *"If requirements are not directly comparable, do not rank them. Surface both."*

Without this, you will get confident nonsense.

### 3. Demarcation schedule is slightly over-trusted

Called "canonical" but conflicts are still surfaced.

**Problem:** Mixed signal.

**Action:** Reword to *"primary reference for scope allocation"*. Prevents false authority assumptions in implementation.

### 4. Taxonomy needs one stabilising rule

Has extensibility + synonym merging. **Missing:** persistence of decisions.

**Action:** Add — *"once confirmed, a taxonomy value becomes canonical for that project unless explicitly overridden"*.

Without this, taxonomy drifts fast.

### 5. Standards column will get noisy without scope

`applicable_standards` added.

**Problem:** unclear extraction boundary.

**Action:** Limit to standards explicitly tied to the deliverable, not inherited broadly from the document.

## Everything else

- Taxonomy model → solid
- Schema vs flexibility → resolved
- Deliverable scope (Option A) → correct
- Builders works handling → acceptable
- HITL model → strong

## Bottom line

Ready to move forward once those 5 small clarifications are made. No redesign required. No new review cycle.

## Final guardrail before build

> "Does the extraction prompt strictly enforce the deliverable definition?"

That's the next real risk, not the context doc.

---

## Resolution

All 5 fixes applied to CONTEXT.md (v4). Mappings:

1. **§3** — added "Unifying rule (governs all carve-outs below)" subsection with three-leg rule (completed building / physical works incl. temporary / documentation with continuing operational value).
2. **§9** — added "Boundary on most-onerous comparison" paragraph: if requirements are not directly comparable, do NOT rank; surface both with the reasoning stated.
3. **§5** — reworded "canonical master" → "primary reference"; added explicit "primary in the sense of leading the analysis, not in the sense of silently overriding" qualifier.
4. **§5 taxonomy governance** — added "Persistence of decisions" bullet: once confirmed, a value becomes canonical for that project unless explicitly overridden.
5. **§4 column table** — tightened `applicable_standards` description with explicit scope boundary: populate ONLY with standards the source explicitly cites against the deliverable in question; do NOT inherit broadly.

The "extraction prompt strictly enforces the deliverable definition" guardrail is logged as a build-phase concern (the prompt will be authored against §3 verbatim, with the unifying rule as the test gate).
