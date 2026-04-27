# Concepts

This page is the mental model behind Meridian. If you understand the four ideas below — the deliverable definition, the trade/service/category taxonomy, the three-outcome gate, and the authority chain — every queue and every export will make sense.

## Deliverable

A **deliverable** is a discrete obligation a contractor or trade hands over. Concretely: a system, a component of a system, a building component, or another physical or logical item that forms part of the completed structure or its operational services. A duct run, a structural beam, a fire-rated penetration, an O&M manual handed over with the equipment — all deliverables. A weekly progress report, an RFI, a meeting minute — not deliverables.

Three things count as deliverables:

1. **Permanent works** that form part of the completed building.
2. **Physical works needed to realise the building**, including temporary works such as scaffolding, hoardings, and propping that get removed at handover.
3. **Documentation with continuing operational value** to the building or its operator after handover — O&M manuals, BIM models at LOD300+, EPDs, as-built drawings, approved technical submittals, and warranty documentation while it remains in force.

Things that are explicitly **not** deliverables: RFIs, submittal-workflow correspondence (the final approved submittal IS a deliverable; the back-and-forth that produced it is not), programmes and schedules, ITPs, process reports, meeting actions, and general coordination tasks. These are real and important — they just live in their own registers, not in the deliverables register.

The Excel master sheet's *deliverables_summary* column restates each deliverable as a terse, present-tense noun phrase. When a row carries a *negotiated_response* flag (typically from a BOD), the summary picks up a warning marker (`⚠`) so you don't miss it during scanning. The full qualifier text lives in the flag, not in the summary.

## Trade, service, and category

Meridian classifies every deliverable on three axes. All three are nullable: a row may be missing one or more if the source genuinely doesn't say.

- **Trade** — *who does the work.* Labour, skill, contracting party. Default values include Electrical, Mechanical, Hydraulic, Fire, Telecommunications, DCS, Security, Carpentry, Formwork, Concrete, Steel, plus a General Contractor / Principal entry for cross-cutting items, plus per-equipment-class vendors (Chiller Vendor, Generator Vendor, Busway Vendor, and so on).
- **Service** — *what system in the building.* Power distribution, Lighting, HVAC, Fire detection & suppression, Comms/ICT, Security/access control, Hydraulics, DCS / Controls.
- **Category** — *a lightweight secondary classification* used for cross-cutting items that don't sit cleanly on a trade or service. Default values: `design`, `procurement`, `delivery`, `builders_works`. Category is intentionally narrow — if the values start to mix lifecycle, responsibility, and activity types, that's a signal the dimension needs to split, not that more values should be added.

One trade delivers many services. One service draws on many trades. The same word may legitimately appear on both axes — DCS is both a trade (the contractor) and a service (the controls system) — and that is allowed.

When a deliverable spans multiple trades or services, **Meridian creates one row per (deliverable × trade × service) combination.** Comma-separated values in a single cell are deliberately avoided.

### Taxonomies are data, not code

The trade, service, and category lists are **per-project taxonomies stored in the project SQLite**. They are extensible, but extensions go through a confirmation flow — when the LLM proposes a value not in the canonical list it lands as a *taxonomy proposal* in the review queue rather than silently being added. You confirm, merge into an existing value, or reject. Once you've decided, the decision is canonical for that project; you won't be asked again for the same value.

This is the central design principle: anything that reasonably varies between projects (taxonomies, prompts, model routing, provider selection) is data and configurable. Hard-coding any of these is a regression.

## Three-outcome discipline

Every candidate Meridian extracts is judged against the deliverable definition with three possible outcomes — not two:

1. **INSIDE** — the candidate clearly matches the definition. It enters the candidate pool and proceeds through normal extraction.
2. **OUTSIDE** — the candidate clearly doesn't match (it's an RFI, a meeting action, a process report). It is **rejected from the master register but logged for audit** in SQLite, with the LLM's reasoning. You can see what was excluded and why in the audit queue. Items are **not silently lost from existence.**
3. **BORDERLINE** — the candidate is genuinely ambiguous. It's flagged `definition_borderline` and routed to the review queue with the LLM's reasoning. You decide; if you accept, the row is tagged `user_promoted` for the audit trail.

This same yes/no/maybe pattern shows up everywhere in Meridian — in the cross-reference sweep, in conflict surfacing, in confidence scoring. The principle is: when the AI is uncertain, surface the uncertainty rather than guess.

