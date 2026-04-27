# Build Kickoff — paste this into the new chat

Copy everything below the line into a fresh Claude Code conversation, opened in this same project directory. Project memory and CONTEXT.md auto-load on session start.

---

I'm starting the implementation phase of a tool we've spent the last several iterations scoping. Working directory contains the full context.

**Authoritative spec:** `CONTEXT.md` in this directory. Read it in full before doing anything. Treat the Flexibility Principle (§0) as a binding constraint on every design decision.

**Status:**
- Discovery + sample-grounding + multiple review cycles complete.
- Prompt prototyping done (`prompts/PROMPT_V0.md`) — two prompts run by hand against real sample documents (OSE Chiller spec via standard text-extraction path; AirTrunk SYD2 BOD via structured-import path). The SME confirmed the approach and surfaced specific corrections that must land in v1.
- Real sample corpus available at `Samples/` (349 files: 286 PDFs, 24 xlsx, 19 docx, 18 dwg, 2 pptx — AirTrunk SYD2 Data Centre, Shell C + D110).
- Discovery + walkthrough audit trail in `archive/2026-04-26-{discovery,taxonomy-review,sample-walkthrough,final-review}/`.

**The next real risk** (per the SME's parting note) is whether the extraction prompt strictly enforces the §3 deliverable definition. The verification check is captured in CONTEXT.md §3 ("Enforcement at extraction time") — three-outcome gate (INSIDE / OUTSIDE-with-audit / BORDERLINE-to-HITL). Don't relax it.

**What I'd like you to do, in order:**

1. **Read CONTEXT.md and prompts/PROMPT_V0.md in full.** Don't skim. The locks were earned through structured review.
2. **Propose an MVP scope** for v1 — what ships, what's explicitly deferred, with the v1.x analyses (Compliance Traceability, OSE Procurement Completeness, Trade Overlap, Quantity Reconciliation, Dependency Dangling References) all DEFERRED unless the SME later confirms one as must-have.
3. **Draft prompt v1** — both the standard text-spec extraction prompt and the BOD structured-import prompt, integrating ALL the corrections in `prompts/PROMPT_V0.md` (vendor attribution rule, OSE granularity rollup, BOD trade defaulting to specialist trade, hybrid Option C negotiated rendering, locked trade taxonomy names, DCS dual-axis, BOD scope-exclusion mechanism). Apply v1 by hand against a few sample docs and show me the output.
4. **Sketch the data model** — SQLite schema with field types, indexes, and the source-doc table (which carries `document_state`, `document_class`, plus the per-document quality scan output) separately from the deliverables table.
5. **Sketch key UX flows** — project create → doc import → run → review (quarantine queue UX is the highest-value flow) → export.
6. **Then** start scaffolding code.

**What you should NOT do:**

- Do not start writing code before steps 1–5. Building infrastructure before validating prompts is the backwards risk profile we explicitly chose to avoid.
- Do not re-litigate locked decisions in CONTEXT.md without strong cause. The locks each represent multiple review cycles. Refinements on locked items go via the SME, not by your judgement.
- Do not collapse the structured-import path into the free-text extraction path — they're deliberately separate.

**Ground rules:**

- **Brief is good.** Long preambles waste context. State, decide, move.
- **No silent decisions.** When you hit a fork CONTEXT.md doesn't resolve, surface it; don't guess.
- **Real verification, not claimed verification.** When you say a prompt extracts well, show me the output against a real sample doc.
- The SME (construction sector domain expert) and I will pair on review. She owns construction-domain decisions; I own tooling/architecture decisions.

When ready, start with step 1 (read) and confirm what you've absorbed before proposing step 2.
