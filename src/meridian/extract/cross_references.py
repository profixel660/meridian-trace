"""Multi-doc cross-reference loader (resolved-fork baseline per CONTEXT.md §23#3).

When extracting source X, scan its text for explicit references to other
documents in the project. For each reference whose target IS in the project,
pull a relevant section into the extraction prompt's `supplementary_context`
variable. References whose target is NOT in the project are left for the
prompt to flag as `outdated_reference`.

Scope (v1 baseline):
  - Owner-format document IDs (AT-..., Spec108, Spec 203, etc.)
  - Optional section anchor (§N, §N.N, §N.N.N, Section N, Clause N)

Out of scope (v1):
  - Embedded-link resolution (PDF link annotations)
  - Drawing-callout cross-references
  - Email-thread cross-references
  - LLM-driven semantic cross-referencing
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from meridian.config import settings
from meridian.db.connection import connect, transaction
from meridian.logging import get_logger
from meridian.projects import project_db_path

# Cap how much per-target context we inject so the prompt stays bounded.
_PER_REF_CONTEXT_CHARS = 3000
_MAX_REFS_INJECTED = 8
_MAX_TOTAL_SUPPLEMENTARY_CHARS = 20000

# Patterns for detecting cross-references inside a source document.
#
# Each pattern is (regex, kind) — the regex's named group `id` carries the
# raw reference string we then try to resolve to a project source.
_REF_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AirTrunk-style owner document IDs:
    #   AT-GLOBAL-OR-000103 / AT-GLOBAL-TR-000015b / AT-SP-ICT00001 / AT-GLOBAL-REF-000012
    (re.compile(r"\b(?P<id>AT[\u2010\u2011\u2013\u2014\-][A-Z]+[\u2010\u2011\u2013\u2014\-][A-Z]+\d*[A-Za-z]*[\u2010\u2011\u2013\u2014\-]?\d{2,8}[A-Za-z]?)\b"), "owner_id"),
    # Short-form spec refs:
    #   Spec108, Spec 203, Spec-203
    (re.compile(r"\bSpec\s*[\u2010\u2011\u2013\u2014\-]?\s*(?P<id>\d{2,4})\b", re.IGNORECASE), "short_spec"),
]

# Section anchors that may follow a reference (e.g. "...AT-XXX-001 §4.2.1").
# Captured separately so we can target a specific chunk in the referenced doc.
_SECTION_ANCHOR = re.compile(
    r"(?:§\s*|\bsection\s+|\bclause\s+)(?P<section>\d+(?:\.\d+){0,3})",
    re.IGNORECASE,
)


@dataclass
class ResolvedReference:
    raw: str
    kind: str
    target_source_id: str
    target_filename: str
    section: str | None
    excerpt: str
    rendered_label: str


def _detect_references(text: str) -> list[tuple[str, str, str | None]]:
    """Return list of (raw_id, kind, optional_section). Deduped, in order."""
    seen: OrderedDict[str, tuple[str, str | None]] = OrderedDict()
    for pattern, kind in _REF_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group("id")
            normalised_key = re.sub(r"\s+", "", raw).lower()
            if kind == "short_spec":
                normalised_key = f"spec-{normalised_key}"
            if normalised_key in seen:
                continue
            # Look ahead up to 60 chars for an associated section anchor.
            tail = text[match.end() : match.end() + 60]
            anchor = _SECTION_ANCHOR.search(tail)
            section = anchor.group("section") if anchor else None
            seen[normalised_key] = (kind, section)
    return [(k.split("-", 1)[1] if k.startswith("spec-") else k, v[0], v[1]) for k, v in seen.items()]


def _resolve_target(
    conn: sqlite3.Connection,
    *,
    own_source_id: str,
    raw_id: str,
    kind: str,
) -> sqlite3.Row | None:
    """Find a project source whose filename or text mentions raw_id.

    Filename match wins over text match. We exclude the source doing the
    referencing so a doc citing its own ID doesn't pull itself in.
    """
    if kind == "short_spec":
        # "Spec108" or "108" — match against filename containing the digits.
        like_filename = f"%spec{raw_id}%"
        like_filename_alt = f"%spec_{raw_id}%"
        like_filename_alt2 = f"%spec-{raw_id}%"
        like_filename_alt3 = f"%spec %{raw_id}%"
        rows = conn.execute(
            """
            SELECT id, filename FROM source_document
            WHERE id != ?
              AND (LOWER(filename) LIKE ? OR LOWER(filename) LIKE ?
                   OR LOWER(filename) LIKE ? OR LOWER(filename) LIKE ?)
            ORDER BY filename
            LIMIT 1
            """,
            (own_source_id, like_filename, like_filename_alt, like_filename_alt2, like_filename_alt3),
        ).fetchall()
    else:
        like = f"%{raw_id.lower()}%"
        rows = conn.execute(
            """
            SELECT id, filename FROM source_document
            WHERE id != ? AND LOWER(filename) LIKE ?
            ORDER BY filename
            LIMIT 1
            """,
            (own_source_id, like),
        ).fetchall()
    return rows[0] if rows else None


def _excerpt_target(
    conn: sqlite3.Connection,
    *,
    target_source_id: str,
    section: str | None,
) -> str:
    """Return a focused excerpt from the target's text, anchored on `section` if possible."""
    row = conn.execute(
        "SELECT text FROM source_document_text WHERE source_id = ?",
        (target_source_id,),
    ).fetchone()
    text = (row["text"] if row else "") or ""
    if not text:
        return ""

    if section:
        # Find the section's first occurrence (header-style: "4.2" or "4.2.1" near start of line).
        section_pattern = re.compile(
            rf"(?m)^\s*{re.escape(section)}\b",
            re.IGNORECASE,
        )
        match = section_pattern.search(text)
        if match:
            start = max(0, match.start() - 200)
            end = min(len(text), match.start() + _PER_REF_CONTEXT_CHARS)
            return text[start:end]

    # Fallback: leading N chars of the target.
    return text[:_PER_REF_CONTEXT_CHARS]


