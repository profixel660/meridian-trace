"""Core logic for the Legal Evidence Pack.

Read-only on the project DB. Defensively introspects the live schema before
querying so missing tables degrade gracefully (a manifest note is added
rather than raising).

Output layout::

    <projects_dir>/<slug>.evidence/<UTC-timestamp>.zip

The zip contents are documented in ``meridian.evidence.__init__``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meridian import __version__ as _meridian_version
from meridian.config import settings
from meridian.db.connection import connect
from meridian.logging import get_logger
from meridian.projects import _slugify, project_db_path  # internal-but-stable

_log = get_logger("meridian.evidence")

# Bumped when the on-disk pack layout/manifest schema changes. Independent of
# the project DB schema_version.
EVIDENCE_PACK_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Secret redaction (defensive — over-redact rather than leak)
# ---------------------------------------------------------------------------

# Patterns intentionally broad. Better a false positive in an evidence pack
# than a leaked credential in a discoverable bundle. Order matters: longer
# / more-specific patterns first so they win.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Anthropic / OpenAI style API keys
    ("ANTHROPIC_OR_OPENAI_KEY", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    # Bearer tokens in Authorization headers
    ("BEARER_TOKEN", re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{8,}")),
    # AWS access key IDs
    ("AWS_ACCESS_KEY_ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    # AWS secret access key (40-char base64-ish — heuristic, broad)
    ("AWS_SECRET_ACCESS_KEY", re.compile(r"(?i)aws_secret[^\s]{0,20}[\"':=\s]+([A-Za-z0-9/+=]{40})")),
    # Generic long hex/base64 tokens labelled as a secret/key/token/password
    ("GENERIC_SECRET_FIELD", re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)"
        r"[\"':=\s]+[\"']?([A-Za-z0-9_\-/+=\.]{12,})[\"']?"
    )),
    # GitHub PAT
    ("GITHUB_TOKEN", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
)


def _redact_text(value: str) -> str:
    """Return ``value`` with obvious secrets replaced by ``[REDACTED:KIND]``."""
    if not value:
        return value
    redacted = value
    for kind, pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
    return redacted


def _redact_record(obj: Any) -> Any:
    """Recursively redact strings inside a JSON-shaped record."""
    if isinstance(obj, str):
        return _redact_text(obj)
    if isinstance(obj, dict):
        return {k: _redact_record(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_record(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    return {r[0] for r in rows}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {r[1] for r in rows}


# ---------------------------------------------------------------------------
# Tool version (read from pyproject.toml as the authoritative source)
# ---------------------------------------------------------------------------


def _tool_version() -> str:
    """Read ``[project].version`` from pyproject.toml; fall back to package version."""
    pyproject = settings.project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    return _meridian_version


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class EvidencePackResult:
    """Outcome of a single ``build_evidence_pack`` call."""

    pack_path: Path
    manifest: dict[str, Any]
    deliverable_count: int
    llm_call_count: int
    source_count: int
    incomplete_provenance: list[str] = field(default_factory=list)
    schema_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSV / JSONL helpers
# ---------------------------------------------------------------------------


def _csv_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if c is None else c for c in r])
    return buf.getvalue().encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Per-section query/build helpers (each returns CSV bytes and metadata)
# ---------------------------------------------------------------------------


def _build_deliverables_csv(
    conn: sqlite3.Connection,
    *,
    deliverable_ids: list[str] | None,
    tables: set[str],
) -> tuple[bytes, list[dict[str, Any]], list[str]]:
    """Return (csv_bytes, deliverable_rows_as_dicts, incomplete_provenance_ids)."""
    if "deliverable" not in tables:
        return _csv_bytes(["note"], [["deliverable table not present in this project"]]), [], []

    cols = _existing_columns(conn, "deliverable")
    base_cols = [
        "id", "extraction_group_id", "source_id", "chunk_id", "extraction_job_id",
        "llm_call_id", "source_ref", "trade_id", "service_id", "category_id",
        "applicable_standards", "confidence", "flags", "flag_context",
        "deliverables_summary", "gate_outcome", "status", "auto_route_decision",
        "created_at", "last_user_action_at", "last_user_action_by_question_id",
        "parent_deliverable_id", "notes",
    ]
    select_cols = [c for c in base_cols if c in cols]

    where = ""
    params: tuple[Any, ...] = ()
    if deliverable_ids:
        placeholders = ",".join("?" for _ in deliverable_ids)
        where = f"WHERE id IN ({placeholders})"
        params = tuple(deliverable_ids)

    rows = conn.execute(
        f"SELECT {', '.join(select_cols)} FROM deliverable {where} ORDER BY created_at",
        params,
    ).fetchall()

    incomplete: list[str] = []
    dict_rows: list[dict[str, Any]] = []
    csv_rows: list[list[Any]] = []
    for r in rows:
        d = {c: r[c] for c in select_cols}
        # Provenance completeness: must have source_id, llm_call_id, extraction_job_id
        has_provenance = bool(
            d.get("source_id") and d.get("llm_call_id") and d.get("extraction_job_id")
        )
        if not has_provenance:
            incomplete.append(str(d.get("id")))
            d["_provenance_status"] = "incomplete_provenance"
        else:
            d["_provenance_status"] = "complete"
        dict_rows.append(d)
        csv_rows.append([d[c] for c in select_cols] + [d["_provenance_status"]])

    headers = list(select_cols) + ["_provenance_status"]
    return _csv_bytes(headers, csv_rows), dict_rows, incomplete


def _build_audit_trail_csv(
    conn: sqlite3.Connection,
    *,
    deliverable_ids: list[str] | None,
    tables: set[str],
) -> bytes:
    """Reviewer actions + status transitions.

    The schema records reviewer state on ``deliverable`` itself (status,
    last_user_action_at, last_user_action_by_question_id) and OUTSIDE-row
    rejections in ``audit_record``. We unify both into a single trail.
    """
    rows: list[list[Any]] = []
    headers = [
        "kind", "entity_id", "source_id", "status_or_action", "actor_question_id",
        "timestamp", "details",
    ]

    if "deliverable" in tables:
        where = ""
        params: tuple[Any, ...] = ()
        if deliverable_ids:
            placeholders = ",".join("?" for _ in deliverable_ids)
            where = f"WHERE id IN ({placeholders})"
            params = tuple(deliverable_ids)
        for r in conn.execute(
            f"""
            SELECT id, source_id, status, last_user_action_at,
                   last_user_action_by_question_id, auto_route_decision,
                   created_at
            FROM deliverable {where}
            ORDER BY COALESCE(last_user_action_at, created_at)
            """,
            params,
        ).fetchall():
            # Auto-route is itself an action (the gate decision at extraction time)
            rows.append([
                "deliverable.auto_route", r["id"], r["source_id"],
                r["auto_route_decision"], None, r["created_at"],
                f"initial gate decision: {r['auto_route_decision']}",
            ])
            if r["last_user_action_at"]:
                rows.append([
                    "deliverable.user_action", r["id"], r["source_id"],
                    r["status"], r["last_user_action_by_question_id"],
                    r["last_user_action_at"],
                    f"user action set status to {r['status']}",
                ])

    if "audit_record" in tables:
        # Scope audit_record by deliverable_ids only when those rows have been
        # promoted (user_promoted_to_deliverable_id). When no scope, include all.
        where = ""
        params = ()
        if deliverable_ids:
            placeholders = ",".join("?" for _ in deliverable_ids)
            where = f"WHERE user_promoted_to_deliverable_id IN ({placeholders})"
            params = tuple(deliverable_ids)
        for r in conn.execute(
            f"""
            SELECT id, source_id, rejection_reason, landlord_response,
                   user_promoted_to_deliverable_id, created_at
            FROM audit_record {where}
            ORDER BY created_at
            """,
            params,
        ).fetchall():
            details = f"rejected: {r['rejection_reason']}"
            if r["user_promoted_to_deliverable_id"]:
                details += f" | later promoted to {r['user_promoted_to_deliverable_id']}"
            if r["landlord_response"]:
                details += f" | landlord_response: {r['landlord_response']}"
            rows.append([
                "audit_record", r["id"], r["source_id"],
                "rejected_or_promoted", None, r["created_at"], details,
            ])

    if not rows:
        rows.append(["(none)", "", "", "", "", "", "no audit-trail rows for this scope"])
    return _csv_bytes(headers, rows)


def _llm_call_ids_for_scope(
    conn: sqlite3.Connection,
    *,
    deliverable_ids: list[str] | None,
    tables: set[str],
) -> set[str] | None:
    """Return the set of llm_call IDs to include (or None for 'all')."""
    if deliverable_ids is None or "deliverable" not in tables:
        return None
    placeholders = ",".join("?" for _ in deliverable_ids)
    ids: set[str] = set()
    for r in conn.execute(
        f"SELECT DISTINCT llm_call_id FROM deliverable WHERE id IN ({placeholders})"
        " AND llm_call_id IS NOT NULL",
        tuple(deliverable_ids),
    ).fetchall():
        ids.add(r[0])
    # Also include calls that produced audit_records linked to those deliverables
    if "audit_record" in tables:
        for r in conn.execute(
            f"SELECT DISTINCT llm_call_id FROM audit_record"
            f" WHERE user_promoted_to_deliverable_id IN ({placeholders})"
            f" AND llm_call_id IS NOT NULL",
            tuple(deliverable_ids),
        ).fetchall():
            ids.add(r[0])
    return ids


def _build_llm_calls(
    conn: sqlite3.Connection,
    *,
    scoped_ids: set[str] | None,
    tables: set[str],
) -> tuple[bytes, bytes, set[str]]:
    """Return (llm_calls.csv bytes, llm_calls_full.jsonl bytes, prompt_versions used)."""
    if "llm_call" not in tables:
        empty_csv = _csv_bytes(
            ["note"], [["llm_call table not present in this project"]]
        )
        return empty_csv, b"", set()

    cols = _existing_columns(conn, "llm_call")
    headers = [
        "id", "job_id", "purpose", "provider", "model", "prompt_version_ref",
        "prompt_hash", "input_hash", "prompt_tokens", "completion_tokens",
        "cache_read_tokens", "cache_write_tokens", "cost_cents",
        "started_at", "finished_at", "error",
    ]

    where = ""
    params: tuple[Any, ...] = ()
    if scoped_ids is not None:
        if not scoped_ids:
            empty_csv = _csv_bytes(headers, [])
            return empty_csv, b"", set()
        placeholders = ",".join("?" for _ in scoped_ids)
        where = f"WHERE id IN ({placeholders})"
        params = tuple(scoped_ids)

    rows = conn.execute(
        f"SELECT * FROM llm_call {where} ORDER BY started_at",
        params,
    ).fetchall()

    csv_rows: list[list[Any]] = []
    jsonl_lines: list[str] = []
    prompt_versions: set[str] = set()

    for r in rows:
        d = dict(zip(r.keys(), r, strict=False))
        # Defensive prompt-hash (the schema doesn't store one separately;
        # derive from the prompts themselves so hand-off is reproducible)
        sys_p = d.get("system_prompt") or ""
        usr_p = d.get("user_prompt") or ""
        prompt_hash = hashlib.sha256(
            (sys_p + "\n---\n" + usr_p).encode("utf-8", errors="replace")
        ).hexdigest()

        if d.get("prompt_version_ref"):
            prompt_versions.add(str(d["prompt_version_ref"]))

        csv_rows.append([
            d.get("id"), d.get("job_id"), d.get("purpose"),
            d.get("provider"), d.get("model"), d.get("prompt_version_ref"),
            prompt_hash, d.get("input_hash"),
            d.get("prompt_tokens"), d.get("completion_tokens"),
            d.get("cache_read_tokens"), d.get("cache_write_tokens"),
            d.get("cost_cents"), d.get("started_at"), d.get("finished_at"),
            d.get("error"),
        ])

        # Full record — defensively redact secrets in every string field.
        full_record = {k: d.get(k) for k in cols}
        full_record["_derived_prompt_hash"] = prompt_hash
        full_record = _redact_record(full_record)
        jsonl_lines.append(json.dumps(full_record, default=str, sort_keys=True))

    csv_bytes = _csv_bytes(headers, csv_rows)
    jsonl_bytes = ("\n".join(jsonl_lines) + ("\n" if jsonl_lines else "")).encode("utf-8")
    return csv_bytes, jsonl_bytes, prompt_versions


def _build_sources_csv(
    conn: sqlite3.Connection,
    *,
    scope_source_ids: set[str] | None,
    tables: set[str],
) -> tuple[bytes, list[dict[str, Any]]]:
    if "source_document" not in tables:
        return _csv_bytes(
            ["note"], [["source_document table not present"]]
        ), []

    headers = [
        "id", "filename", "relative_path", "content_hash", "mime_type",
        "size_bytes", "imported_at", "document_class", "document_state",
        "revision", "is_authoritative_revision", "extraction_path",
    ]
    where = ""
    params: tuple[Any, ...] = ()
    if scope_source_ids is not None:
        if not scope_source_ids:
            return _csv_bytes(headers, []), []
        placeholders = ",".join("?" for _ in scope_source_ids)
        where = f"WHERE id IN ({placeholders})"
        params = tuple(scope_source_ids)

    rows = conn.execute(
        f"SELECT {', '.join(headers)} FROM source_document {where} ORDER BY imported_at",
        params,
    ).fetchall()
    csv_rows = [[r[c] for c in headers] for r in rows]
    dict_rows = [{c: r[c] for c in headers} for r in rows]
    return _csv_bytes(headers, csv_rows), dict_rows


# ---------------------------------------------------------------------------
# Cover letter and chain-of-custody (humans read these first)
# ---------------------------------------------------------------------------


_COVER_TEMPLATE = """\
# Meridian Legal Evidence Pack

