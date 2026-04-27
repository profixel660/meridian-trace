"""Evidence-pack round-trip tests.

Builds a pack, verifies its hashes, and confirms the secret-redaction layer
catches a planted ``sk-...`` token in an LLM response.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from meridian.evidence.builder import build_evidence_pack, verify_pack
from meridian.extract.orchestrator import run_job_over_sources


def test_build_pack_then_verify(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
    tmp_path: Path,
) -> None:
    """Run a small extraction, build the pack, then verify every file hash
    matches MANIFEST.json. ``verify_pack(...).ok`` must be True."""
    name, _db, conn, src_id = fresh_project_with_sample_doc

    run_job_over_sources(conn, source_ids=[src_id])
    conn.close()

    output_path = tmp_path / "pack.zip"
    result = build_evidence_pack(name, output_path=output_path)

    assert output_path.exists()
    assert result.deliverable_count >= 1
    assert result.llm_call_count >= 1

    verify = verify_pack(output_path)
    assert verify.ok, (
        f"verify_pack failed: missing={verify.missing_files} "
        f"extra={verify.extra_files} "
        f"bad={[r['name'] for r in verify.file_results if not r['ok']]}"
    )
    # Every file recorded in MANIFEST should have been hash-checked.
    for entry in verify.file_results:
        assert entry["ok"], f"hash mismatch for {entry['name']}"


def test_pack_redacts_known_secret_patterns(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
    tmp_path: Path,
) -> None:
    """Inject a fake ``sk-test-fakekey-xxxxxxxxxxxxxxxxxxxx`` into the LLM
    response_text, build a pack, then assert the literal secret is NOT
    present in ``llm_calls_full.jsonl`` and that the redaction marker
    appears in its place.
    """
    name, _db, conn, src_id = fresh_project_with_sample_doc

    fake_secret = "sk-test-fakekey-xxxxxxxxxxxxxxxxxxxx"
    # Append the secret to the response_text the stub will store verbatim
    # in llm_call.response_text — simulating a model that echoed a key
    # found in its prompt.
    mock_llm_client.extra_response_text = f"\nfake-token={fake_secret}\n"

    run_job_over_sources(conn, source_ids=[src_id])
    conn.close()

    # Sanity check: the raw secret IS in the DB before redaction (otherwise
    # the test would pass trivially).
    raw_db = sqlite3.connect(_db)
    try:
        n_raw = raw_db.execute(
            "SELECT COUNT(*) FROM llm_call WHERE response_text LIKE ?",
            (f"%{fake_secret}%",),
        ).fetchone()[0]
        assert n_raw >= 1, "stub failed to inject the secret into llm_call"
    finally:
        raw_db.close()

    output_path = tmp_path / "secret-pack.zip"
    build_evidence_pack(name, output_path=output_path)

    with zipfile.ZipFile(output_path) as zf:
        assert "llm_calls_full.jsonl" in zf.namelist(), (
            "evidence pack missing the LLM-call JSONL section"
        )
        jsonl_bytes = zf.read("llm_calls_full.jsonl")

    text = jsonl_bytes.decode("utf-8")
    assert fake_secret not in text, (
        "secret leaked unredacted into evidence pack — "
        "_redact_record() did not run on llm_calls_full.jsonl"
    )
    assert "[REDACTED:" in text, (
        "expected a [REDACTED:KIND] marker in llm_calls_full.jsonl"
    )

    # Each JSONL line should be valid JSON (the redaction must not corrupt
    # the structure).
    for line in text.splitlines():
        if not line.strip():
            continue
        json.loads(line)
