"""Shared fixtures for the Meridian end-to-end test harness.

Every fixture is offline-safe: the LLM client is monkeypatched so no network
call ever happens, and project SQLite files live under pytest's tmp_path so
each test runs in an isolated workspace.

Determinism notes:
- ``settings.data_dir`` is repointed at a per-test tmp dir before each test
  imports anything project-related, then restored.
- The ``mock_llm_client`` fixture replaces ``meridian.llm.client.call_llm``
  with a stub that returns canned ``LlmCall`` records and persists them to
  the ``llm_call`` table the same way the real client does.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docx import Document
from fastapi.testclient import TestClient

from meridian import llm as _llm_pkg  # noqa: F401  (ensure package import)
from meridian.config import settings
from meridian.db.connection import connect, transaction
from meridian.llm.client import LlmCall
from meridian.projects import create_project

# --------------------------------------------------------------------------
# Projects-dir isolation
# --------------------------------------------------------------------------


@pytest.fixture
def tmp_projects_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint ``settings.data_dir`` at a per-test tmp directory.

    The Meridian config exposes ``settings.projects_dir`` as a derived
    property of ``settings.data_dir``; overriding the latter in-place gives
    us full filesystem isolation without re-importing the settings module.

    Alpha-22: also redirect the wizard state file via ``MERIDIAN_WIZARD_STATE_DIR``
    so the new stable ``state_path()`` (which resolves to ``~/.meridian/``
    rather than ``<projects_dir>/_meridian/``) stays isolated per test.
    Without this, tests share the developer's real ``~/.meridian/onboarding_state.json``
    and get contaminated state.
    """
    target = tmp_path / "projects"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", target)
    monkeypatch.setenv("MERIDIAN_DATA_DIR", str(target))
    # Redirect the wizard state file to a per-test directory so tests do
    # not share (or corrupt) the developer's real ~/.meridian/ state.
    wizard_state_dir = tmp_path / "wizard_state"
    wizard_state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(wizard_state_dir))
    # Belt-and-braces: clear any cloud creds so any accidental real call
    # would fail loudly rather than silently spend money.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return target


# --------------------------------------------------------------------------
# Project-creation fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def fresh_project(tmp_projects_dir: Path) -> Iterator[tuple[str, Path, sqlite3.Connection]]:
    """Create an empty project; yield (slug, db_path, conn). Closes conn on teardown."""
    name = "test-proj"
    _project_id, db_path = create_project(name=name, notes="e2e fixture")
    conn = connect(db_path)
    try:
        yield name, db_path, conn
    finally:
        conn.close()


def _make_synthetic_docx(target: Path, *, paragraphs: list[str] | None = None) -> Path:
    """Write a tiny .docx with one Heading 1 + a few paragraphs.

    Kept under ~200 bytes of body text so ingest + chunking is sub-millisecond.
    """
    doc = Document()
    doc.add_heading("Sample Specification Section", level=1)
    body = paragraphs or [
        "Contractor shall supply and install one (1) air handling unit.",
        "All ductwork shall be sealed to SMACNA Class A.",
        "Coordinate penetrations with the structural engineer.",
    ]
    for p in body:
        doc.add_paragraph(p)
    doc.save(target)
    return target


@pytest.fixture
def synthetic_docx(tmp_path: Path) -> Path:
    """Materialise a tiny synthetic DOCX under tmp_path/sample.docx."""
    return _make_synthetic_docx(tmp_path / "sample.docx")


@pytest.fixture
def fresh_project_with_sample_doc(
    fresh_project: tuple[str, Path, sqlite3.Connection],
    synthetic_docx: Path,
) -> tuple[str, Path, sqlite3.Connection, str]:
    """Create a project, ingest the synthetic doc, return (slug, db_path, conn, source_id)."""
    from meridian.ingest import ingest_file

    name, db_path, conn = fresh_project
    result = ingest_file(conn, file_path=synthetic_docx, project_root=synthetic_docx.parent)
    return name, db_path, conn, result.source_id


# --------------------------------------------------------------------------
# FastAPI test client
# --------------------------------------------------------------------------


@pytest.fixture
def fastapi_client(tmp_projects_dir: Path) -> Iterator[TestClient]:
    """Yield a Starlette TestClient bound to the live FastAPI app.

    Imports the app lazily so the ``tmp_projects_dir`` env-var override is
    in place before any module-level project-discovery code runs.
    """
    from meridian.api.main import app

    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------
# LLM stubbing
# --------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist_llm_row(
    conn: sqlite3.Connection,
    *,
    call_id: str,
    purpose: str,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_text: str,
    parsed: Any | None,
    job_id: str | None,
) -> None:
    """Insert a stub llm_call row matching the schema the real client writes."""
    started_at = _now()
    finished_at = _now()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO llm_call (
                id, job_id, purpose, provider, model, provider_api_version,
                system_prompt, user_prompt, prompt_version_ref,
                temperature, top_p, max_tokens, input_hash,
                response_text, parsed_response,
                prompt_tokens, completion_tokens,
                cache_read_tokens, cache_write_tokens, cost_cents,
                started_at, finished_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                job_id,
                purpose,
                provider,
                model,
                None,
                system_prompt,
                user_prompt,
                f"{purpose}_v_test",
                0.0,
                None,
                16000,
                "deadbeef",
                response_text,
                json.dumps(parsed) if parsed is not None else None,
                10,
                10,
                None,
                None,
                0,
                started_at,
                finished_at,
                None,
            ),
        )


