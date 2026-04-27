# Decisions to make — for review with collaborator

Six decisions that need to land before CONTEXT.md can be hardened. Listed in approximate priority order.

---

## 1. What is a "deliverable"?
*(ChatGPT flagged this as the #1 gap, agreed.)*

We need a working definition. Without it, the LLM and users will disagree on what to extract.

Examples of the question's edges:
- Are full drawings deliverables? Or only the items shown on them?
- Are specifications themselves deliverables? Or only the items they specify?
- Are submittals deliverables? Or only items within submittals?
- Are project-management artefacts (program, RFI register, ITP) deliverables?

Goal: a one-paragraph definition the LLM can be prompted with consistently.

Definition: A deliverable is a system, component of a system, building component or other physical or logical item that will form part of the structure or services of the building to form the full building and its complete end to end services. 

---

## 2. Excel role — export-only (PoC) or round-trip from day one?

- **Export-only:** Excel is regenerated from SQLite. User edits don't survive re-runs. Simpler.
- **Round-trip:** User edits in Excel, re-imports, edits preserved. Requires stable hidden row IDs and a re-import flow.
- **Recommended PoC compromise:** export-only for v1, but bake the stable IDs in now. Costs nothing extra and unlocks round-trip later without a schema migration.

Decision: confirm the compromise, or commit to round-trip from v1?

Decision: Confirm the Recommended PoC compromise.

---

## 3. Document revision authority — which rev wins?

Initial instinct: latest rev wins, HITL when ambiguous. Nuance worth confirming:

- **Default:** latest rev wins, with the tool showing *how* it decided (filename, metadata, content).
- **HITL trigger:** when status flags and recency disagree (e.g. older "Issued for Construction" rev exists alongside a newer "Draft" or "For Review" rev — the older IFC often outranks).
- **Project-level override:** user can pin authoritative revisions if auto-detection is consistently wrong for their org's conventions.

Decision: confirm this layered rule, or simplify?

Decision: HITL trigger then pin authoritative revision with a Project-level override if auto-detection is consistently wrong.

---

## 4. Workspace model — per-project SQLite vs single DB with project FK?

Foundation choice that shapes everything downstream (data isolation, portability, backup, future multi-user).

- **Per-project SQLite file:** clean isolation, easy to share/archive a project, simple backup. Harder to do cross-project queries later.
- **Single DB, project_id FK:** unified queries possible, simpler app state, but a single bigger thing to back up and harder to share an individual project.

Decision needed. Recommend per-project for the PoC unless cross-project analytics is on the roadmap.

Decision: Per-project SQLite File.

---

## 5. Granularity — how does the user steer collective items?

When a doc says "100 type-A light fittings," does that become 1 row or 100? Configurable per project? Default behaviour?

Decision: pick a default, decide whether it's overrideable per project or per extraction.

Decision: this becomes 1 row where the item is for a single trade or service, there are instances where trades will have a common deliverable or component.

---

## 6. Provider scope — narrow v1 to Anthropic + OpenAI only?

Currently the brief lists Anthropic / OpenAI / Gemini / Azure / Bedrock. Five providers = five sets of edge cases to support during a PoC.

ChatGPT recommends limiting initially. Recommend narrowing v1 to **Anthropic + OpenAI** (the two most likely org-mandated), defer Gemini / Azure / Bedrock to v1.x.

Decision: agree on narrowing, or keep all five from day one?

Decision: agree on narrowing initially as recommended.

---

## Smaller items already proposed for locking (please confirm or push back):

- **Trade and service columns: nullable.** Some deliverables (process / contractual / meta) genuinely fit neither.
- **Category axis: lock IN.** With null tolerance. Values: design / procurement / coordination / delivery (extensible).
- **Multi-tag handling: multi-row, not comma-separated.** Comma-separated values in structured columns is an antipattern.
- **Reproducibility logging: broaden** beyond model + prompt to include temperature, top_p, max_tokens, system prompt, input hash, API version.
- **HITL queue: pauses are batched, not interactive.** Tool collects ambiguity questions during a run; user resolves them all at once when ready. Preserves "drop docs and walk away" UX.
- **Source reference table in CONTEXT.md is illustrative**, not exhaustive — generalise the wording.
- **Pipeline assumption (OCR → LLM)** to be relaxed; vision-capable LLMs may go direct on some inputs.

Decision: Confirm for all no comments.