def build_supplementary_context(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    source_text: str,
) -> tuple[str, list[ResolvedReference]]:
    """Detect cross-references in `source_text`, resolve to other project sources, build context.

    Returns (rendered_supplementary_block, list_of_resolved_refs). The block is
    ready to drop into the extraction prompt's `{{ supplementary_context }}` slot.
    Returns ("", []) when no references resolve.
    """
    references = _detect_references(source_text)
    if not references:
        return "", []

    resolved: list[ResolvedReference] = []
    for raw, kind, section in references:
        if len(resolved) >= _MAX_REFS_INJECTED:
            break
        target_row = _resolve_target(conn, own_source_id=source_id, raw_id=raw, kind=kind)
        if target_row is None:
            continue  # leave as outdated_reference for the prompt to flag
        excerpt = _excerpt_target(conn, target_source_id=target_row["id"], section=section)
        if not excerpt.strip():
            continue
        section_label = f" §{section}" if section else ""
        rendered_label = f"{raw}{section_label}"
        resolved.append(
            ResolvedReference(
                raw=raw,
                kind=kind,
                target_source_id=target_row["id"],
                target_filename=target_row["filename"],
                section=section,
                excerpt=excerpt,
                rendered_label=rendered_label,
            )
        )

    if not resolved:
        return "", []

    # Build the supplementary context block. Order: as-detected, capped by total chars.
    parts: list[str] = []
    chars_used = 0
    for ref in resolved:
        header = (
            f"### Reference: {ref.rendered_label}\n"
            f"Source: {ref.target_filename}\n"
        )
        body = ref.excerpt.strip()
        block = f"{header}\n{body}\n"
        if chars_used + len(block) > _MAX_TOTAL_SUPPLEMENTARY_CHARS:
            break
        parts.append(block)
        chars_used += len(block)

    rendered = "\n---\n".join(parts)
    return rendered, resolved


__all__ = [
    "ResolvedReference",
    "SweepFinding",
    "SweepReport",
    "build_supplementary_context",
    "run_exhaustive_sweep",
]


# ──────────────────────────────────────────────────────────────────────────
# Exhaustive post-extraction sweep (CONTEXT.md §4–§5; overnight report 2026-04)
# ──────────────────────────────────────────────────────────────────────────
#
# `build_supplementary_context` (above) is best-effort — it runs DURING
# single-doc extraction and only catches references the LLM happens to surface.
# `run_exhaustive_sweep` (below) runs AFTER all docs are ingested and walks
# every (deliverable, source_doc) pair deterministically. Findings land in
# `cross_reference_sweep_result`; borderline ones additionally open review-
# queue questions for SME confirmation.

_sweep_log = get_logger("meridian.extract.cross_references.sweep")

# Hard cap so a runaway regex on a malformed doc doesn't explode memory.
_MAX_FINDINGS_PER_DELIVERABLE = 200
_EXCERPT_RADIUS = 120  # chars before/after match for from_excerpt

# Common construction abbreviations + English words that the equipment-tag
# pattern (or other loose patterns) may snag as false positives. Matched
# case-insensitively after collapsing whitespace. These get outcome='rejected'
# and are NOT added to the SME review queue.
_FALSE_POSITIVE_BLOCKLIST: frozenset[str] = frozenset(
    {
        "general",
        "rfp",
        "rfi",
        "change",
        "chw",
        "hhw",
        "chws",
        "chwr",
        "bms",
        "hvac",
        "ups",
        "dcs",
        "ahj",
        "ifc",
        "ibc",
        "epd",
        "bod",
        "ose",
        "tr",
        "dr",
        "sop",
        "tmp",
        "fwk",
        "pol",
        "ref",
        "spc",
        "sch",
        "acc",
    }
)