Project: **{project}**
Generated (UTC): **{generated_at}**
Tool version: **{tool_version}**
Pack schema version: **{pack_schema_version}**

## What this pack is

This zip is a self-contained snapshot of the project's deliverables register
plus the full audit trail that produced it. It is intended to be handed to
a construction lawyer, project director, claims team, or opposing counsel
to demonstrate **how** each deliverable in the register was identified.

## What it contains

- `MANIFEST.json` — index of every file in this pack with its SHA-256 hash.
  Use `meridian evidence verify <pack.zip>` to re-check tamper status.
- `deliverables.csv` — the register snapshot.
- `audit_trail.csv` — every reviewer action and status transition (auto-routes,
  user accepts/rejects/edits, audit-record promotions).
- `llm_calls.csv` — index of every LLM call with model, prompt version, prompt
  hash, cost in cents, and timestamps.
- `llm_calls_full.jsonl` — the full LLM call records, one JSON per line, with
  defensive secret redaction (`[REDACTED:KIND]` markers).
- `prompts/` — copies of every prompt file referenced by the LLM calls.
- `sources.csv` — the source documents (filenames, content hashes, sizes,
  ingest timestamps).
- `chain_of_custody.md` — a human-readable narrative of how documents were
  ingested, extracted, and reviewed.