# Default canned responses keyed by ``purpose``. Tests can override entries
# (or pass a ``per_call`` callable) by setting attributes on the fixture
# object before triggering work.
DEFAULT_QUALITY_SCAN = {
    "scan_quality": "clean",
    "markups_present": False,
    "illegible_regions": [],
    "mismatched_references": [],
    "is_template": False,
    "document_class": "customer_requirements",
    "document_state": "100%",
    "revision": "A",
    "revision_detected_via": "filename_pattern",
    "is_demarcation_schedule": False,
    "extraction_path": "text_spec",
    "summary": "Synthetic test document.",
}

DEFAULT_TRIAGE_KEEP = {"keep": True, "reason": "stub: keep"}

# NB: trade/service/category values MUST match the locked default taxonomies
# (meridian.taxonomy.defaults) — using a value not in the seeded list would
# trip the ``taxonomy_new_value_proposed`` flag in persist.py and route the
# row to ``quarantined`` instead of ``auto_approved``, knocking it out of
# v_master_register and breaking downstream tender-list assertions.
DEFAULT_TEXT_SPEC = {
    "deliverables": [
        {
            "trade": "Mechanical",
            "service": "HVAC",
            "category": "design",
            "deliverables_summary": "Supply and install one (1) air handling unit.",
            "confidence": "high",
            "flags": [],
            "source_ref": "Section 1, paragraph 1",
            "applicable_standards": ["SMACNA Class A"],
        }
    ],
    "audit": [],
    "questions": [],
}


class StubLlm:
    """Mutable container for canned responses; exposed via the fixture object.

    Attributes (all overridable per-test):
        responses: dict[purpose -> response_dict]
        per_call: optional callable (purpose, system_prompt, user_prompt) -> dict
                  When set, takes precedence over ``responses``.
        recorded: list of (purpose, system_prompt, user_prompt) — for assertions
        fail_on: optional purpose to raise RuntimeError on (one-shot)
    """

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any]] = {
            "quality_scan": dict(DEFAULT_QUALITY_SCAN),
            "triage": dict(DEFAULT_TRIAGE_KEEP),
            "extract_text_spec": dict(DEFAULT_TEXT_SPEC),
            "extract_demarcation": {"deliverables": [], "audit": [], "questions": []},
            "extract_bod": {"deliverables": [], "audit": [], "questions": []},
            "conflict_pass": {"conflicts": []},
            "error_explain": {"explanation": "stub"},
        }
        self.per_call: Callable[[str, str, str], dict[str, Any]] | None = None
        self.recorded: list[tuple[str, str, str]] = []
        self.fail_on: str | None = None
        self.extra_response_text: str = ""  # appended to response_text — used by
        # the secret-redaction test to inject a fake API key.

    def resolve(self, purpose: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if self.per_call is not None:
            return self.per_call(purpose, system_prompt, user_prompt)
        return self.responses.get(purpose, {})


@pytest.fixture
def mock_llm_client(monkeypatch: pytest.MonkeyPatch) -> StubLlm:
    """Replace ``meridian.llm.client.call_llm`` with a deterministic stub.

    The stub mirrors the real client's contract:
    - returns a populated ``LlmCall`` dataclass
    - inserts a row into the ``llm_call`` table (so FK references from
      ``deliverable.llm_call_id`` etc. resolve)
    - honours ``StubLlm.fail_on`` to simulate a mid-pipeline failure
    """
    stub = StubLlm()

    def _fake_call_llm(
        conn: sqlite3.Connection,
        *,
        purpose: str,
        provider: str | None = None,
        model: str | None = None,
        system_prompt: str,
        user_prompt: str,
        prompt_version_ref: str | None = None,
        job_id: str | None = None,
        temperature: float = 0.0,
        top_p: float | None = None,
        max_tokens: int = 16000,
        parse_json: bool = True,
    ) -> LlmCall:
        stub.recorded.append((purpose, system_prompt, user_prompt))
        if stub.fail_on == purpose:
            stub.fail_on = None  # one-shot
            raise RuntimeError(f"stub: forced failure for purpose={purpose}")

        parsed = stub.resolve(purpose, system_prompt, user_prompt)
        response_text = json.dumps(parsed) + stub.extra_response_text
        call_id = str(uuid.uuid4())
        _persist_llm_row(
            conn,
            call_id=call_id,
            purpose=purpose,
            provider=provider or "stub",
            model=model or "stub-model",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_text=response_text,
            parsed=parsed,
            job_id=job_id,
        )
        return LlmCall(
            id=call_id,
            purpose=purpose,  # type: ignore[arg-type]
            response_text=response_text,
            parsed_json=parsed if parse_json else None,
            cost_cents=0,
            prompt_tokens=10,
            completion_tokens=10,
            cache_read_tokens=None,
            cache_write_tokens=None,
        )

    # Patch every call site that imported ``call_llm`` by name. Each
    # extract module did ``from meridian.llm.client import call_llm``, so
    # the binding lives on each module's namespace and must be patched
    # there as well as on the source module.
    monkeypatch.setattr("meridian.llm.client.call_llm", _fake_call_llm)
    for mod_path in (
        "meridian.extract.quality_scan",
        "meridian.extract.triage",
        "meridian.extract.text_spec",
        "meridian.extract.bod_import",
        "meridian.extract.demarcation",
        "meridian.extract.conflict_pass",
        "meridian.bootstrap.sweep",
    ):
        # Module hasn't been imported yet or doesn't bind the name — fine;
        # the source-level patch above still applies once the module
        # imports it the first time.
        with contextlib.suppress(AttributeError, ImportError):
            monkeypatch.setattr(f"{mod_path}.call_llm", _fake_call_llm)

    return stub