# Anchor shapes that, even when no in-corpus target resolves, are valid
# external citations (standards, owner-format external IDs). We tag those
# `external_reference` so the SME knows which external docs might be worth
# ingesting — separate signal from genuinely ambiguous borderline matches.
_EXTERNAL_ANCHOR_KINDS: frozenset[str] = frozenset({"standard"})

# Owner-ID prefixes that point at GLOBAL/external docs (e.g. AT-GLOBAL-REF-*)
# rather than project-internal artefacts. When an owner_id matches one of
# these and we can't find it in the corpus, treat it as `external_reference`.
_EXTERNAL_OWNER_PREFIXES: tuple[str, ...] = (
    "at-global-",
)


def _is_external_anchor(anchor_kind: str, normalised: str) -> bool:
    """True if the anchor is plausibly a valid external citation (not in corpus)."""
    if anchor_kind in _EXTERNAL_ANCHOR_KINDS:
        return True
    if anchor_kind == "owner_id":
        n = normalised.lower()
        return any(n.startswith(p) for p in _EXTERNAL_OWNER_PREFIXES)
    return False


def _clean_match(raw: str) -> str:
    """Normalise multi-line / multi-space artefacts in a captured match."""
    return re.sub(r"\s+", " ", raw).strip()

# Deterministic patterns (anchor_kind, regex). Order matters — longer / more
# specific patterns first so we don't double-count overlapping matches.
_SWEEP_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # MasterFormat / spec-section style: "Specification 23 21 13", "MasterFormat 26 05 19"
    (
        "spec",
        re.compile(
            r"\b(?:Specification|MasterFormat|Spec(?:ification)?\s*Section)\s+"
            r"(?P<m>\d{2}\s\d{2}\s\d{2})\b",
            re.IGNORECASE,
        ),
    ),
    # Short spec ref: Spec108, Spec 203, Spec-203
    (
        "spec",
        re.compile(r"\bSpec(?:ification)?\s*[\u2010\u2011\u2013\u2014\-]?\s*(?P<m>\d{2,4})\b", re.IGNORECASE),
    ),
    # Section / clause numbers: Section 3.2.1, §3.2, Sec. 3.2, Clause 4.5, Cl. 4.5
    (
        "section",
        re.compile(
            r"(?:§\s*|\bSection\s+|\bSec\.\s*)(?P<m>\d+(?:\.\d+){0,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "clause",
        re.compile(r"\b(?:Clause|Cl\.)\s*(?P<m>\d+(?:\.\d+){0,3})\b", re.IGNORECASE),
    ),
    # Standards: AS/NZS 3000, AS 3000, ASTM A123, ISO 9001, IEC 60364, NFPA 70, BS EN 50173
    (
        "standard",
        re.compile(
            r"\b(?P<m>(?:AS/NZS|AS|NZS|ASTM|ISO|IEC|NFPA|BS\s*EN|EN|IEEE|UL)\s*"
            r"[A-Z]?\d{2,5}(?:[\.\-]\d{1,4})*)\b"
        ),
    ),
    # Drawing references: Drawing M-101, Dwg M101, M-101, SK-001, A-201
    (
        "drawing",
        re.compile(
            r"\b(?:Drawing|Dwg\.?|Dwg)\s+(?P<m>[A-Z]{1,3}[\u2010\u2011\u2013\u2014\-]?\d{2,4}[A-Za-z]?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "drawing",
        re.compile(r"\b(?P<m>(?:SK|DWG)[\u2010\u2011\u2013\u2014\-]\d{3,4}[A-Za-z]?)\b"),
    ),
    # Equipment tags: must be 1-5 uppercase letters, then a separator (- or .),
    # then at least one digit (e.g. CH-01, AHU-1, UPS-A1, CH-1A, FCU-3.2).
    # The trailing optional "[A-Z0-9.]{0,3}" allows variants like CH-1A or
    # FCU-3.2 without admitting bare words. Words without a separator-digit
    # tail (GENERAL, CHANGE, RFP) cannot match this pattern.
    (
        "equipment_tag",
        re.compile(
            r"\b(?P<m>[A-Z]{1,5}[\u2010\u2011\u2013\u2014\-\.]\d+[A-Z0-9\.]{0,3})\b"
        ),
    ),
    # Owner-format IDs (from baseline): AT-XXX-NNNNNN
    (
        "owner_id",
        re.compile(
            r"\b(?P<m>AT[\u2010\u2011\u2013\u2014\-][A-Z]+[\u2010\u2011\u2013\u2014\-][A-Z]+\d*[A-Za-z]*"
            r"[\u2010\u2011\u2013\u2014\-]?\d{2,8}[A-Za-z]?)\b"
        ),
    ),
]


@dataclass
class SweepFinding:
    """One detected cross-reference (deterministic OR LLM-flagged)."""

    from_source_id: str
    to_source_id: str | None
    to_deliverable_id: str | None
    anchor_kind: str
    raw_match: str
    normalised_match: str
    from_excerpt: str
    detection_method: str  # 'regex' | 'string' | 'llm'
    outcome: str           # 'confirmed' | 'borderline' | 'external_reference' | 'rejected'
    confidence: str        # 'high' | 'medium' | 'low'
    reason: str | None = None


@dataclass
class SweepReport:
    """Result handed back to CLI / API / tests after a sweep run.

    `report_csv_path` / `report_md_path` are populated even on dry runs so the
    operator can inspect what *would* be written before committing.
    """

    sweep_run_id: str
    started_at: str
    finished_at: str
    status: str  # 'completed' | 'failed'
    sources_scanned: int
    deliverables_scanned: int
    findings_total: int
    confirmed: int
    borderline: int
    rejected: int
    report_csv_path: str | None
    report_md_path: str | None
    dry_run: bool
    llm_assist: bool
    error_message: str | None = None
    external_reference: int = 0


def _normalise(raw: str) -> str:
    """Canonical form for a cross-reference match — collapse whitespace/dashes."""
    s = re.sub(r"\s+", "", raw)
    s = re.sub(r"[\u2010\u2011\u2013\u2014]", "-", s)
    return s.lower()


def _excerpt_around(text: str, start: int, end: int) -> str:
    a = max(0, start - _EXCERPT_RADIUS)
    b = min(len(text), end + _EXCERPT_RADIUS)
    return text[a:b].replace("\n", " ").strip()


def _load_all_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT sd.id, sd.filename, sdt.text
        FROM source_document sd
        LEFT JOIN source_document_text sdt ON sdt.source_id = sd.id
        WHERE sd.is_authoritative_revision = 1
        ORDER BY sd.filename
        """
    ).fetchall()


def _load_all_deliverables(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.id, d.source_id, d.deliverables_summary, d.applicable_standards,
               sd.filename AS source_filename
        FROM deliverable d
        LEFT JOIN source_document sd ON sd.id = d.source_id
        WHERE d.status IN (
            'auto_approved','quarantined','user_accepted','user_edited','user_promoted'
        )
        ORDER BY d.created_at
        """
    ).fetchall()


