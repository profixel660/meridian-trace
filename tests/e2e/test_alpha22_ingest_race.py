"""Alpha-22 punch-list #1 — ingest_file race-condition handling.

Two workers ingesting the same file concurrently (the alpha-22
double-submission bug, punch-list item #4) collide on the
``source_document.content_hash`` UNIQUE constraint: the slower worker's
INSERT raises ``sqlite3.IntegrityError`` because the SELECT-then-INSERT
in :func:`ingest_file` is not atomic.

Pre-fix: the IntegrityError propagated, ``_classify_ingest_error``
labelled it ``code='unknown'``, and the post-import banner reported
"127 files failed for an unclassified reason" — the SME's 2026-05-02
testing round saw this on a 347-file folder import.

Post-fix: the loser detects the UNIQUE conflict, re-reads the row the
winner committed, and returns ``IngestResult(deduped=True)`` —
semantically "this file is already in the project."
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from meridian.db.connection import connect
from meridian.ingest import dispatcher as disp
from meridian.ingest._types import _Chunk, _Extracted


def test_concurrent_ingest_of_same_file_dedupes_via_unique_violation(
    fresh_project: tuple[str, Path, sqlite3.Connection],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slug, db_path, setup_conn = fresh_project
    setup_conn.close()  # workers open their own connections

    pdf_path = tmp_path / "race.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 alpha22 race fixture\n%%EOF\n")

    # Stub the heavy PDF extractor — this test is about race semantics,
    # not pypdf parsing. Both threads must see identical extracted
    # content for the dedupe-by-content_hash invariant to hold.
    def _fake_extract(_path: Path) -> _Extracted:
        return _Extracted(
            extraction_method="pdf_text",
            text="alpha22 race fixture body",
            chunks=[
                _Chunk(chunk_kind="pdf_page", locator={"page": 1}, text="alpha22 race fixture body"),
            ],
            metadata={},
        )

    monkeypatch.setattr(disp.pdf_ingest, "extract", _fake_extract)

    # Force both threads through the SELECT *before* either reaches the
    # transaction. After the barrier, both see an empty SELECT and race
    # the transaction lock — exactly the alpha-22 double-submission shape.
    barrier = threading.Barrier(2)
    real_hash = disp._hash_file

    def _hash_then_sync(path: Path) -> str:
        digest = real_hash(path)
        barrier.wait(timeout=5.0)
        return digest

    monkeypatch.setattr(disp, "_hash_file", _hash_then_sync)

    results: list[disp.IngestResult | None] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def worker(idx: int) -> None:
        try:
            conn = connect(db_path)
            try:
                results[idx] = disp.ingest_file(
                    conn, file_path=pdf_path, project_root=tmp_path
                )
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001 — surface every failure mode
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive(), "worker thread hung past timeout"

    assert errors == [None, None], (
        f"ingest_file raised under concurrent same-file ingest: {errors!r}. "
        "Pre-fix this was sqlite3.IntegrityError on UNIQUE(content_hash); "
        "post-fix the loser must recover as deduped."
    )

    r0, r1 = results
    assert r0 is not None and r1 is not None

    # Exactly one race-winner (deduped=False) and one race-loser (deduped=True).
    assert sorted([r0.deduped, r1.deduped]) == [False, True], (
        f"Expected one winner + one loser; got deduped flags "
        f"{[r0.deduped, r1.deduped]}"
    )

    # The loser must point at the winner's row, not at a phantom uncommitted one.
    assert r0.source_id == r1.source_id, (
        f"Race-loser should resolve to the winner's source_id; "
        f"got {r0.source_id!r} vs {r1.source_id!r}"
    )

    # And only one row exists in the DB — the winner's INSERT.
    audit = connect(db_path)
    try:
        rows = audit.execute(
            "SELECT id FROM source_document WHERE content_hash = ?",
            (r0.content_hash,),
        ).fetchall()
        assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    finally:
        audit.close()