The reason for keeping OUTSIDE items in audit (rather than throwing them away) is simple: a strict gate that silently loses real deliverables is exactly as bad as a loose gate that ships noise. The audit queue lets you spot-check what the AI excluded, so you catch the cases where the gate was wrong.

## Authority chain

Construction documents form a hierarchy. The same fact may appear in several documents that disagree. Meridian tracks three structural attributes on every source — *revision*, *document_state* (design maturity), and *document_class* (the document's role) — and uses them to **inform** conflict surfacing. They do not auto-resolve conflicts.

The default document classes:

- `customer_requirements` — Basis-of-Design (BOD), Owner Project Requirements (OPR), customer Functional Requirements Documents.
- `global_tr` — owner's global Technical Requirements (e.g. `AT-GLOBAL-TR-*`).
- `global_ose_spec` — owner's global Owner-Supplied Equipment specifications.
- `project_amendment` — project-specific amendments to global specs.
- `project_clarification` — project-specific clarifications to global specs.
- `drawing` — project drawings (architectural, mechanical, electrical, etc.).
- `demarcation_schedule` — responsibility / scope demarcation matrices (see below).
- `methodology` — methodology and framework documents (typically excluded from extraction).
- `template` — unfilled templates (auto-excluded).

Document state captures design maturity — `concept`, `30%`, `50%`, `90%`, `100%`, `IFC` (Issued For Construction), `as-built`. Anything below IFC gets a `provisional_design_stage` flag.

When two sources disagree, Meridian:

1. Identifies all the sources in the conflict with their full source references.
2. Calls out the **most onerous** requirement — the version that imposes the greater obligation, the larger quantity, the tighter tolerance, or the higher cost — and explains why.
3. Surfaces the conflict for human decision. **No conflict is silently resolved.**

If two requirements are not directly comparable (one is stricter on quantity, the other on quality; one constrains material, the other constrains method), the LLM is told **not** to rank them — the conflict is surfaced with the reasoning *"requirements are not directly comparable"* and you decide.

### Demarcation Schedule special handling

A **Demarcation Schedule** is a project document whose explicit purpose is to allocate scope items to responsible parties (Supplier, Contractor, Client, Cxa Agent, etc.) in a structured matrix. Where present, it is treated as the **primary reference** for what is in scope and who is responsible — primary in the sense of leading the analysis, not silently overriding other sources. Conflicts with other sources still get surfaced.

Operationally:
- Deliverables extracted from a Demarcation Schedule populate the master register as authoritative scope+allocation rows.
- Items present in the Demarcation Schedule but missing from other docs get flagged `scope_extra_to_demarcation`.
- Items present in other docs but missing from the Demarcation Schedule get flagged `scope_missing_from_demarcation`.
- A document gets the `responsibility_conflict` flag when its trade allocation disagrees with the Demarcation Schedule.

Meridian tells you when a document was processed as a Demarcation Schedule (special handling) rather than as a standard spec.

### BOD response registers

A **Basis of Design** (BOD) or similar customer-supplied requirements register is a tabular document where each row is already a candidate deliverable paired with a formal response (typically `Comply` / `Not Comply` with a clarifying comment). Meridian uses a structured-import path for these:

- **Comply** → row proceeds to the deliverable gate.
- **Not Comply** with reason `N/A` / *"no [feature] requirement"* → out of scope for this project; logged to audit. (The BOD itself defines what's *out*; Meridian honours those exclusions.)
- **Not Comply** with substantive qualifier (*"Comply with design requirements. However..."*) → row proceeds with the `negotiated_response` flag.
- **Comply with conditions** → row proceeds with `negotiated_response`.
- Blank / missing response → flagged `definition_borderline` and `unclear_language`, routed to review.

When a BOD row indicates the deliverable is in scope but is being delivered under the customer's fit-out NRC (Non-Recurring Charge) scope rather than the base-build scope, the `scope_shifted_to_nrc` flag is added. The deliverable still exists in the project; the delivery party shifted.

## The review queue

Extraction puts candidates into SQLite; **none enter the master register without passing the queues.** There are six queues, each with its own `review walk-*` walker:

- **Quarantine** (`walk-quarantine`) — deliverables held back because the AI rated confidence as low or medium, or because a flag was raised, or because the gate said BORDERLINE. You **Accept**, **Edit** (creates a child row tagged `user_edited`, with the parent kept for audit), or **Reject** (kept in the audit trail; never on the master register).
- **Audit** (`walk-audit`) — the OUTSIDE log. Every candidate the gate ruled OUTSIDE lives here so nothing the AI considered is invisible. If a row should actually be on the master register, you can **promote** it (tagged `user_promoted`).
- **Conflicts** (`walk-conflicts`) — pairs (or larger groups) of sources that disagree. Each conflict carries the most-onerous reasoning. You **accept-A**, **accept-B**, **reject-both**, or write a **hybrid**.
- **Questions** (`walk-questions`) — ambiguities the LLM raised during extraction that it can't resolve on its own (a new trade not in the taxonomy, an ambiguous deliverable spanning two services, an unfamiliar equipment tag). You **resolve** by typing a plain-English answer or **dismiss**. Resolutions feed back into future extractions.
- **Taxonomy proposals** (`walk-taxonomy`) — the LLM proposed a trade, service, or category value that isn't in the project's canonical list. You **confirm** (it becomes canonical), **merge** (cascades existing rows that used the proposal to a target value), or **reject**. The merge action is destructive across many rows and asks for explicit confirmation.
- **Cross-reference borderlines** — borderline findings from `meridian xref sweep` land in the questions queue when the sweep is persisted. The sweep persists by default (`--commit`); pass `--dry-run` for a preview-only report.

You do not have to clear every queue at once. The "drop docs and walk away" UX means an unattended extraction will stop and *log* its questions rather than wait for an interactive answer. You collect the questions later, in batch.

### Why we keep what's excluded

Meridian's audit queue is the OUTSIDE log of the three-outcome gate. Everything the AI judged outside the deliverable definition lives there with the LLM's reasoning attached. There are two reasons this matters:

1. **The gate is fallible.** When you spot a misclassified row in the audit, you promote it back to the master register (`promote-audit`) and learn whether the gate is making a systematic mistake on this corpus.
2. **Defensibility.** A reviewer who later asks *"why isn't X on the register?"* gets a real answer with a timestamped reason — not a shrug.

The same logic applies to rejected deliverables (kept in the DB, excluded from the master and from Excel exports), to dismissed questions (kept in the audit trail), and to xref findings classified `external_reference` (informational citations to standards or docs outside this corpus, not noise).

Below-threshold items are not visible on the Excel master sheet, but they remain queryable in the SQLite and in the Legal Evidence Pack export. Nothing the AI considered is silently lost.

## Provider routing and presets

Every LLM purpose (`quality_scan`, `triage`, `extract_text_spec`, `extract_bod`, `extract_demarcation`, `conflict_pass`, `error_explain`) is independently routable to a `(provider, model)` pair. Defaults are cloud Sonnet 4.6 (Haiku 4.5 for triage). You override per project, per environment, or per call. The override hierarchy is documented in `design/PROVIDER_ROUTING_V1.md`; the operator-facing surface is the `meridian routing` command group.

Presets ship under **two parallel names** — an *operator-facing alias* that names the deployment intent, and a *technical name* that names the underlying provider/model recipe. The aliases (`cloud-default`, `hybrid`, `air-gapped`) match the vocabulary in CONTEXT.md §12 and are the names you should reach for when communicating with non-engineers ("we're running this project air-gapped"). The technical names (`cloud-sonnet-default`, `ollama-5090-balanced`, `ollama-air-gapped`, `triage-local-only`) describe what the preset actually does and are the names that get persisted into the project SQLite when you apply one. `routing apply` accepts either form and resolves the alias before writing — meaning the alias layer can be retuned (a new local model replaces qwen2.5 in `hybrid`) without renaming anything that operators have learned to type, and a project DB written under one tool version remains readable under another. Run `meridian routing list-presets` for the live mapping.

Air-gap mode is orthogonal to the preset choice — it's a per-project flag that blocks any non-local resolved route at preflight. Applying the `air-gapped` preset and turning air-gap mode on are distinct steps; the preset sets the routes, the flag enforces them. The CLI's `routing air-gap-on` command warns you if any purpose still resolves to a cloud route after the flag flips, so you don't ship into a project where preflight will reject every call.