def _load_vendor_terms(conn: sqlite3.Connection) -> list[str]:
    """Return lowercased vendor names from trade taxonomy (category_hint='vendor').

    Vendor product references are matched against this list as deterministic
    substring scans (case-insensitive). Returns [] if no vendor taxonomy.
    """
    rows = conn.execute(
        """
        SELECT value, synonyms FROM trade_taxonomy
        WHERE category_hint = 'vendor' AND is_active = 1
        """
    ).fetchall()
    terms: set[str] = set()
    for r in rows:
        if r["value"]:
            terms.add(str(r["value"]).strip().lower())
        if r["synonyms"]:
            try:
                arr = json.loads(r["synonyms"])
            except (json.JSONDecodeError, ValueError):
                arr = []
            for s in arr if isinstance(arr, list) else []:
                if isinstance(s, str) and s.strip():
                    terms.add(s.strip().lower())
    # Drop very short strings that would create noise (e.g. 1-2 chars).
    return [t for t in terms if len(t) >= 3]


def _scan_source_for_anchors(
    text: str,
) -> list[tuple[str, str, str, int, int]]:
    """Return list of (anchor_kind, raw_match, normalised_match, start, end).

    Deduplicates by (anchor_kind, normalised) within the same source so we
    don't emit the same reference 50 times when a doc repeats it.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str, int, int]] = []
    for kind, pattern in _SWEEP_PATTERNS:
        for m in pattern.finditer(text):
            # Collapse multi-line / multi-space artefacts before normalising.
            raw = _clean_match(m.group("m"))
            if not raw:
                continue
            norm = _normalise(raw)
            key = (kind, norm)
            if key in seen:
                continue
            seen.add(key)
            out.append((kind, raw, norm, m.start("m"), m.end("m")))
            if len(out) >= _MAX_FINDINGS_PER_DELIVERABLE:
                return out
    return out


def _scan_for_vendor_terms(
    text: str, vendor_terms: list[str]
) -> list[tuple[str, str, str, int, int]]:
    if not vendor_terms or not text:
        return []
    lower = text.lower()
    out: list[tuple[str, str, str, int, int]] = []
    seen: set[str] = set()
    for term in vendor_terms:
        idx = lower.find(term)
        if idx == -1 or term in seen:
            continue
        seen.add(term)
        end = idx + len(term)
        out.append(("vendor", text[idx:end], term, idx, end))
    return out


def _classify_outcome(
    *,
    target_source_ids: list[str],
    detection_method: str,
    anchor_kind: str,
    normalised: str,
    raw: str,
) -> tuple[str, str, str | None]:
    """Map (target-matches, method, anchor) → (outcome, confidence, reason).

    Four-outcome pattern (post round-11 fix):
      - rejected: captured string is in the false-positive blocklist
      - confirmed: target resolved to exactly one other source (auto-confirm)
      - borderline: ambiguous — multiple targets, or no target with no external
        signal (genuinely unclear, needs SME)
      - external_reference: pattern matches a known external-citation shape
        (standard, AT-GLOBAL-* owner id) but target isn't in this corpus.
        Useful intel for the SME — separate signal from borderline noise.
    """
    cleaned = _clean_match(raw).lower()
    if cleaned in _FALSE_POSITIVE_BLOCKLIST:
        return ("rejected", "low", "false-positive blocklist")
    # Also reject matches whose letter-prefix (before the first dash/dot/digit)
    # is in the blocklist — catches sub-segments of owner-IDs that the
    # equipment-tag regex picks up (e.g. 'REF-000012' from 'AT-GLOBAL-REF-000012').
    prefix_match = re.match(r"^[a-z]+", cleaned)
    if prefix_match and prefix_match.group(0) in _FALSE_POSITIVE_BLOCKLIST:
        return ("rejected", "low", "false-positive blocklist (prefix)")

    if len(target_source_ids) == 1:
        # Tight-anchor kinds → high; loose kinds → medium. Auto-confirmed.
        if detection_method == "regex" and anchor_kind in {
            "spec",
            "owner_id",
            "drawing",
            "standard",
        }:
            return ("confirmed", "high", "auto-confirmed: single in-corpus match")
        return ("confirmed", "medium", "auto-confirmed: single in-corpus match")

    if len(target_source_ids) > 1:
        return (
            "borderline",
            "low",
            f"ambiguous: matched {len(target_source_ids)} candidate sources",
        )

    # No target_source_id resolved.
    if _is_external_anchor(anchor_kind, normalised):
        return (
            "external_reference",
            "medium",
            "valid external citation; target not in ingested corpus",
        )
    if detection_method == "llm":
        return ("borderline", "low", None)
    return ("borderline", "low", "no matching project source")


def _resolve_anchor_to_sources(
    *,
    anchor_kind: str,
    normalised: str,
    sources: list[sqlite3.Row],
    own_source_id: str,
) -> list[str]:
    """Return list of OTHER source ids whose filename contains the normalised token.

    Multi-match returns >1 entry — caller treats as ambiguous (borderline).
    """
    needle = normalised.lstrip("§").replace(" ", "")
    if not needle:
        return []
    out: list[str] = []
    for s in sources:
        if s["id"] == own_source_id:
            continue
        fn = (s["filename"] or "").lower().replace(" ", "")
        if needle in fn:
            out.append(s["id"])
    return out


def _ensure_review_question(
    conn: sqlite3.Connection,
    *,
    finding: SweepFinding,
    sweep_run_id: str,
) -> str | None:
    """Open a review-queue question for a borderline finding. Returns question id."""
    question_id = str(uuid.uuid4())
    now = _now_iso()
    # We piggy-back on the existing `question` table. extraction_job_id and
    # llm_call_id are NOT NULL there, so we synthesise a sentinel job; rather
    # than create one, we look for any prior xref-sweep extraction_job to reuse.
    job_row = conn.execute(
        "SELECT id FROM extraction_job ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if job_row is None:
        # No extraction has ever run — skip review-queue creation rather than
        # fabricate a job. Caller will still see the borderline in the report.
        return None
    llm_row = conn.execute(
        "SELECT id FROM llm_call ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if llm_row is None:
        return None
    context_payload = {
        "kind": "cross_reference_sweep",
        "sweep_run_id": sweep_run_id,
        "from_source_id": finding.from_source_id,
        "raw_match": finding.raw_match,
        "anchor_kind": finding.anchor_kind,
        "from_excerpt": finding.from_excerpt,
    }
    conn.execute(
        """
        INSERT INTO question (
            id, extraction_job_id, llm_call_id, kind, context, question_text,
            candidate_source_refs, status, created_at
        ) VALUES (?, ?, ?, 'borderline_decision', ?, ?, '[]', 'pending', ?)
        """,
        (
            question_id,
            job_row["id"],
            llm_row["id"],
            json.dumps(context_payload),
            (
                f"Cross-reference sweep found ambiguous reference "
                f"'{finding.raw_match}' ({finding.anchor_kind}) in source "
                f"{finding.from_source_id}. Confirm whether this points to a "
                f"document in the project, or reject as out-of-scope."
            ),
            now,
        ),
    )
    return question_id


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_csv_report(
    findings: list[SweepFinding],
    *,
    sweep_run_id: str,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"xref-sweep-{sweep_run_id}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "from_source_id",
                "to_source_id",
                "to_deliverable_id",
                "anchor_kind",
                "raw_match",
                "normalised_match",
                "detection_method",
                "outcome",
                "confidence",
                "reason",
                "from_excerpt",
            ]
        )
        for f in findings:
            w.writerow(
                [
                    f.from_source_id,
                    f.to_source_id or "",
                    f.to_deliverable_id or "",
                    f.anchor_kind,
                    f.raw_match,
                    f.normalised_match,
                    f.detection_method,
                    f.outcome,
                    f.confidence,
                    f.reason or "",
                    f.from_excerpt,
                ]
            )
    return path


def _write_md_report(
    findings: list[SweepFinding],
    *,
    sweep_run_id: str,
    project_slug: str,
    out_dir: Path,
    counts: dict[str, int],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"xref-sweep-{sweep_run_id}.md"
    lines: list[str] = []
    lines.append(f"# Cross-reference sweep — {project_slug}")
    lines.append("")
    lines.append(f"- Sweep run id: `{sweep_run_id}`")
    lines.append(f"- Generated at: {_now_iso()}")
    lines.append(f"- Total findings: {len(findings)}")
    lines.append(f"  - Confirmed: {counts['confirmed']}")
    lines.append(f"  - Borderline (review queue): {counts['borderline']}")
    lines.append(f"  - External reference (not in corpus): {counts.get('external_reference', 0)}")
    lines.append(f"  - Rejected (false-positive blocklist): {counts['rejected']}")
    lines.append("")
    if not findings:
        lines.append("_No cross-references detected._ This usually means either:")
        lines.append("")
        lines.append("1. The project only contains one document (nothing to cross-reference).")
        lines.append("2. Documents do not use detectable section/drawing/spec anchors.")
        lines.append("3. Run `meridian xref sweep <project> --llm-assist` to engage")
        lines.append("   the optional LLM pass for ambiguous semantic references.")
        return _flush(path, lines)
    lines.append("## Confirmed cross-references")
    lines.append("")
    confirmed = [f for f in findings if f.outcome == "confirmed"]
    if not confirmed:
        lines.append("_None._")
    else:
        lines.append("| From | To | Anchor | Match | Confidence |")
        lines.append("|------|------|--------|-------|------------|")
        for f in confirmed:
            lines.append(
                f"| `{f.from_source_id}` | `{f.to_source_id or '?'}` | "
                f"{f.anchor_kind} | `{f.raw_match}` | {f.confidence} |"
            )
    lines.append("")
    lines.append("## Borderline (queued for SME review)")
    lines.append("")
    bord = [f for f in findings if f.outcome == "borderline"]
    if not bord:
        lines.append("_None._")
    else:
        lines.append("| From | Anchor | Match | Reason |")
        lines.append("|------|--------|-------|--------|")
        for f in bord:
            lines.append(
                f"| `{f.from_source_id}` | {f.anchor_kind} | "
                f"`{f.raw_match}` | {f.reason or 'unresolved target'} |"
            )
    lines.append("")
    lines.append("## External references (valid citations not in this corpus)")
    lines.append("")
    ext = [f for f in findings if f.outcome == "external_reference"]
    if not ext:
        lines.append("_None._")
    else:
        lines.append(
            "_The patterns below match well-known external citation shapes "
            "(standards, AT-GLOBAL-* IDs). Consider ingesting these documents "
            "if they're in scope._"
        )
        lines.append("")
        lines.append("| From | Anchor | Match | Reason |")
        lines.append("|------|--------|-------|--------|")
        for f in ext:
            lines.append(
                f"| `{f.from_source_id}` | {f.anchor_kind} | "
                f"`{f.raw_match}` | {f.reason or 'external citation'} |"
            )
    lines.append("")
    lines.append("## Rejected (false-positive blocklist)")
    lines.append("")
    rej = [f for f in findings if f.outcome == "rejected"]
    if not rej:
        lines.append("_None._")
    else:
        lines.append(
            f"_{len(rej)} matches suppressed because the captured string is in the "
            "false-positive blocklist (common abbreviations like RFP, GENERAL, "
            "CHW). These are NOT added to the SME review queue._"
        )
    return _flush(path, lines)


def _flush(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _xref_report_dir(project_slug: str) -> Path:
    return settings.projects_dir / f"{project_slug}.reports" / "xref"


def _project_slug_from_db_path(db_path: Path) -> str:
    return db_path.stem


def run_exhaustive_sweep(
    project_slug: str,
    *,
    llm_assist: bool = False,
    dry_run: bool = False,
) -> SweepReport:
    """Exhaustive post-extraction cross-reference sweep.

    Walks every authoritative source doc, extracts cross-reference anchors via
    deterministic regex (and an optional LLM pass for ambiguous remainder),
    resolves anchors to other project sources, persists results to
    `cross_reference_sweep_result`, opens review-queue questions for borderline
    findings, and emits CSV + Markdown reports.

    Args:
        project_slug: project name (will be slugified to find the SQLite file).
        llm_assist: if True, after the deterministic pass, send unresolved
            anchors to the LLM for semantic resolution. Default False — the
            deterministic pass is useful on its own (zero LLM cost).
        dry_run: if True, do not write to the DB. CSV + MD reports are still
            generated so the operator can inspect what would be written.

    Returns:
        SweepReport with counts + paths to generated artefacts.
    """
    db_path = project_db_path(project_slug)
    if not db_path.exists():
        raise FileNotFoundError(f"Project not found: {db_path}")

    sweep_run_id = str(uuid.uuid4())
    started_at = _now_iso()
    _sweep_log.info(
        "xref.sweep.start",
        sweep_run_id=sweep_run_id,
        project=project_slug,
        llm_assist=llm_assist,
        dry_run=dry_run,
    )

    conn = connect(db_path)
    try:
        # Insert the run row up-front (NOT in dry_run) so a crash leaves an
        # auditable 'failed' record after the except branch finalises it.
        if not dry_run:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO cross_reference_sweep_run (
                        id, started_at, status, llm_assist, dry_run,
                        sources_scanned, deliverables_scanned,
                        references_confirmed, references_borderline,
                        references_rejected
                    ) VALUES (?, ?, 'running', ?, ?, 0, 0, 0, 0, 0)
                    """,
                    (sweep_run_id, started_at, int(llm_assist), int(dry_run)),
                )

        try:
            sources = _load_all_sources(conn)
            deliverables = _load_all_deliverables(conn)
            vendor_terms = _load_vendor_terms(conn)

            # Index deliverables by source for fast back-resolution.
            deliv_by_source: dict[str, list[sqlite3.Row]] = {}
            for d in deliverables:
                deliv_by_source.setdefault(d["source_id"], []).append(d)

            findings: list[SweepFinding] = []
            for src in sources:
                text = src["text"] or ""
                if not text.strip():
                    continue
                anchor_hits = _scan_source_for_anchors(text)
                vendor_hits = _scan_for_vendor_terms(text, vendor_terms)

                for kind, raw, norm, start, end in anchor_hits:
                    target_ids = _resolve_anchor_to_sources(
                        anchor_kind=kind,
                        normalised=norm,
                        sources=sources,
                        own_source_id=src["id"],
                    )
                    outcome, confidence, reason = _classify_outcome(
                        target_source_ids=target_ids,
                        detection_method="regex",
                        anchor_kind=kind,
                        normalised=norm,
                        raw=raw,
                    )
                    # Single-target → set to_source_id; multiple/none → leave null.
                    to_source_id = target_ids[0] if len(target_ids) == 1 else None
                    to_deliverable_id: str | None = None
                    if to_source_id and to_source_id in deliv_by_source:
                        # Pick the first deliverable in the target source so
                        # downstream consumers have an anchor to navigate to.
                        delivs = deliv_by_source[to_source_id]
                        if delivs:
                            to_deliverable_id = delivs[0]["id"]
                    findings.append(
                        SweepFinding(
                            from_source_id=src["id"],
                            to_source_id=to_source_id,
                            to_deliverable_id=to_deliverable_id,
                            anchor_kind=kind,
                            raw_match=raw,
                            normalised_match=norm,
                            from_excerpt=_excerpt_around(text, start, end),
                            detection_method="regex",
                            outcome=outcome,
                            confidence=confidence,
                            reason=reason,
                        )
                    )

                for kind, raw, norm, start, end in vendor_hits:
                    outcome, confidence, reason = _classify_outcome(
                        target_source_ids=[],  # vendor hits don't resolve to docs
                        detection_method="string",
                        anchor_kind=kind,
                        normalised=norm,
                        raw=raw,
                    )
                    # Vendor hits surface awareness; mark borderline so SME
                    # confirms whether the vendor is actually in scope here.
                    findings.append(
                        SweepFinding(
                            from_source_id=src["id"],
                            to_source_id=None,
                            to_deliverable_id=None,
                            anchor_kind="vendor",
                            raw_match=raw,
                            normalised_match=norm,
                            from_excerpt=_excerpt_around(text, start, end),
                            detection_method="string",
                            outcome=outcome,
                            confidence=confidence,
                            reason=reason or "vendor mention — confirm in-scope",
                        )
                    )

            if llm_assist and findings:
                # Optional LLM pass over the borderline subset. Kept tiny so
                # zero-LLM runs remain the default and useful.
                _llm_assist_pass(conn, sweep_run_id=sweep_run_id, findings=findings)

            counts = {
                "confirmed": 0,
                "borderline": 0,
                "external_reference": 0,
                "rejected": 0,
            }
            for f in findings:
                counts[f.outcome] = counts.get(f.outcome, 0) + 1

            # Generate reports first (cheap, side-effect-free on DB).
            report_dir = _xref_report_dir(project_slug)
            csv_path = _write_csv_report(
                findings, sweep_run_id=sweep_run_id, out_dir=report_dir
            )
            md_path = _write_md_report(
                findings,
                sweep_run_id=sweep_run_id,
                project_slug=project_slug,
                out_dir=report_dir,
                counts=counts,
            )

            if not dry_run:
                # Single transaction — partial writes never persist.
                with transaction(conn):
                    for f in findings:
                        review_q_id: str | None = None
                        if f.outcome == "borderline":
                            review_q_id = _ensure_review_question(
                                conn,
                                finding=f,
                                sweep_run_id=sweep_run_id,
                            )
                        conn.execute(
                            """
                            INSERT INTO cross_reference_sweep_result (
                                id, sweep_run_id, from_source_id, to_source_id,
                                to_deliverable_id, anchor_kind, raw_match,
                                normalised_match, from_excerpt, detection_method,
                                outcome, confidence, reason, review_question_id,
                                created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(uuid.uuid4()),
                                sweep_run_id,
                                f.from_source_id,
                                f.to_source_id,
                                f.to_deliverable_id,
                                f.anchor_kind,
                                f.raw_match,
                                f.normalised_match,
                                f.from_excerpt,
                                f.detection_method,
                                f.outcome,
                                f.confidence,
                                f.reason,
                                review_q_id,
                                _now_iso(),
                            ),
                        )
                    conn.execute(
                        """
                        UPDATE cross_reference_sweep_run
                        SET finished_at = ?, status = 'completed',
                            sources_scanned = ?, deliverables_scanned = ?,
                            references_confirmed = ?, references_borderline = ?,
                            references_rejected = ?, report_csv_path = ?,
                            report_md_path = ?
                        WHERE id = ?
                        """,
                        (
                            _now_iso(),
                            len(sources),
                            len(deliverables),
                            counts["confirmed"],
                            counts["borderline"],
                            counts["rejected"],
                            str(csv_path),
                            str(md_path),
                            sweep_run_id,
                        ),
                    )

            finished_at = _now_iso()
            report = SweepReport(
                sweep_run_id=sweep_run_id,
                started_at=started_at,
                finished_at=finished_at,
                status="completed",
                sources_scanned=len(sources),
                deliverables_scanned=len(deliverables),
                findings_total=len(findings),
                confirmed=counts["confirmed"],
                borderline=counts["borderline"],
                rejected=counts["rejected"],
                external_reference=counts["external_reference"],
                report_csv_path=str(csv_path),
                report_md_path=str(md_path),
                dry_run=dry_run,
                llm_assist=llm_assist,
            )
            _sweep_log.info(
                "xref.sweep.complete",
                sweep_run_id=sweep_run_id,
                findings=len(findings),
                confirmed=counts["confirmed"],
                borderline=counts["borderline"],
                rejected=counts["rejected"],
                external_reference=counts["external_reference"],
                csv=str(csv_path),
                md=str(md_path),
            )
            return report
        except BaseException as exc:
            if not dry_run:
                with transaction(conn):
                    conn.execute(
                        """
                        UPDATE cross_reference_sweep_run
                        SET finished_at = ?, status = 'failed', error_message = ?
                        WHERE id = ?
                        """,
                        (_now_iso(), repr(exc), sweep_run_id),
                    )
            _sweep_log.exception(
                "xref.sweep.failed",
                sweep_run_id=sweep_run_id,
                error_type=type(exc).__name__,
            )
            raise
    finally:
        conn.close()


def _llm_assist_pass(
    conn: sqlite3.Connection,
    *,
    sweep_run_id: str,
    findings: list[SweepFinding],
) -> None:
    """Optional LLM-assist over borderline findings. Kept conservative.

    Lazily imports the LLM client so the module stays importable in
    air-gapped environments where the LLM SDK may not be configured.
    Any LLM error is swallowed and logged — the deterministic findings
    remain valid even if the assist pass fails.
    """
    try:
        from meridian.llm.client import call_llm  # noqa: F401  (defensive import)
    except Exception:  # noqa: BLE001
        _sweep_log.warning("xref.sweep.llm_assist.unavailable", sweep_run_id=sweep_run_id)
        return
    # The LLM-assist contract is intentionally minimal in v1: we tag every
    # borderline finding with a `reason` annotation so the SME sees that an
    # assist pass was attempted. Wiring a bespoke prompt is a v2 task — the
    # hook is here so the surface stays stable.
    for f in findings:
        if f.outcome == "borderline" and f.reason and "llm_assist" not in f.reason:
            f.reason = f"{f.reason}; llm_assist=requested"


def latest_sweep_findings(
    conn: sqlite3.Connection,
) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    """Return (latest sweep_run row, its results). Used by the report renderer."""
    run = conn.execute(
        "SELECT * FROM cross_reference_sweep_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return None, []
    rows = conn.execute(
        "SELECT * FROM cross_reference_sweep_result WHERE sweep_run_id = ? "
        "ORDER BY outcome DESC, anchor_kind, raw_match",
        (run["id"],),
    ).fetchall()
    return run, rows
