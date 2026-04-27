"""Legal Evidence Pack — assembles project audit trail into a defensible bundle.

A Tier-3 differentiator. The pack is a single ``.zip`` a construction lawyer,
project director, or claims team can hand to opposing counsel or use in a
dispute. It contains:

- ``MANIFEST.json`` — top-level index with SHA-256 hash of every file in the
  pack, generated-at UTC, schema version, tool version.
- ``deliverables.csv`` — full register snapshot.
- ``audit_trail.csv`` — every reviewer action + status transition.
- ``llm_calls.csv`` — every LLM call with model, prompt version, prompt hash,
  cost, timestamp.
- ``llm_calls_full.jsonl`` — full LLM call records (one JSON per line) with
  defensive secret redaction.
- ``prompts/`` — copies of every prompt file referenced by the LLM calls.
- ``sources.csv`` — source-document register with paths, hashes, sizes,
  ingest timestamps.
- ``cover.md`` — human-readable cover letter for non-lawyers.
- ``chain_of_custody.md`` — auto-generated narrative.

Public API:
    >>> from meridian.evidence import build_evidence_pack
    >>> pack = build_evidence_pack("acme-tower")

CLI:   ``meridian evidence build|verify``
HTTP:  ``meridian.evidence.api.evidence_router``
"""

from __future__ import annotations

from meridian.evidence.builder import (
    EvidencePackResult,
    build_evidence_pack,
    pack_chain_of_custody,
    pack_manifest,
    verify_pack,
)

__all__ = [
    "EvidencePackResult",
    "build_evidence_pack",
    "pack_chain_of_custody",
    "pack_manifest",
    "verify_pack",
]
