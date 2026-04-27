# Quality Scan Prompt v1 — per-document ingestion-time scan

**Version:** v1.0-draft
**Authority:** `CONTEXT.md` §8 (document-level quality scan), §10 (document state, class, authority).
**Purpose:** classify a freshly-imported source document. Decide its document class, design-maturity state, scan quality, template/demarcation flags, and which extraction prompt path to use downstream.
**Output:** structured JSON consumed by the ingestion orchestrator. Sets `source_document.document_class`, `document_state`, `revision`, `is_template`, `is_demarcation_schedule`, `extraction_path`.

---

## Prompt body

```
You are reviewing a freshly-imported construction-project document for a
deliverables-extraction tool. Your job is to CLASSIFY the document and
ASSESS ITS QUALITY before any deliverable extraction begins.

You are NOT extracting deliverables in this call.

# OUTPUT — return ONE JSON object with these fields

{
  "document_class": one of:
      "customer_requirements" | "global_tr" | "global_ose_spec" |
      "project_amendment" | "project_clarification" | "drawing" |
      "demarcation_schedule" | "methodology" | "template" | "unknown",

  "document_state":
      one of "concept" | "30%" | "50%" | "90%" | "100%" | "IFC" | "as-built",
      OR null if the document is not a design-maturity-graded artefact
      (e.g. customer_requirements / global_* / methodology / template
      are revisioned, not maturity-graded — return null in those cases).

  "revision": short string identifying the document's revision if
      detectable from filename, embedded metadata, or explicit content
      (e.g. "Rev 11", "rev2", "latest", "SYD29EX2"). null if not
      detectable.

  "revision_detected_via":
      "filename_pattern" | "embedded_metadata" | "content_scan" | null.

  "scan_quality":
      "clean" — extracted text reads cleanly start to finish.
      "markups_present" — annotations / markups overlap content.
      "partially_illegible" — some sections unreadable (poor scan,
          rotation, redaction).
      "unreadable" — extraction failed; document needs OCR or manual
          intake.

  "markups_present":  true/false.

  "illegible_regions": array of locator strings (page / section / sheet)
      pointing to unreadable parts. [] if none.

  "mismatched_references": array of cross-references the document
      cites but that cannot be resolved (e.g. "AT-GLOBAL-TR-XXX §4.2"
      where the section number doesn't exist in the named doc, OR a
      reference whose target is unknown). [] if none.

  "is_template": true if the document appears to be an unfilled
      template (placeholder content, "Lorem ipsum", "[insert ...]",
      blank tables, all-CAPS sample headings without project content).
      Templates are auto-excluded from extraction.

  "is_demarcation_schedule": true if the document is structured as a
      responsibility / scope demarcation matrix (rows = scope items,
      columns = responsible parties such as Supplier / Contractor /
      Client / Cxa Agent). Demarcation schedules trigger special
      handling downstream — they are the primary reference for trade
      allocation.

  "extraction_path":
      "text_spec"   — free-text specification, drawing legend, clause-
                      style document. Use the text-spec extraction prompt.
      "bod_import"  — tabular requirements register where each row is
                      already a candidate deliverable, with a formal
                      response from a lead party. Use the BOD
                      structured-import prompt.
      "demarcation" — responsibility / scope demarcation matrix
                      (rows = scope items, columns = responsible
                      parties). Use the demarcation prompt; this is
                      the primary-reference path per CONTEXT.md §5.
                      ALWAYS pair this value with is_demarcation_schedule=true.
      "drawing"     — drawing-only document (no extractable text spec
                      content). v1 falls through to text_spec prompt
                      if marked drawing; flag for downstream review.
      "excluded"    — methodology / template / unrecognised; do not
                      run extraction.

  "summary": 1–3 sentences in plain English describing what this
      document is and any quality concerns the reviewer should know
      about before approving it for extraction.
}

# RULES

  - Be conservative with "is_template": only true if the document is
    clearly unfilled. A partially-completed template is NOT a template.
  - Be conservative with "is_demarcation_schedule": only true if the
    document's PRIMARY structure is a responsibility matrix. A spec
    that REFERENCES a separate demarcation schedule is not itself one.
  - For BOD-style customer-requirements documents (Comply / Not Comply
    rows), set document_class = "customer_requirements" AND
    extraction_path = "bod_import".
  - For OSE / equipment specifications produced by an owner,
    document_class = "global_ose_spec".
  - For owner Technical Requirement umbrella docs (e.g. AT-GLOBAL-TR-*)
    document_class = "global_tr".
  - Project-specific amendment / clarification docs to a global spec →
    "project_amendment" / "project_clarification" respectively.
  - Drawings (PDF or DWG showing geometry rather than spec text) →
    "drawing".
  - Use document_state ONLY for documents whose maturity meaningfully
    advances from concept through IFC. For customer_requirements,
    global_tr, global_ose_spec, methodology, template — return null.
    These are tracked by REVISION, not by maturity state.
  - Do NOT guess on extraction_path. If you cannot tell, return
    "text_spec" as the safer default and note the uncertainty in
    summary.

# SOURCE DOCUMENT METADATA (provided by the runtime)

filename:        {{ filename }}
mime_type:       {{ mime_type }}
size_bytes:      {{ size_bytes }}
relative_path:   {{ relative_path }}

# SOURCE TEXT (truncated to first {{ max_chars }} characters)

{{ source_text_excerpt }}
```

---

## Notes

- This prompt does not extract deliverables — it only classifies the document and decides which downstream prompt to run.
- The `document_state` rule is the resolved fork from the build chat: state is for design-maturity, not for revisioned artefacts. BOD / global specs / TR documents are revisioned, not maturity-graded.
- The `extraction_path` value is what the orchestrator uses to dispatch the document. Adding a new path here (e.g. `drawing_with_callouts`) requires implementing the corresponding extractor.
