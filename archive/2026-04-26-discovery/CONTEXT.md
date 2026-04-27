# Meridian [TBD] — Build Context

> **Status:** Design / scoping phase. No code written. No sample documents yet ingested. This document captures decisions made during requirements discovery and is intended as the starting brief for the implementation chat.

---

## Flexibility Principle (read first)

Sample project documents (to be provided) will inform — but must not constrain — the tool's design. Real-world construction documents vary widely across project types, organisations, and regions. The tool must be built so that:

- **Trade and service taxonomies are data, not code** — extensible per project, with new entries added through user-approval flow.
- **Extraction prompts are versioned data**, updateable without redeployment.
- **Model selection, provider, output structure, and most policies are configurable.**
- **Architecture decisions favour adapting to new document types over optimising for known ones.**

What we design today must accommodate what we have not yet seen. Hard-coding to today's sample set is the failure mode to avoid.

---

## Purpose

Build a repeatable tool that ingests heterogeneous project documents (Revizto exports, PDFs incl. drawings, DWG, Excel, text, emails) and produces a per-trade, per-service deliverables register as a structured Excel workbook. Replaces slow, error-prone manual extraction.

## Target users

Construction-sector project managers — non-technical. Tool must feel polished and approachable, not engineering-heavy. Onboarding doubles as user education on AI and data handling.

---

## Output schema

**Master sheet (Excel):**

| Column | Description |
|---|---|
| `index` | Sequential ID |
| `source_document` | Filename / document title (WHICH doc) |
| `source_ref` | Location within that doc (WHERE) — page/clause, sheet+region, cell range, email msg+paragraph etc. Stored structured in SQLite, rendered as readable string in Excel. Designed to support future click-to-open-at-location hyperlinking. |
| `trade` | WHO does the work (e.g. Electrical, Mechanical, Plumbing) |
| `service` | WHAT system / function (e.g. Power distribution, HVAC, Fire detection) |
| `category` | (Open) Lightweight third axis for items that don't fit trade/service: design / procurement / coordination / delivery |
| `confidence` | LLM self-assessed: high / medium / low |
| `flags` | Structured review reasons (see below) |
| `deliverables_summary` | The deliverable itself, in plain English |

**Pivot views (auto-generated as separate sheets):**
- By Trade
- By Service
- (Optional) By Category

Master register receives only deliverables that have passed the human review / quarantine step.

---

## Trade vs Service (locked)

Two independent axes with an N:M relationship.

- **Trade** = WHO does the work. Labour, skillset, scope of work package, contracting, on-site responsibility.
  Examples: Electrical contractor, Mechanical (HVAC) contractor, Plumbing, Fire, Data/comms, Carpentry, Concrete, Steel.
- **Service** = WHAT system in the building, function / engineering outcome.
  Examples: Power distribution, Lighting, HVAC, Fire detection & suppression, Comms/ICT, Security/access control, Hydraulics.

One trade delivers multiple services. One service draws on multiple trades. Both axes ship as columns. PMs procure by trade; designers think by service. Pivot views give each their lens.

**Open items:**
- Include the `category` axis or defer.
- When a deliverable spans multiple trades or services: comma-separated values in one row vs split into multiple rows.

---

## Tech stack

- **Backend:** Python + FastAPI.
- **Frontend:** Next.js (Plan B — multi-tool platform direction; Meridian shell will host future modules).
- **LLM abstraction:** LiteLLM (multi-provider).
- **Storage:** SQLite as intermediate / working store. Excel is the render target, not the working data.
- **Local processing:** PDF text extraction, OCR (tesseract), DWG conversion (ODA File Converter), email parsing.
- **Worker model:** each extraction job runs in a subprocess for crash isolation. Per-document checkpoints to SQLite enable pause / resume after laptop close, network drop, or app crash.

---

## LLM providers

BYO API key model — organisations have AI vendor affiliations, so a centralised proxy is unviable.

Supported v1:
- Anthropic Claude (preferred — Sonnet 4.6 default, Opus 4.7 for hardest cases)
- OpenAI (GPT-4o family)
- Google Gemini 2.x
- Azure OpenAI (procurement-friendly for Microsoft-aligned orgs)
- AWS Bedrock (procurement-friendly path to Claude)

Excluded v1: Mistral and others without strong vision capability.

---

## Token / cost controls

- **Pre-run cost preview** shown before each extraction so users see expected spend.
- Cost-reduction stack:
  - Prompt caching (Anthropic native) for repeated prompt + project context.
  - Haiku-tier triage pass identifies which doc sections likely contain deliverables; Sonnet only processes the flagged sections.
  - Content-hash dedup across inputs (same drawing in three PDFs → processed once).
  - Skip re-processing of unchanged sources on re-run.
  - Compact intermediate text representation.

---

## Reproducibility

Pin LLM model version **and** extraction prompt version per project record in SQLite. Re-running an old project uses the original model + prompt. Future model upgrades or prompt edits do not silently change historical outputs.

---

## Source traceability (vital)

Every deliverable links back to source doc + location:

| Source type | Reference format |
|---|---|
| PDF text | page + paragraph / clause |
| PDF drawing | sheet + bbox region or annotation ID |
| DWG | layer + view + extents |
| Excel | sheet + cell range |
| Email | thread / message + paragraph |
| Text doc | line / paragraph |

Stored structured in SQLite; rendered as readable text in Excel. Designed so a future Excel cell can become a clickable hyperlink that opens the source at the right location.

User is responsible for vetting every output. The tool's job is to make that vetting tractable.