## What this pack does NOT prove

- It does **not** assert that any individual deliverable is contractually
  binding, technically correct, or commercially reasonable.
- The user (you / your reviewers) remain the authoritative party on every
  accepted row. The tool only assists; the audit trail captures *what the
  tool produced* and *what humans did with it*.
- LLM outputs are non-deterministic. The pack records the exact prompt
  version, prompt hash, model, and input hash so the run is reproducible
  in principle, but identical re-runs may produce slightly different
  outputs.

## How to verify

```
meridian evidence verify {pack_filename}
```

This re-computes the SHA-256 hash of every contained file and compares to
`MANIFEST.json`. Any discrepancy is reported — a clean verification means
the pack has not been altered since it was generated.

## Coverage

- Deliverables included: **{deliverable_count}**
- LLM calls referenced: **{llm_call_count}**
- Source documents referenced: **{source_count}**
- Rows flagged as `incomplete_provenance`: **{incomplete_count}**

{schema_notes_section}
"""


def _build_cover_md(
    *,
    project: str,
    generated_at: str,
    tool_version: str,
    pack_filename: str,
    deliverable_count: int,
    llm_call_count: int,
    source_count: int,
    incomplete_count: int,
    schema_notes: list[str],
) -> bytes:
    notes_section = ""
    if schema_notes:
        notes_section = (
            "## Schema notes (during generation)\n\n"
            + "\n".join(f"- {n}" for n in schema_notes)
            + "\n"
        )
    text = _COVER_TEMPLATE.format(
        project=project,
        generated_at=generated_at,
        tool_version=tool_version,
        pack_schema_version=EVIDENCE_PACK_SCHEMA_VERSION,
        pack_filename=pack_filename,
        deliverable_count=deliverable_count,
        llm_call_count=llm_call_count,
        source_count=source_count,
        incomplete_count=incomplete_count,
        schema_notes_section=notes_section,
    )
    return text.encode("utf-8")


def pack_chain_of_custody(
    conn: sqlite3.Connection,
    *,
    deliverable_ids: list[str] | None = None,
) -> str:
    """Return the chain-of-custody narrative as a Markdown string.

    Exposed as a public helper for testability and for callers who want the
    narrative without building the full zip.
    """
    tables = _existing_tables(conn)
    lines: list[str] = ["# Chain of custody", ""]

    # Sources
    if "source_document" in tables:
        rows = conn.execute(
            """
            SELECT filename, relative_path, content_hash, size_bytes,
                   imported_at, document_class, document_state, revision
            FROM source_document
            ORDER BY imported_at
            """
        ).fetchall()
        if rows:
            lines.append("## Source ingestion")
            lines.append("")
            for r in rows:
                lines.append(
                    f"- On **{r['imported_at']}**, document `{r['filename']}` "
                    f"was ingested from path `{r['relative_path']}` "
                    f"(sha256=`{r['content_hash']}`, size={r['size_bytes']} bytes, "
                    f"class={r['document_class'] or 'unknown'}, "
                    f"state={r['document_state'] or 'n/a'}, "
                    f"revision={r['revision'] or 'n/a'})."
                )
            lines.append("")

    # Extraction jobs
    if "extraction_job" in tables:
        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status, provider, model,
                   prompt_text_spec_version, prompt_bod_version
            FROM extraction_job
            ORDER BY started_at
            """
        ).fetchall()
        if rows:
            lines.append("## Extraction jobs")
            lines.append("")
            for r in rows:
                count = 0
                if "deliverable" in tables:
                    cnt_row = conn.execute(
                        "SELECT COUNT(*) FROM deliverable WHERE extraction_job_id = ?",
                        (r["id"],),
                    ).fetchone()
                    count = int(cnt_row[0]) if cnt_row else 0
                lines.append(
                    f"- Job `{r['id']}` ran on **{r['started_at']}** "
                    f"(finished {r['finished_at'] or 'n/a'}, status={r['status']}) "
                    f"using model **{r['model']}** ({r['provider']}), "
                    f"prompt versions text_spec=`{r['prompt_text_spec_version']}` "
                    f"bod=`{r['prompt_bod_version']}`. "
                    f"{count} deliverables produced."
                )
            lines.append("")

    # Reviewer actions summary
    if "deliverable" in tables:
        where = ""
        params: tuple[Any, ...] = ()
        if deliverable_ids:
            placeholders = ",".join("?" for _ in deliverable_ids)
            where = f"WHERE id IN ({placeholders})"
            params = tuple(deliverable_ids)
        status_rows = conn.execute(
            f"SELECT status, COUNT(*) AS n FROM deliverable {where} GROUP BY status",
            params,
        ).fetchall()
        action_window = conn.execute(
            f"""SELECT MIN(last_user_action_at) AS first_at,
                       MAX(last_user_action_at) AS last_at,
                       COUNT(last_user_action_at) AS n_actioned
                FROM deliverable {where}""",
            params,
        ).fetchone()
        if status_rows:
            lines.append("## Reviewer actions")
            lines.append("")
            for r in status_rows:
                lines.append(f"- {r['n']} deliverable(s) in status `{r['status']}`.")
            if action_window and action_window["n_actioned"]:
                lines.append("")
                lines.append(
                    f"Reviewer actions logged between **{action_window['first_at']}** "
                    f"and **{action_window['last_at']}** "
                    f"({action_window['n_actioned']} action(s) recorded)."
                )
            lines.append("")

    if len(lines) <= 2:
        lines.append("_No chain-of-custody data available for this project._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Manifest assembly (separated so it's testable on its own)
# ---------------------------------------------------------------------------


def pack_manifest(
    *,
    project: str,
    file_hashes: dict[str, str],
    file_sizes: dict[str, int],
    deliverable_count: int,
    llm_call_count: int,
    source_count: int,
    incomplete_provenance: list[str],
    schema_notes: list[str],
    doc_list: list[str],
    generated_at: str | None = None,
    tool_version: str | None = None,
) -> dict[str, Any]:
    """Build the MANIFEST.json payload."""
    return {
        "pack_schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "tool_version": tool_version or _tool_version(),
        "project": project,
        "generated_at": generated_at or _now_utc(),
        "deliverable_count": deliverable_count,
        "llm_call_count": llm_call_count,
        "source_count": source_count,
        "incomplete_provenance_ids": incomplete_provenance,
        "schema_notes": schema_notes,
        "documents": doc_list,
        "files": [
            {
                "name": name,
                "sha256": file_hashes[name],
                "size_bytes": file_sizes[name],
            }
            for name in sorted(file_hashes)
        ],
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def build_evidence_pack(
    project_slug: str,
    *,
    output_path: Path | None = None,
    deliverable_ids: list[str] | None = None,
) -> EvidencePackResult:
    """Build a Legal Evidence Pack zip for ``project_slug``.

    Parameters
    ----------
    project_slug
        Project name (will be slugified to find the SQLite file).
    output_path
        Destination zip path. If ``None``, defaults to
        ``<projects_dir>/<slug>.evidence/<UTC-timestamp>.zip``.
    deliverable_ids
        Optional list of deliverable IDs to scope the pack to. When provided,
        only those rows + their full provenance trail are included. When
        ``None``, the entire project is packed.
    """
    db_path = project_db_path(project_slug)
    if not db_path.exists():
        raise FileNotFoundError(f"Project not found: {db_path}")

    slug = _slugify(project_slug)
    generated_at = _now_utc()
    timestamp_for_filename = generated_at.replace(":", "").replace("-", "")

    if output_path is None:
        out_dir = settings.projects_dir / f"{slug}.evidence"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{timestamp_for_filename}.zip"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info(
        "evidence.build.start",
        project=slug,
        output=str(output_path),
        scoped=deliverable_ids is not None,
        scope_size=len(deliverable_ids) if deliverable_ids else 0,
    )

    conn = connect(db_path)
    try:
        tables = _existing_tables(conn)
        schema_notes: list[str] = []
        for required in ("deliverable", "llm_call", "source_document", "audit_record"):
            if required not in tables:
                schema_notes.append(
                    f"Table '{required}' missing from project DB — its section "
                    "will note the absence rather than be populated."
                )

        # ── Deliverables ────────────────────────────────────────────────
        deliverables_csv, deliverable_dicts, incomplete = _build_deliverables_csv(
            conn, deliverable_ids=deliverable_ids, tables=tables
        )
        deliverable_count = len(deliverable_dicts)

        # ── Audit trail ─────────────────────────────────────────────────
        audit_csv = _build_audit_trail_csv(
            conn, deliverable_ids=deliverable_ids, tables=tables
        )

        # ── Determine which LLM calls and sources are in scope ──────────
        scoped_llm_ids = _llm_call_ids_for_scope(
            conn, deliverable_ids=deliverable_ids, tables=tables
        )
        scope_source_ids: set[str] | None = None
        if deliverable_ids is not None:
            scope_source_ids = {
                d["source_id"] for d in deliverable_dicts if d.get("source_id")
            }

        # ── LLM calls (CSV index + full JSONL with redaction) ───────────
        llm_csv, llm_jsonl, prompt_versions = _build_llm_calls(
            conn, scoped_ids=scoped_llm_ids, tables=tables
        )
        # Count by counting JSONL lines (each call → one line)
        llm_call_count = (
            llm_jsonl.count(b"\n") if llm_jsonl else 0
        )

        # ── Sources ─────────────────────────────────────────────────────
        sources_csv, source_dicts = _build_sources_csv(
            conn, scope_source_ids=scope_source_ids, tables=tables
        )
        source_count = len(source_dicts)
        doc_list = [
            f"{s['filename']} (sha256={s['content_hash']})" for s in source_dicts
        ]

        # ── Chain of custody narrative ──────────────────────────────────
        custody_md = pack_chain_of_custody(
            conn, deliverable_ids=deliverable_ids
        ).encode("utf-8")

        # ── Cover letter ────────────────────────────────────────────────
        tool_version = _tool_version()
        cover_md = _build_cover_md(
            project=project_slug,
            generated_at=generated_at,
            tool_version=tool_version,
            pack_filename=output_path.name,
            deliverable_count=deliverable_count,
            llm_call_count=llm_call_count,
            source_count=source_count,
            incomplete_count=len(incomplete),
            schema_notes=schema_notes,
        )

        # ── Resolve referenced prompt files ─────────────────────────────
        # Versioning convention diverges: DB stores `prompt_version_ref` as
        # `<purpose>_v<n>.<n>` (e.g. `text_spec_v1.1`, `bod_v1.0`) while the
        # files on disk are named `PROMPT_V1_<purpose>.md` (e.g.
        # `PROMPT_V1_text_spec.md`, `PROMPT_V1_bod_import.md`). We resolve by
        # splitting the version on `_v` and matching the purpose-prefix as a
        # case-insensitive substring of the filename, with a small alias map
        # for the cases where the on-disk file uses a longer name (e.g.
        # `bod_import.md` for the `bod` purpose).
        prompts_to_include: dict[str, bytes] = {}
        prompts_dir = settings.prompts_path
        # Aliases: DB-side purpose → list of filename substrings to accept.
        _purpose_aliases: dict[str, list[str]] = {
            "bod": ["bod_import", "bod"],
        }
        if prompts_dir.exists():
            all_prompt_files = list(prompts_dir.glob("*.md"))
            for version in prompt_versions:
                purpose = version.split("_v", 1)[0] if "_v" in version else version
                purpose_lc = purpose.lower()
                accept_substrings = _purpose_aliases.get(purpose_lc, [purpose_lc])
                matched = False
                for f in all_prompt_files:
                    fname_lc = f.name.lower()
                    text_head_lc = ""
                    if any(sub in fname_lc for sub in accept_substrings):
                        prompts_to_include[f.name] = f.read_bytes()
                        matched = True
                        continue
                    # Last resort: exact version string in file header.
                    try:
                        text_head_lc = f.read_text(encoding="utf-8")[:2048].lower()
                    except OSError:
                        continue
                    if version.lower() in text_head_lc:
                        prompts_to_include[f.name] = f.read_bytes()
                        matched = True
                if not matched:
                    schema_notes.append(
                        f"Prompt version '{version}' was referenced by an LLM call "
                        f"but no matching file was found under prompts/."
                    )
        else:
            schema_notes.append(
                f"Prompts directory not found at {prompts_dir}; prompts/ section omitted."
            )

        # ── Assemble file map (name → bytes) ────────────────────────────
        files: dict[str, bytes] = {
            "deliverables.csv": deliverables_csv,
            "audit_trail.csv": audit_csv,
            "llm_calls.csv": llm_csv,
            "sources.csv": sources_csv,
            "chain_of_custody.md": custody_md,
            "cover.md": cover_md,
        }
        if llm_jsonl:
            files["llm_calls_full.jsonl"] = llm_jsonl
        for fname, fdata in prompts_to_include.items():
            files[f"prompts/{fname}"] = fdata

        # Hash + size every file before writing
        file_hashes = {name: _sha256(data) for name, data in files.items()}
        file_sizes = {name: len(data) for name, data in files.items()}

        manifest = pack_manifest(
            project=project_slug,
            file_hashes=file_hashes,
            file_sizes=file_sizes,
            deliverable_count=deliverable_count,
            llm_call_count=llm_call_count,
            source_count=source_count,
            incomplete_provenance=incomplete,
            schema_notes=schema_notes,
            doc_list=doc_list,
            generated_at=generated_at,
            tool_version=tool_version,
        )
        manifest_bytes = json.dumps(
            manifest, indent=2, sort_keys=True, default=str
        ).encode("utf-8")

        # ── Write the zip ───────────────────────────────────────────────
        with zipfile.ZipFile(
            output_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            zf.writestr("MANIFEST.json", manifest_bytes)
            for name, data in files.items():
                zf.writestr(name, data)

        _log.info(
            "evidence.build.done",
            project=slug,
            output=str(output_path),
            deliverables=deliverable_count,
            llm_calls=llm_call_count,
            sources=source_count,
            incomplete=len(incomplete),
            schema_notes=len(schema_notes),
        )

        return EvidencePackResult(
            pack_path=output_path,
            manifest=manifest,
            deliverable_count=deliverable_count,
            llm_call_count=llm_call_count,
            source_count=source_count,
            incomplete_provenance=incomplete,
            schema_notes=schema_notes,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    pack_path: Path
    ok: bool
    manifest: dict[str, Any]
    file_results: list[dict[str, Any]] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)


def verify_pack(pack_path: Path) -> VerifyResult:
    """Re-compute hashes in a pack against MANIFEST.json. Reports tamper status."""
    if not pack_path.exists():
        raise FileNotFoundError(f"Pack not found: {pack_path}")

    with zipfile.ZipFile(pack_path, "r") as zf:
        try:
            manifest_raw = zf.read("MANIFEST.json")
        except KeyError as exc:
            raise ValueError(
                f"Pack {pack_path} is missing MANIFEST.json — not a Meridian evidence pack."
            ) from exc
        manifest = json.loads(manifest_raw)

        expected = {entry["name"]: entry["sha256"] for entry in manifest.get("files", [])}
        present = set(zf.namelist()) - {"MANIFEST.json"}

        missing = sorted(set(expected) - present)
        extra = sorted(present - set(expected))
        results: list[dict[str, Any]] = []
        all_ok = not missing and not extra

        for name, expected_hash in sorted(expected.items()):
            if name not in present:
                continue
            actual_hash = _sha256(zf.read(name))
            ok = actual_hash == expected_hash
            if not ok:
                all_ok = False
            results.append({
                "name": name,
                "expected": expected_hash,
                "actual": actual_hash,
                "ok": ok,
            })

    return VerifyResult(
        pack_path=pack_path,
        ok=all_ok,
        manifest=manifest,
        file_results=results,
        missing_files=missing,
        extra_files=extra,
    )


__all__ = [
    "EVIDENCE_PACK_SCHEMA_VERSION",
    "EvidencePackResult",
    "VerifyResult",
    "build_evidence_pack",
    "pack_chain_of_custody",
    "pack_manifest",
    "verify_pack",
]
