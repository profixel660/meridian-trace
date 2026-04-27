# Meridian [TBD] — Build Context (v2, hardened)

> **Status:** End of discovery / scoping phase. No code written. No sample documents yet ingested.
> **Lineage:** Revised after a structured review pass. Original discovery artefacts archived under `archive/2026-04-26-discovery/`.
> **Purpose of this document:** the starting brief for implementation. Not a low-level technical specification — that comes next.

---

## 0. Flexibility Principle (read first)

Sample project documents (to be provided) will inform — but must not constrain — the tool's design. Real-world construction documents vary widely across project types, organisations, and regions. The tool must be built so that:

- **Trade, service, and category taxonomies are data, not code** — extensible per project, with new entries added through user-approval flow.
- **Extraction prompts are versioned data**, updateable without redeployment.
- **Model selection, provider, output structure, and most policies are configurable.**
- **Architecture decisions favour adapting to new document types over optimising for known ones.**
- **Output format is decoupled from working data.** Excel is one render target among possible future ones (CSV, JSON, API push). The SQLite store is the single source of truth.
- **Pipeline stages are not assumed.** Today's path is OCR → LLM for some inputs; vision-capable LLMs may go direct on others. Don't entrench a fixed sequence.

What we design today must accommodate what we have not yet seen. Hard-coding to today's sample set is the failure mode to avoid.

---

## 1. Purpose

Build a repeatable tool that ingests heterogeneous project documents (Revizto exports, PDFs incl. drawings, DWG, Excel, text, emails) and produces a per-trade, per-service deliverables register as a structured Excel workbook. Replaces slow, error-prone manual extraction.

## 2. Target users

Construction-sector project managers — non-technical. Tool must feel polished and approachable, not engineering-heavy. Onboarding doubles as user education on AI and data handling.

**Tool/user responsibility boundary:** the tool extracts, classifies, and surfaces uncertainty. The user is responsible for accepting, editing, or rejecting items in the review queue and for vetting the final register before downstream use. The tool's job is to make that vetting tractable; it is not authoritative.

---

## 3. Definition of "deliverable" (locked)

> **A deliverable is a system, component of a system, building component, or other physical or logical item that forms part of the completed structure or its operational services.**

### Unifying rule (governs all carve-outs below)

Deliverables include:

1. Items that form part of the **completed building** (permanent works); **or**
2. **Physical works required to realise the building** (including temporary works such as scaffolding, hoardings, propping); **or**
3. **Documentation that has continuing operational value** to the building or its operator after handover (including in-force warranty documentation).

The strict definition above governs (1). The temporary-works and documentation carve-outs below are particular applications of (2) and (3) — not exceptions to a strict rule, but specifications under a single unified rule.

### Inclusion test

The governing test for whether a documentary or non-physical artefact is a deliverable:

> **Does this artefact have continuing value to the operating building (or its operator) after handover, including through any in-force warranty period?**

If yes → deliverable. If no → exclusion.

### Documentation deliverables (included)

Documents that themselves form part of the operational life of the building are deliverables. These include:

- O&M (Operating & Maintenance) manuals
- BIM models (e.g. LOD300+) handed over with the building
- Type III Environmental Product Declarations (EPDs)
- As-installed / as-built documentation
- Functional description specifications
- Shop drawings and approved technical submittals (the final approved versions, not the in-flight workflow artefacts)
- **Warranty documentation** — included for the duration it remains in force; carries continuing operational value during the warranty period.

### Explicit exclusions

The following are NOT deliverables for the purposes of this register and must not be extracted:

- **RFIs** (Requests for Information)
- **Submittals workflow artefacts** (the request/review correspondence — the final approved technical submittal IS a deliverable per above)
- **Programmes / schedules** (project programme, construction schedule)
- **ITPs** (Inspection & Test Plans — process, not product)
- **Process reports** (RFI register, weekly status, meeting minutes, progress reports)
- **Meeting actions / minutes**
- **General coordination tasks**

These are real and important project artefacts, but they live in their own dedicated registers and tools. The deliverables register's purpose is what gets built into the building — it must not become a single-source-of-truth dumping ground for everything project-related.

### Builders-works carve-out

Builders works are included where they result in physical modifications or components that form part of the completed building (e.g. penetrations cast into concrete, structural openings, embedded fixings).

**Temporary works that enable construction (scaffolding, hoardings, propping, temporary access) are also included** even though they are removed at handover. They are physical, contractor-owned deliverables required to deliver the project, and excluding them would create gaps in builders-works tracking. This is a deliberate carve-out from the "forms part of the completed structure" rule above.

This definition (with exclusions and the builders-works carve-out) is the seed for the extraction prompt.

### Enforcement at extraction time (build-phase requirement)

The extraction prompt must apply this §3 definition (the unifying rule, the explicit exclusion list, and the builders-works carve-out) as a structured test on every candidate. The test has **three outcomes, not two** — strict enforcement is balanced against the equal risk of silently losing real deliverables that fall in genuinely ambiguous territory.

For each candidate the LLM identifies, the extraction prompt must require it to return one of:

1. **Clearly inside the definition** → enters the candidate pool; normal extraction flow.
2. **Clearly outside the definition** (matches an explicit exclusion or unambiguously fails the unifying rule) → rejected from the master register, but **logged for audit** in SQLite with the LLM's reasoning. Visible in a "Below Threshold" review queue so the user can inspect what was excluded and why. Not silently lost from existence.
3. **Borderline / unclear** (genuinely ambiguous, project-specific phrasing the global definition didn't anticipate, or definition-edge cases) → flagged `definition_borderline`, routed to the standard review queue with the LLM's reasoning. The user decides; can promote to the master register if they judge it a deliverable (tagged `user_promoted` for audit).

This preserves the gate's value — preventing drift toward random extractions or process artefacts — while preventing the equally-bad failure mode of real deliverables disappearing silently. It is fully consistent with the §9 principle: no silent auto-resolution; surface ambiguity for human decision.

**Verification check after the prompt is authored:**

> *"For every item the LLM identifies as a candidate, does the extraction prompt require it to be tested against the §3 definition with three explicit outcomes (inside / outside / borderline), with outside items logged not lost and borderline items routed to HITL — rather than relying on the LLM's general 'sense' of what looks like a deliverable?"*

If the answer is weaker than that, the prompt has not enforced the definition correctly and the register will either drift toward random extractions (weak gate) or silently lose real deliverables (binary gate).

---

## 4. Output schema

**Master sheet (Excel) columns:**

| Column | Description |
|---|---|
| `id` | Stable hidden UUID. Survives Excel editing and re-imports. Underpins future round-trip support. |
| `index` | Human-readable sequential row number (cosmetic, not stable across runs) |
| `source_document` | Filename / document title (WHICH doc) |
| `source_ref` | Location within that doc (WHERE) — structured object stored in SQLite, rendered as a readable string in Excel. Designed to support future click-to-open hyperlinking. Format varies by source type — see §7. |
| `trade` | WHO does the work. **Nullable** — process/contractual items may have no trade. |
| `service` | WHAT system / function. **Nullable** — items not tied to a building service may have no service. |
| `category` | Lightweight **secondary** axis to classify cross-cutting or non-system-specific items. Should not be relied upon as the primary classification dimension — `trade` and `service` remain the high-signal axes. **Nullable.** Default values: `design`, `procurement`, `delivery`, `builders_works` (extensible). |
| `applicable_standards` | Standards, codes, and guidelines **explicitly tied to this specific deliverable** (e.g. `AS/NZS 1680`, `ASHRAE 90.1`, `AHJ`, `GMS`). Stored as a list in SQLite, rendered as a comma-separated string in Excel. Used for compliance review and cross-deliverable consistency checks. **Scope boundary:** populate ONLY with standards the source explicitly cites against the deliverable in question — do NOT inherit broadly from the document's general standards list, foreword, or scope-of-work preamble. Inherited document-wide standards belong on the source-doc record, not on every deliverable row. **Nullable.** |
| `document_state` | Design maturity / lifecycle status of the source document this deliverable was extracted from (e.g. `30%`, `IFC`, `as-built` — see §10). Denormalised onto the deliverable row for at-a-glance visibility in Excel; canonical record lives on the source-doc record in SQLite. |
| `document_class` | Structural class of the source document (e.g. `customer_requirements`, `global_tr`, `global_ose_spec`, `project_amendment`, `drawing`, `demarcation_schedule` — see §10). Denormalised same as `document_state`. |
| `confidence` | LLM self-assessed: `high` / `medium` / `low` |
| `flags` | Structured review-reason codes (see §8). Comma-separated in the cell is acceptable here because flags are a small fixed enum, not structured cross-references. |
| `deliverables_summary` | The deliverable itself, restated as a clear, terse, present-tense noun phrase in plain English. **Negotiated-response convention:** when a deliverable carries a `negotiated_response` flag (typically from BOD-style sources), append a single visible marker (e.g. ` ⚠`) to the summary so the qualifier is visible at a glance. The full qualifier text lives in the flag context, not in the summary. This is the **hybrid (Option C)** rendering: keeps the summary terse and uniform while preventing PMs from missing the negotiated qualifier when scanning the master sheet. |

**Multi-tag handling (locked):** when a deliverable spans multiple trades or services, **use multiple rows** — one per `(deliverable × trade × service)` combination. Comma-separated values in structured columns are an antipattern.

**Null handling (intentional):** `trade`, `service`, and `category` may all be null where a deliverable cannot be meaningfully attributed to that axis. Nulls are expected behaviour, not error conditions, and must not be flagged or imputed unless the source genuinely supports a value.

**Pivot views (auto-generated as separate sheets):**
- By Trade
- By Service
- By Category

Pivots are read-only renders of the master, regenerated on every export.

**Master register receives only deliverables that have passed the human review / quarantine step** — see §9.

---

## 5. Trade vs Service (locked)

Two independent axes with an N:M relationship.

- **Trade** = WHO does the work. Labour, skillset, scope of work package, contracting, on-site responsibility.

  **Specialist trades (default values):** Electrical, Mechanical, Hydraulic, Fire, Telecommunications, DCS, Security, Carpentry, Formwork, Concrete, Steel.

  **Formwork vs Concrete (related but distinct):** Formwork is a separate trade. It installs the formwork that shapes concrete pours and is responsible for any **reinforcement and post-tensioning strands**. The Concrete trade designs the mix, supplies, and pours. A plinth (or any cast element) typically generates separate deliverable rows for each — Formwork (mould + reinforcement + PT) and Concrete (mix design + pour) — per the multi-row rule (§4).

  **Cross-cutting role:** General Contractor / Principal — for items that don't belong to a specific specialist trade or vendor (builders works, coordination outputs, head-contractor packages).

  **Equipment vendors (default values):** one entry per OSE category encountered — `Chiller Vendor`, `Generator Vendor`, `Busway Vendor`, `PDU Vendor`, `PTU Vendor`, `Fan Wall Vendor`, `HRU Vendor`, `Kiosk Transformer Vendor`, `CDU Vendor`, etc. There is **not one vendor for OSE in aggregate**; each equipment class has a distinct vendor. Add new vendor entries via the same taxonomy-approval flow when a new OSE class is encountered (see governance below).

- **Service** = WHAT system in the building, function / engineering outcome.
  Default values: Power distribution, Lighting, HVAC, Fire detection & suppression, Comms/ICT, Security/access control, Hydraulics, **DCS / Controls**.

One trade delivers many services. One service draws on many trades. Both ship as columns. PMs procure by trade; designers think by service. Pivot views give each their lens.

**Cross-axis values are allowed.** Some terms legitimately exist on **both** axes — most notably `DCS`, which is a trade (the contractor delivering distributed-control infrastructure) and also a service (the controls/monitoring system in the building). The schema supports a row where `trade=DCS` and `service=DCS / Controls` simultaneously; this is not a conflict, just the same word naming both who-does-it and what-system-it-is. Other domains may surface similar dual-axis values over time; the taxonomy governance allows a value to be approved on either axis, both, or neither.

### Attribution rule — vendor-supplied equipment (locked)

When extracting a deliverable that pertains to vendor-supplied equipment (OSE) or its accompanying documentation, apply the following attribution:

- **The equipment itself, and its native documentation** (shop drawings, O&M manuals, BIM, EPD, warranty, equipment certification, factory test results) → `trade = <Equipment-class> Vendor` (e.g. `Chiller Vendor`).
- **Connecting provisions to that equipment** (chilled water piping, power feeds, DCS interfaces, BMS integration points, drainage, etc.) → `trade = the relevant specialist trade` (Mechanical / Electrical / DCS / Hydraulic / Telecommunications / Security).
- **Each specialist trade owns its own** shop drawings, O&M, BIM, EPD, and warranty for the systems and provisions *it* delivers — not for the vendor's equipment.
- **Builders works supporting the equipment** (e.g. equipment plinths, structural openings, embedded fixings, penetrations) → `trade = Concrete / Steel / Formwork as appropriate`, `category = builders_works`. **Coordinated by GC.** The specialist trade (e.g. Mechanical) provides specifications such as weights and dimensions — and validates these *with the equipment vendor* — but does not deliver the plinth itself.

This rule corrects the naive "assign equipment docs to GC" pattern. GC owns coordination and cross-cutting items; the vendor owns its own product and product documentation.

### BOD / negotiated requirements registers — structured-import path (locked)

A **Basis of Design (BOD)** or similar customer-supplied requirements register is a tabular document where each row already represents a candidate deliverable, paired with a formal response from the project's lead party (the Landlord, Lease Provider, AirTrunk, etc.) — typically `Comply` / `Not Comply` with a clarifying comment. This is structurally different from a free-text spec and warrants a different ingestion path.

**Disposition rule (applied BEFORE the §3 gate):**

- **`Comply`** → row proceeds to §3 gate.
- **`Not Comply`** with reason `N/A` / `no [feature] requirement` / `no [feature] utilised` → **OUTSIDE** for this project. The BOD itself defines what is *out* of scope; honour those exclusions. Logged to audit with reason `out of scope for this project per Landlord Response`.
- **`Not Comply`** with substantive qualifier (e.g. *"Comply with design requirements. However..."*, *"Technically comply, clarification:..."*) → row proceeds to §3 gate but flag `negotiated_response`.
- **`Comply with conditions`** / similar → row proceeds, flag `negotiated_response`.
- Blank or missing response → flag `definition_borderline` and `unclear_language`, route to HITL.

**Trade attribution for BOD rows:**

The default for a BOD row is **the relevant specialist trade** (or vendor, where the requirement clearly implicates an OSE item) — *not* the General Contractor. Although the BOD contracts the GC/Principal as the responding party, the GC has back-to-back contracts with specialist trades for delivery; tagging the trade directly preserves operational accuracy. The GC tag is reserved for cross-cutting items genuinely owned at GC level (e.g. project programmes, head-contractor coordination packages — most of which fail the §3 gate anyway).

**Service mapping** comes from the BOD's discipline column (e.g. `DCE Mechanical Engineering` → `HVAC`; `DCE Building Automation` → `DCS / Controls`; `DCE Electrical Engineering` → `Power distribution`). Project-specific mappings are locked at first encounter via the taxonomy approval flow.

The structured-import path produces deliverable rows with the same schema as free-text extraction; the difference is upstream (parsing the table + applying the disposition rule) rather than downstream.

### Demarcation Schedules — primary reference for trade allocation (locked)

A **Demarcation Schedule** is a project document whose explicit purpose is to allocate scope items to responsible parties (Supplier / Contractor / Client / Cxa Agent / etc.) in a structured matrix. Where present in the corpus, the Demarcation Schedule is treated as the **primary reference for what is in scope and who is responsible** — primary in the sense of leading the analysis, not in the sense of silently overriding other sources. Conflicts with other sources are still surfaced for HITL per §10.

Operational implications:

- Deliverables extracted from a Demarcation Schedule populate the master register as authoritative scope+allocation rows.
- Extractions from other documents (specs, drawings, BOD) are **verified against the demarcation**: missing scope items not present in the demarcation are flagged (`scope_missing_from_demarcation`); items present in other docs but not in the demarcation are flagged (`scope_extra_to_demarcation`).
- Other documents may **add detail or context** to deliverables already listed in the demarcation (specifications, quantities, locations, applicable standards).
- Where another document's allocation conflicts with the demarcation, the conflict is flagged for HITL with both sources surfaced — the demarcation is the primary reference but not silently authoritative; see §10.
- The user must be made aware during review that a document was processed as a Demarcation Schedule (special handling), not as a standard spec.

### Builders works (locked handling)

Builders works are GC/Principal-owned items that are a consequence or dependency of trades works. They fall into two groups:

1. **Permanent physical modifications** that form part of the completed building — e.g. penetrations cut into walls or cast into concrete, structural openings, embedded fixings.
2. **Temporary works that enable construction** — e.g. scaffolding, hoardings, propping, temporary access. Included even though they are removed at handover (per the §3 carve-out).

Both groups are typically referenced *inside other trades' specs* rather than scoped on their own.

**Why "General Contractor / Principal" exists in the trade taxonomy:** it captures ownership of cross-cutting and builders-works items that do not belong to specialist trades. Without it, these items have no coherent owner in the register.

Handling:
- Default trade taxonomy includes **General Contractor / Principal**.
- Default category taxonomy includes **`builders_works`**.
- Extraction prompt instructs the LLM to recognise builders-works references inside other trades' specifications and create matching deliverables tagged `trade=General Contractor / Principal, category=builders_works`.
- **Deferred to v1.x:** explicit dependency-link modelling (e.g. "Mech ductwork item depends on GC penetration item"). For PoC, both items appear in the register independently — that is sufficient.

### Taxonomy governance (locked)

Trade, service, and category taxonomies are extensible per project, but extension must be controlled to prevent the dataset becoming noisy.

- **Canonical value enforcement:** each taxonomy maintains a single authoritative form per concept. The LLM extracts against the canonical list before considering new values.
- **Synonym merging via user confirmation:** when the LLM extracts a value that closely matches an existing canonical value (e.g. "Electrical Contractor" vs "Electrical"), the tool surfaces the near-match and asks the user to either merge into the existing canonical value or accept it as a genuinely new entry.
- **Prefer reuse over creation:** the LLM is prompted to use existing taxonomy values whenever defensible. Creation of new values requires explicit user approval (matches the active-feedback prompt model in §9).
- **Persistence of decisions:** once a taxonomy value is confirmed by the user (either accepted as new or merged from a synonym), it becomes **canonical for that project** and is preferred for all subsequent extractions in the same project. Subsequent extractions do not re-prompt for the same decision unless the user explicitly overrides via the project's taxonomy settings. This prevents taxonomy drift across long-running projects.
- **Category vocabulary is intentionally narrow** for v1 (four values). Resist aggressive expansion — if categories begin to mix lifecycle, responsibility, and activity types, that is a signal to split into multiple dimensions in a future version, not to keep adding values.

---

## 6. Tech stack (locked)

- **Backend:** Python + FastAPI.
- **Frontend:** Next.js (Plan B — multi-tool platform direction; the Meridian shell will host future modules).
- **LLM abstraction:** LiteLLM (multi-provider).
- **Storage:** **One SQLite file per project**, holding raw extracted text, normalised chunks, LLM-derived deliverables, per-job checkpoints, and the structured source-ref objects. Excel is the render target, not the working store. Per-project files give clean isolation, easy archive/share, simple backup. Cross-project queries are not on the roadmap.
- **Local processing:** PDF text extraction, OCR (tesseract), DWG conversion (ODA File Converter), email parsing.
- **Worker model:** each extraction job runs in a subprocess for crash isolation. Per-document checkpoints to SQLite enable pause/resume after laptop close, network drop, or app crash.

---

## 7. Source traceability (vital)

Every deliverable links back to source doc + location.

The reference format depends on the source type. Below is **illustrative, not exhaustive** — the structured object schema accommodates future source types and granularities:

| Source type | Reference format examples |
|---|---|
| PDF text (specifications) | page + paragraph / clause / **section** (CSI MasterFormat / NATSPEC alignment) |
| PDF drawing | sheet + bbox region or annotation ID |
| DWG | layer + view + extents |
| Excel | sheet + cell range |
| Email | thread / message + paragraph |
| Text doc | line / paragraph |

Stored structured in SQLite; rendered as readable text in Excel. The data model is designed so a future Excel cell can become a clickable hyperlink that opens the source at the right location.

---

## 8. Quality handling, flags, and confidence

Construction documents are messy human artefacts: markups, conflicting revisions, ambiguous wording, TBD placeholders, outdated references, spec/drawing inconsistencies. The tool actively surfaces uncertainty rather than silently producing low-quality outputs.

**Per-deliverable confidence** (LLM self-assessed): `high` / `medium` / `low`. Heuristic signal, not ground truth.

**Flag vocabulary (initial, extensible per project):**

| Flag code | Meaning |
|---|---|
| `unclear_language` | Source wording is ambiguous; LLM made best guess |
| `tbd_placeholder` | Source contains "TBD", "TBA", "to be advised" or similar — does not always mean "skip"; deliverable may exist with a value pending |
| `markup_present` | Drawing or text has annotations/markups overlapping the deliverable |
| `outdated_reference` | Refers to a drawing, section, or doc that's superseded or missing |
| `conflicts_with_source_X` | Contradicted by another source (cross-doc check) — see §10 for resolution |
| `drawing_unreadable` | Visual quality (scan, blur, rotation) compromised extraction |
| `revision_ambiguous` | Couldn't determine which revision of the doc to trust |
| `trade_inferred` | Trade tag inferred rather than explicit in source |
| `service_inferred` | Service tag inferred rather than explicit in source |
| `quantity_uncertain` | Quantity present but unclear or not extractable |
| `provisional_design_stage` | Source document is at an immature design stage (e.g. `30%`, `Not For Construction`); deliverable is provisional |
| `template_detected` | Source appears to be an unfilled template (placeholder content); auto-excluded from extraction |
| `responsibility_conflict` | Trade/responsibility allocation in this source disagrees with the Demarcation Schedule (or other sources) — see §5 and §10 |
| `scope_missing_from_demarcation` | Deliverable found in another source but not present in the Demarcation Schedule's scope |
| `scope_extra_to_demarcation` | Demarcation Schedule lists this scope item but no other source corroborates it |
| `definition_borderline` | Candidate is genuinely ambiguous against the §3 deliverable definition; routed to HITL with LLM reasoning so the user can promote (`user_promoted`) or reject. Prevents silent loss of real deliverables that fall in edge-case territory. |
| `negotiated_response` | Source row (typically BOD) carries a qualified compliance response (`Comply with conditions`, `Not Comply with...`, `Technically comply, clarification:...`). The qualifier text is preserved in the flag's context payload; a visible marker is appended to `deliverables_summary` per the §4 hybrid rendering convention. |
| `scope_shifted_to_nrc` | Source row (typically BOD) indicates the deliverable is in scope on the project but is delivered under the customer's fit-out NRC (Non-Recurring Charge) scope rather than under the base-build / Landlord scope. The deliverable still exists in the project; its delivery party has shifted. Almost always co-occurs with `negotiated_response`. Lets the review queue filter NRC-scope-shifted items as a group. |

**Document-level quality scan at ingestion:** per-doc LLM summary noting scan quality, revision detected, document state (design maturity), document class (see §10), markups present, illegible regions, mismatched references, **template detection** (auto-excludes from extraction). Surfaces issues before extraction starts.

**Cross-source conflict detection:** a second-pass LLM run after initial extraction compares deliverables across sources and flags contradictions for reconciliation. **No conflict is silently resolved** — see §10.

---

## 9. Human-in-the-loop & review queue

Two-stage workflow between extraction and the master register:

1. **Extraction produces candidates** — all candidates land in SQLite. None are in the master register yet.
2. **Auto-route:**
   - **High confidence + no flags → auto-approved**, lands in master register without user action.
   - **Low/medium confidence OR any flag → quarantined**, lands in "Needs Review" queue.
3. **User reviews quarantine** — for each item, side-by-side with source preview, user clicks **Accept** / **Edit** (tagged `user_edited`) / **Reject** (kept in SQLite for audit, excluded from master and Excel exports).

**HITL ambiguity prompts are batched, not interactive.** When the LLM is genuinely stuck during a run (new trade not in taxonomy, ambiguous deliverable spanning two services), it logs the question and continues. The user resolves all collected questions in one batch when ready. Preserves "drop docs and walk away" UX.

### Conflict surfacing — "most onerous" principle (locked)

For every conflict surfaced to the user (cross-source content disagreement, responsibility disagreement, revision disagreement, document-class disagreement), the LLM must:

1. **Identify both (or all) sources** in the conflict, with their full source references.
2. **Call out the "most onerous" requirement** — the version that imposes the greater obligation, stricter standard, larger quantity, tighter tolerance, or higher cost. The reasoning for which is more onerous must be stated.
3. **Surface for HITL resolution** — the user accepts one, edits a hybrid, or rejects both. No conflict is silently resolved by the tool.

**Boundary on most-onerous comparison:** if the conflicting requirements are **not directly comparable** (e.g. one is stricter on quantity while the other is stricter on quality; one constrains material while the other constrains method), the LLM must NOT rank them. Surface both with the reasoning *"requirements are not directly comparable"* and let the user decide. This prevents confidently-wrong rankings on dimensions the LLM cannot meaningfully weigh.

This applies regardless of source-type hierarchy or revision recency — those are **structural metadata to inform the user**, not rules that auto-resolve conflicts. The default human bias toward the more-onerous reading (where comparable) is the safer engineering posture and matches how PMs already manage construction risk.

**Open auxiliary decisions for build phase:**
- Should rejected items be exposed in Excel (separate "Rejected" sheet) for audit trail?
- Should the auto-approval threshold be user-adjustable per project (some projects may warrant reviewing every row)?

---

## 10. Document state, class, and authority (locked)

Three structural attributes are tracked on every source document. They inform extraction and surface conflicts to the user — but **they do not auto-resolve conflicts**. All disagreements between sources are flagged for HITL with the most-onerous requirement called out (per §9).

### 10.1 Revision

When multiple revisions of a document exist:

1. **Default:** latest revision wins, with the tool showing *how* it decided (filename pattern, embedded metadata, content scan).
2. **HITL trigger:** when status flags and recency disagree (e.g. older "Issued for Construction" rev exists alongside a newer "Draft" or "For Review" — the older IFC often outranks). Tool pauses and asks.
3. **Project-level override:** user can pin authoritative revisions if auto-detection is consistently wrong for their org's conventions.

### 10.2 Document state (design maturity)

Every source document carries an explicit `document_state` reflecting how mature its content is. This is **distinct from revision** — revision = which version, state = how authoritative is the content of this version.

**Default vocabulary (extensible per project):**

| State | Meaning |
|---|---|
| `concept` | Concept / pre-design |
| `30%` | 30% Design Development |
| `50%` | 50% Design Development |
| `90%` | 90% Design Development |
| `100%` | 100% Design (pre-IFC) |
| `IFC` | Issued For Construction |
| `as-built` | As-built / as-installed |

Any deliverable extracted from a source where state is below `IFC` is flagged `provisional_design_stage` and must be reviewed before entering the master register.

**Conflict handling:** when sources of different maturity disagree, the conflict is flagged with both documents identified and the most-onerous reading called out. The state hierarchy informs but does not override.

### 10.3 Document class

Every source document is classified into a structural class that reflects its role in the project's authority chain:

**Default classes:**

| Class | Examples |
|---|---|
| `customer_requirements` | BOD, OPR, customer Functional Requirements Document |
| `global_tr` | Owner's global Technical Requirements (e.g. AT-GLOBAL-TR-*) |
| `global_ose_spec` | Owner's global Owner-Supplied Equipment specs |
| `project_amendment` | Project-specific amendments to global specs |
| `project_clarification` | Project-specific clarifications to global specs |
| `drawing` | Project drawings (architectural, mechanical, electrical, etc.) |
| `demarcation_schedule` | Responsibility / scope demarcation matrices (see §5) |
| `methodology` | Methodology and framework documents (typically excluded from extraction) |
| `template` | Unfilled templates (auto-excluded — see §8) |

**Conflict handling:** the class hierarchy (project-specific > global, amendment > base spec) is **structural metadata**, not an auto-resolution rule. When a project amendment disagrees with the global OSE spec it modifies, the conflict is flagged with both sources identified and the most-onerous reading called out. The user resolves.

---

## 11. Granularity (locked)

When a source describes collective items ("100 type-A light fittings"):

- **Default:** **one row per collective item**, where the items are for a single trade or service. The deliverable summary captures the quantity (`100× type-A light fittings, ...`).
- **Multi-row exception:** when the same collective item spans multiple trades or services, it occupies multiple rows per the multi-tag rule (§4).

This default is overrideable per project as configurability matures; not a v1 setting.

---

## 12. LLM providers (locked, narrowed)

BYO API key model — organisations have AI vendor affiliations, so a centralised proxy is unviable.

**v1 supported providers:**
- **Anthropic Claude** (preferred — Sonnet 4.6 default, Opus 4.7 for hardest cases)
- **OpenAI** (GPT-4o family)
- **Local via Ollama / OpenAI-compatible local servers** — supported via the LiteLLM provider seam. v1 ships per-purpose routing (see below) so users can selectively run cheap-and-volume-heavy purposes (especially `triage`) on a local model while keeping the load-bearing extraction + conflict-pass calls on a frontier cloud model.

**Deferred to v1.x:** Google Gemini 2.x, Azure OpenAI, AWS Bedrock. Architecture (LiteLLM) supports these; v1 simply doesn't ship UI/test coverage for them, to keep the PoC support surface narrow.

**Excluded:** Mistral and others without strong vision capability.

### Per-purpose provider routing (locked)

Each LLM purpose (`quality_scan`, `triage`, `extract_text_spec`, `extract_bod`, `extract_demarcation`, `conflict_pass`, `error_explain`) is **independently routable** to a `(provider, model)` pair. Defaults preserve current cloud behaviour; users may override per project, per environment, or per call. See `design/PROVIDER_ROUTING_V1.md` for the complete shape.

Three named recipes ship with v1:
- **Cloud-default** (no override) — every purpose on Anthropic Sonnet 4.6 (Haiku 4.5 for `triage`).
- **Hybrid local+cloud** — `triage` and the lower-stakes purposes routed to a local Ollama model; `extract_text_spec` and `conflict_pass` (the load-bearing calls) stay on cloud Sonnet.
- **Air-gapped** — every purpose on local; cloud routes are blocked at preflight.

This is the design seam through which the tool meets the air-gapped / data-sovereign / cost-control needs of AEC projects without compromising the headline quality of the cloud path. A regression to any of: hard-coded provider per purpose, single global provider lock, or implicit cloud assumption that fails silently in air-gap mode — is a regression against this section and CONTEXT.md §0.

---

## 13. Token / cost controls (locked)

- **Pre-run cost preview** shown before each extraction so users see expected spend.
- **Cost-reduction stack:**
  - Prompt caching (Anthropic native) for repeated prompt + project context.
  - Haiku-tier triage pass identifies which doc sections likely contain deliverables; Sonnet only processes flagged sections.
  - Content-hash dedup across inputs (same drawing in three PDFs → processed once).
  - Skip re-processing of unchanged sources on re-run.
  - Compact intermediate text representation.

---

## 14. Reproducibility (locked, broadened)

Per project record in SQLite, store the **full inference context** for each LLM call so an old project re-runs deterministically:

- LLM model + version
- Extraction prompt + version
- Temperature, top_p, max_tokens
- System prompt
- Input hash (so you can detect if source content changed)
- Provider API version

Future model upgrades or prompt edits do not silently change historical outputs.

---

## 15. Excel role (locked)

**v1: export-only.** Excel is regenerated from SQLite on demand. User edits to the Excel do **not** survive re-runs.

**Round-trip is not in v1**, but the schema supports it. Stable UUID `id` column is baked into every row from day one. When round-trip is added in a future version, the import flow matches by `id` to update existing records — no schema migration required.

UI must clearly communicate that Excel is a render target, not the working data; edits should be made in the app.

---

## 16. Auth (locked)

- Single-user **TOTP**, self-enrolled at first launch (`pyotp` + `qrcode`).
- One-time recovery codes shown at enrolment.
- Self-contained — no coordination with T-Bionic required.

## 17. Licensing (locked)

- Flow: install → app shows machine fingerprint → user emails T-Bionic support → Peter issues signed key (Ed25519, private key held by Peter, public key embedded in app) → user pastes key → app validates → TOTP enrolment.
- **Term: 6 months.** Then **8-week grace** with escalating reminders, then **read-only lockout** (open + re-export of prior Excels permitted; no new processing).
- Renewal wording must NOT imply replacement keys are free (preserves commercialisation option).
- License log: SQLite file in Peter's OneDrive, written by a single-writer CLI on Peter's machine.
- No revocation endpoint — expiry is the kill switch.
- **Tamper detection** (signature check, encrypted last-seen timestamp, file integrity hash, HMAC on SQLite state, fingerprint binding) → immediate read-only lockout. Recovery path = re-enrol via fresh key (don't permanently brick legitimate users e.g. after motherboard swap).

## 18. Distribution & updates (locked)

- **Installer:** generic, NOT one-shot. License gate is the real control. Distributed via email/download link.
- No install-time phone-home — would fail behind corporate firewalls / proxies (real concern for construction-sector IT).
- **Updates:** in-app auto-update with "skip this version" option. Endpoint = JSON file on CDN. License survives updates.

## 19. Crash & error handling (locked)

- App shell stays bulletproof; sub-module failures contained to worker process.
- Local structured logging always on.
- **LLM-assisted error explanation:** stack trace + redacted context → user's LLM produces plain-English summary + suggested workaround.
- **Opt-in crash reporting:** the same LLM-generated report is shown to the user for approval before send. Endpoint = small serverless function.

---

## 20. Onboarding (locked)

Three screens after license activation, before TOTP enrolment:

1. **Why frontier AI is required** — drawings + cross-doc reasoning need vision capability that cannot run on a PM laptop.
2. **How your data is handled** — honest framing: document content goes to the chosen provider; nothing is sent to T-Bionic beyond license activation; provider's policies apply (links provided).
3. **Recommended setup** — Anthropic preferred, alternatives if your org mandates otherwise.

Plus:
- **Permanent Help → Data & AI page** accessible anytime.
- **Downloadable one-page PDF** for the PM to hand to IT / compliance.
- **Disclaimer on load** during the dev-tool / preview phase. Disclaimer text versioned in code so it can soften at v1.0.

**Recommended hardware:** Windows 10/11 or macOS 12+, 8 GB RAM (16 GB comfortable), 5–10 GB disk, stable internet. Headline message: the heavy AI work happens in the cloud, not on the laptop.

---

## 21. UX philosophy

The product serves non-technical users but contains genuinely configurable internals (taxonomies, prompts, providers, granularity). Tension is resolved by **sensible defaults exposed simply, with an "Advanced" panel for configurability that doesn't intrude on the default flow.** Power users find what they need; PMs are not asked to understand prompt versioning to use the tool.

### Discoverability is a hard requirement (locked)

The most accumulating risk for this tool is *feature-rich-but-undrivable* — a UI a non-technical PM opens once and abandons because the feature surface (deliverables / quarantine / audit / conflicts / questions / taxonomy / routing / coverage / cost / analytics / etc.) outpaces the affordances for understanding it. Every UI deliverable must therefore bake in:

- **Hover tooltips** on every non-self-explanatory element (flag codes, status badges, confidence scores, taxonomy categories, gate outcomes, "most onerous" reasoning, ⚠ markers). Tooltips explain in plain PM-friendly language.
- **Inline glossary links** for domain terms (deliverable, audit, conflict, demarcation schedule, OSE, BOD).
- **Empty states are tutorials** — "No quarantined items" must also explain what quarantine is and what action lands an item there. Never bare "no data".
- **First-use guidance per screen** — a dismissible "what am I looking at?" callout the first time the user hits each screen.
- **Action affordances are visible** — buttons, hotkeys, drag-and-drop, right-click menus all have visible cues.
- **Confirmations before destructive or hard-to-reverse actions** — reject deliverable, merge taxonomy (cascades many rows!), force re-extract (orphans reviewer state). Always show what will happen.
- **Undo where reasonably possible** — single-deliverable accept/reject/edit must be reversible from a recent-actions tray.
- **Error messages tell the user what to do next** — not just what went wrong (e.g. "Anthropic credit balance too low → top up at console.anthropic.com → Plans & Billing").
- **Keyboard shortcuts with discoverability** — `?` shows the per-screen shortcut sheet.
- **Status / progress visible** — long-running extractions show per-source progress; the user is never staring at a frozen screen.

These are **not deferred polish**. Treat skipped UX as a regression against this section. Every UI subagent brief from now on must include an explicit "UX discoverability requirements" section enumerating which of the above apply to the screens being built; every UI deliverable's verification step must include a UX checklist.

---

## 22. Branding & naming

Dark-themed web app with T-Bionic branding. Logo to be provided; design tokens (palette, typography, spacing) to be derived from it.

Working under "Meridian" parent brand. Candidate sub-product names: **Meridian Trace** (favoured for source-traceability angle), Meridian Register, Meridian Atlas, Meridian Compass. Final name TBD.

---

## 23. Remaining open items (for build-phase / next pass)

These are the items that did not get fully resolved during the discovery + sample-walkthrough passes and need attention before or during implementation.

1. **Final product name** from the Meridian family.
2. **Brand kit / starter logo.**
3. **Multi-document context strategy** — when the LLM needs to interpret doc A in the context of doc B (drawing references spec section, OSE spec references global TR), how is that managed in the prompt pipeline? Sample walkthrough confirmed this is critical, not optional. Needs concrete design during solution-design phase.
4. **Per-project bootstrap mechanism** — proposed during walkthrough: a first-pass LLM sweep of a representative sample of a new project's corpus that proposes the project-specific document classes, taxonomies, and authority chain, then surfaces them to the user for confirmation. Effectively automates the manual sample-walkthrough process. Not yet locked; flagged for solution-design discussion.
5. **Performance expectations** — target processing time per page / per doc / per project. Sets engineering targets and shapes the user's mental model of "how long should I expect this to take?".
6. **Auto-approval threshold tuning** — fixed `high+no-flags` for v1; should the threshold be user-adjustable per project once the tool is in real use?
7. **Rejected-items audit visibility** — should rejected items appear in a separate "Rejected" sheet in Excel exports for audit?
8. **"Most onerous" determination heuristics** — the §9 conflict-surfacing principle requires the LLM to identify the more-onerous reading. Needs prompt-design guidance for ambiguous cases (e.g. when two requirements are stricter on different dimensions).
9. **v1.x analyses prioritisation** — Compliance Traceability, OSE Procurement Completeness, Trade Overlap, Quantity Reconciliation, Dependency Dangling References. Decision deferred until after v1 ships and is debugged.

---

## 24. Known future considerations

- **Procore API integration** (out of scope now).
- **Commercialisation / Pro tier** — language is being kept open for this; feature-flag seams worth designing in.
- **Multi-user / collaboration** — currently single-user only.
- **Regional taxonomy variants** — UK / AU / US trade naming differences.
- **Hyperlinked source references in Excel** — data model supports this; UX deferred.
- **Round-trip Excel editing** — schema supports this via stable `id`; flow not built in v1.
- **Explicit dependency-link modelling** between deliverables (e.g. trades item depends on GC builders works item) — deferred to v1.x.
- **Additional providers** (Gemini, Azure OpenAI, Bedrock) — LiteLLM supports them; v1 ships only Anthropic + OpenAI.
- **Output formats beyond Excel** — CSV, JSON, API push (e.g. Procore) — schema is decoupled to support these.
- **Pipeline relaxation** — vision-capable LLMs going direct on some inputs without OCR pre-pass.

---

*This document is the starting brief for implementation. Treat the Flexibility Principle as a constraint on every design decision — anything that bakes today's assumptions into the codebase rather than into configuration is a regression against this brief.*