---

## Human-in-the-loop & quality handling

Construction documents are messy human artefacts: markups, conflicting revisions, ambiguous wording, TBD placeholders, outdated references, spec/drawing inconsistencies. The tool actively surfaces uncertainty rather than silently producing low-quality outputs.

1. **Document quality scan at ingestion** — per-doc LLM summary: scan quality, revision detected, markups, illegible regions, mismatched references. Surfaces issues before extraction starts.
2. **Per-deliverable confidence score** (high / medium / low).
3. **Flags column** — structured review reasons: `unclear_language`, `tbd_placeholder`, `markup_present`, `outdated_reference`, `conflicts_with_source_X`, `drawing_unreadable`, etc.
4. **Quarantine workflow** — low-confidence or flagged items land in a "Needs Review" state. User must accept / edit / reject before they enter the master register. Nothing dubious silently makes it to the Excel.
5. **Cross-source conflict detection** — second-pass LLM compares deliverables across sources, flags contradictions for reconciliation.
6. **Active feedback prompts** — when genuinely stuck (new trade not in taxonomy, ambiguous deliverable), tool pauses and asks rather than guessing.

---

## Auth

- Single-user **TOTP**, self-enrolled at first launch (`pyotp` + `qrcode`).
- One-time recovery codes shown at enrolment.
- Self-contained — no coordination with Undivided Systems required.

---

## Licensing (gated)

- Flow: install → app shows machine fingerprint → user emails `support@undivided.systems` → Peter issues signed key (Ed25519, private key held by Peter, public key embedded in app) → user pastes key → app validates → TOTP enrolment.
- **Term: 6 months.** Then **8-week grace** with escalating reminders, then **read-only lockout**.
- Lockout = open + re-export of prior Excels permitted; no new processing.
- Renewal wording must NOT imply replacement keys are free (preserves commercialisation option).
- License log: SQLite file in Peter's OneDrive, written by a single-writer CLI on Peter's machine.
- No revocation endpoint — expiry is the kill switch.
- **Tamper detection** (signature check, encrypted last-seen timestamp, file integrity hash, HMAC on SQLite state, fingerprint binding) → immediate read-only lockout. Recovery path = re-enrol via fresh key (don't permanently brick legitimate users e.g. after motherboard swap).

---

## Distribution & updates

- **Installer:** generic, NOT one-shot. License gate is the real control. Distributed via email/download link.
- No install-time phone-home — would fail behind corporate firewalls / proxies (real concern for construction-sector IT environments).
- **Updates:** in-app auto-update with "skip this version" option. Endpoint = JSON file on CDN. License survives updates.

---

## Crash & error handling

- App shell stays bulletproof; sub-module failures contained to worker process.
- Local structured logging always on.
- **LLM-assisted error explanation:** stack trace + redacted context → user's LLM produces plain-English summary + suggested workaround.
- **Opt-in crash reporting:** the same LLM-generated report is shown to the user for approval before send. Endpoint = small serverless function.

---

## Onboarding

Three screens after license activation, before TOTP enrolment:

1. **Why frontier AI is required** — drawings + cross-doc reasoning need vision capability that cannot run on a PM laptop.
2. **How your data is handled** — honest framing: document content goes to the chosen provider; nothing is sent to Undivided Systems beyond license activation; provider's policies apply (links provided).
3. **Recommended setup** — Anthropic preferred, alternatives if your org mandates otherwise.

Plus:
- **Permanent Help → Data & AI page** accessible anytime.
- **Downloadable one-page PDF** for the PM to hand to IT / compliance.
- **Disclaimer on load** during the dev-tool / preview phase. Disclaimer text versioned in code so it can soften at v1.0.

**Recommended hardware:** Windows 10/11 or macOS 12+, 8 GB RAM (16 GB comfortable), 5–10 GB disk, stable internet. Headline message: the heavy AI work happens in the cloud, not on the laptop.

---

## Branding

Dark-themed web app with Undivided Systems branding. No formal brand kit yet — starter logo to be provided by Peter; design tokens (palette, typography, spacing) to be derived from it. Aim for a polished, modern, professional feel suitable for client-facing PM use.

## Naming

Working under "Meridian" parent brand (Peter's planned product family). Candidate sub-product names surfaced:
- **Meridian Trace** — leans into source-traceability story (favoured).
- **Meridian Register** — literal nod to the deliverables register output.
- **Meridian Atlas** — mapping the document landscape.
- **Meridian Compass** — navigating scattered project docs.

Final name TBD.

---

## Open items

1. Final product name from the Meridian family.
2. Brand kit / starter logo.
3. Include the `category` axis (design / procurement / coordination / delivery) or defer.
4. Multi-tag handling: comma-separated vs split-into-multiple-rows when a deliverable spans trades / services.
5. Project workspace model: single active project vs project switcher.
6. Sample documents — pending. Review will surface format-specific edge cases and likely additional requirements.

---

## Known future considerations

- **Procore API integration** (out of scope now).
- **Commercialisation / Pro tier** — language is being kept open for this; feature-flag seams worth designing in.
- **Multi-user / collaboration** — currently single-user only.
- **Regional taxonomy variants** — UK / AU / US trade naming differences.
- **Hyperlinked source references in Excel** — data model supports this; UX deferred.
- **Source documents not yet seen** — will surface additional requirements once reviewed.

---

*This document is the starting brief for implementation. Treat the Flexibility Principle as a constraint on every design decision — anything that bakes today's assumptions into the codebase rather than into configuration is a regression against this brief.*
