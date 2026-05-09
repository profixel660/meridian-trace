"""FastAPI router exposing the GUI setup wizard.

Mounted by :mod:`meridian.api.main` at the root prefix (paths begin
``/setup``). The Tauri-bundled SPA's first-run wizard (Stream B) is the
canonical consumer.

DEFERRED: see §3.2 — every ``/setup/*`` endpoint is intentionally PUBLIC
(no ``Depends(require_session)``). The wizard runs PRE-AUTH; a "team
edition" tightening (require an admin token to access setup once initial
setup is done) is a future operator decision recorded in
``OVERNIGHT_REPORT.md`` §3.2. Until then, anyone with HTTP access to the
host can drive setup. This is acceptable for v1 because the wizard runs
on localhost inside Tauri.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, status

from meridian.config import settings
from meridian.db.connection import connect
from meridian.ingest import ingest_file
from meridian.ingest.dispatcher import walk_directory
from meridian.ingest.dwg import ODA_INSTALL_URL, oda_available
from meridian.logging import get_logger
from meridian.projects import _slugify, create_project, project_db_path
from meridian.wizard.models import (
    ApiKeyRequest,
    ApiKeyResponse,
    FolderImportJobStatusResponse,
    FolderImportRequest,
    FolderScanRequest,
    FolderScanResponse,
    FolderSkipEntry,
    ImportErrorCode,
    ImportErrorEntry,
    ImportErrorGroup,
    ImportJobResponse,
    ImportJobStatusResponse,
    ImportRequest,
    ImportSkipRequest,
    ImportSkipResponse,
    OdaStatus,
    ProjectCreateRequest,
    ProjectCreateResponse,
    RuntimeStatusResponse,
    SetupCompleteResponse,
    SetupDefaultsResponse,
    SetupStateResponse,
    SuggestNameRequest,
    SuggestNameResponse,
)

# Process-start timestamp captured at module import. Used by the alpha-12
# /setup/runtime endpoint to compute uptime; also recorded as the
# canonical ISO-8601 wall-clock start so the operator can correlate
# with backend.log entries without doing arithmetic.
_PROCESS_STARTED_MONOTONIC: float = time.monotonic()
# Sub-millisecond ISO-8601 with explicit Z suffix. Symmetric with
# uptime_seconds (which has 3-decimal precision) so timestamp + uptime
# arithmetic is consistent. Alpha-12 reviewer flagged the alpha-11 form
# `strftime("%Y-%m-%dT%H:%M:%SZ")` as silently dropping sub-second precision.
_PROCESS_STARTED_AT_ISO: str = (
    datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
)
from meridian.wizard.state import (
    _KEYRING_ACCOUNT,
    _KEYRING_SERVICE,
    OnboardingState,
    WizardState,
    load_wizard_state,
    mark_documents_imported,
    mark_documents_skipped,
    mark_first_project,
    mark_setup_complete,
    save_wizard_state,
    set_api_key_configured,
    validate_anthropic_key_str,
)

_log = get_logger("meridian.wizard")

# Stdlib logger routed to operator-visible backend.log so import-worker
# events surface alongside uvicorn's HTTP access lines. Alpha-11
# finding: alpha-10 backend.log was silent during a 30-min folder
# import — operator had no way to tell the worker was alive.
#
# Alpha-12 finding: alpha-11 _attached_ this logger but never gave it
# a working route to stderr. ``meridian.api.main`` calls
# ``configure_logging(console=False)`` to keep structlog JSON out of
# uvicorn's stderr (so HTTP access lines stay readable). That suppresses
# the global stderr handler — which means alpha-11's ``_stdlog.info(...)``
# calls were silently dropped. **No per-file lines actually appeared in
# backend.log on the user's alpha-11 install.**
#
# The fix: attach a targeted ``StreamHandler(sys.stderr)`` to THIS
# logger only, and disable propagation so we don't double-emit through
# root. The wizard import logger now gets to stderr (and therefore
# backend.log) regardless of the global ``console`` setting; structlog's
# JSON tree elsewhere is unaffected.
_stdlog = logging.getLogger("meridian.wizard.import")
_stdlog.setLevel(logging.INFO)
# Idempotent: re-import (e.g. test reload) must not stack handlers.
if not any(getattr(h, "_meridian_import_stderr", False) for h in _stdlog.handlers):
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setLevel(logging.INFO)
    _stderr_handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [import] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _stderr_handler._meridian_import_stderr = True  # type: ignore[attr-defined]
    _stdlog.addHandler(_stderr_handler)
    # propagate=False so the message doesn't double-fire through root.
    _stdlog.propagate = False

# --------------------------------------------------------------------------
# Rate limiting — same shape as meridian/auth/login_api.py: in-memory
# sliding-window bucket keyed by client IP. 10 attempts / 5 minutes.
# Applied to /setup/api-key only; the other endpoints are cheap and
# state-changes are user-driven.
# --------------------------------------------------------------------------

_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
_RATE_LIMIT_MAX_ATTEMPTS = 10

_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = Lock()


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _check_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_ATTEMPTS:
            return False
        bucket.append(now)
        return True


# --------------------------------------------------------------------------
# Import-job tracker — minimal in-memory job board.
#
# Reuses meridian.ingest.dispatcher.ingest_file directly. The tracker
# spawns a daemon thread per request; each call is short (single-digit
# seconds for typical PDFs) so the GUI's poll loop completes in 1-3
# pings. The job records are never persisted — if the user reloads the
# wizard mid-import the GUI restarts the import (idempotent because
# ingest_file dedupes on content_hash).
# --------------------------------------------------------------------------


class _ImportJob:
    __slots__ = (
        "_persisted",
        "completed",
        "current_file",
        "db_path",
        "deduped",
        "errors",
        "id",
        "imported",
        "status",
        "total",
    )

    def __init__(self, total: int) -> None:
        self.id = str(uuid.uuid4())
        self.status = "pending"
        self.total = total
        self.completed = 0
        self.imported = 0
        self.deduped = 0
        # Structured error store. The legacy flat list[str] view used by
        # alpha-10-and-earlier responses is derived in the response
        # builder via ``_legacy_error_strings(...)`` so we don't carry
        # duplicate state here.
        self.errors: list[ImportErrorEntry] = []
        self.current_file: str | None = None
        self.db_path: Path | None = None
        self._persisted = False


_jobs: dict[str, _ImportJob] = {}
_jobs_lock = Lock()


# --------------------------------------------------------------------------
# Idempotency registry — alpha-24 item #4.
#
# Maps Idempotency-Key (UUIDv4 from the wizard) → (job_id, recorded_at_monotonic).
# Lifetime is process-local: a backend bounce forfeits the dedup window.
# Lookups also opportunistically delete entries older than the TTL — no
# background thread, no persistent storage.
# --------------------------------------------------------------------------

_IDEMPOTENCY_TTL_SECONDS: float = 15 * 60.0
_IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_idempotency: dict[str, tuple[str, float]] = {}
_idempotency_lock = Lock()


def _idempotency_record(key: str, job_id: str) -> None:
    with _idempotency_lock:
        _idempotency[key] = (job_id, time.monotonic())


def _idempotency_lookup(key: str) -> tuple[str, float] | None:
    """Return (job_id, age_seconds) if the key is on file and unexpired.

    Side-effect: opportunistically purges any expired entries it sees.
    """
    now = time.monotonic()
    with _idempotency_lock:
        # Lazy GC of every expired entry — cheap (registry is bounded by
        # request rate × TTL, ~hundreds at most for a single-user wizard).
        expired = [
            k for k, (_jid, recorded_at) in _idempotency.items()
            if now - recorded_at > _IDEMPOTENCY_TTL_SECONDS
        ]
        for k in expired:
            del _idempotency[k]
        record = _idempotency.get(key)
        if record is None:
            return None
        job_id, recorded_at = record
        return job_id, now - recorded_at


def _idempotency_claim(key: str, job_id: str) -> str:
    """Atomically claim ``key`` for ``job_id`` or return the existing winner.

    If the key is already registered (and unexpired) returns the winner's
    job_id unchanged. If the key is absent (or expired), records ``job_id``
    and returns it. The entire check-and-set is held under ``_idempotency_lock``
    so two threads racing on the same key always agree on one winner — the
    alpha-24 parallel-POST race condition that the gauntlet step 7i catches.
    """
    now = time.monotonic()
    with _idempotency_lock:
        # Lazy GC (mirrors _idempotency_lookup).
        expired = [
            k for k, (_jid, recorded_at) in _idempotency.items()
            if now - recorded_at > _IDEMPOTENCY_TTL_SECONDS
        ]
        for k in expired:
            del _idempotency[k]
        record = _idempotency.get(key)
        if record is not None:
            existing_job_id, _ = record
            return existing_job_id
        # No existing entry — claim the slot for this job_id.
        _idempotency[key] = (job_id, now)
        return job_id


def _validate_idempotency_key(value: str | None) -> str | None:
    """Return the UUIDv4 unchanged if valid, or None if absent. Raises on malformed."""
    if value is None:
        return None
    if not _IDEMPOTENCY_KEY_PATTERN.match(value):
        _log.info("wizard.import_folder.idempotency_token_rejected", reason="invalid_format")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_idempotency_key",
                "message": (
                    "Idempotency-Key header must be a UUIDv4 "
                    "(lower-case, e.g. '11111111-1111-4111-8111-111111111111')."
                ),
            },
        )
    return value


# --------------------------------------------------------------------------
# Error classification + coalescing — alpha-11
# --------------------------------------------------------------------------
#
# Alpha-11 finding: a real-world SME corpus produced 18 identical 800-byte
# strings for "ODA File Converter not installed" inside ``failed: list[str]``.
# Operator UX failure mode: the GUI rendered each as a separate row,
# unreadable and unactionable. The fix:
#
#   * Per-file failures are stored as :class:`ImportErrorEntry` with a
#     stable ``code`` field.
#   * The status response coalesces same-code entries into
#     :class:`ImportErrorGroup` with a PM-language summary + remediation.
#
# Adding a new code is a scoped change here AND in
# ``meridian.wizard.models.ImportErrorCode`` AND in the frontend's copy.

_ERROR_REMEDIATION: dict[ImportErrorCode, str] = {
    "oda_missing": (
        "Install ODA File Converter from "
        f"{ODA_INSTALL_URL} and re-scan this folder. The wizard "
        "detects the binary automatically once it is on PATH or in the "
        "default Windows install location."
    ),
    "pdf_unreadable": (
        "Open the file in a PDF reader to confirm it isn't corrupt. "
        "If the PDF is a scan with no text layer, re-export it from "
        "the source application or run OCR before re-importing."
    ),
    "file_locked": (
        "Close the file in any other application (Word, Excel, "
        "AutoCAD, your file manager's preview pane) and re-scan."
    ),
    "permission_denied": (
        "Grant read access to this file (right-click → Properties "
        "→ Security on Windows) or move it out of a system-protected "
        "directory and re-scan."
    ),
    "file_missing": (
        "The file was deleted or moved between scan and import. "
        "Re-scan the folder and try again."
    ),
    "unsupported_mime": (
        "The dispatcher refused this file type. If you believe it "
        "should be supported, file a GitHub issue with the filename "
        "and the error message below."
    ),
    "worker_aborted": (
        "The import worker terminated unexpectedly. Capture the "
        "backend.log lines around this entry and file a GitHub issue. "
        "You can re-run the import — successfully ingested files "
        "are de-duplicated by content hash."
    ),
    "unknown": "",  # rendered with no remediation; raw message only.
}


_GROUP_SUMMARY: dict[ImportErrorCode, str] = {
    "oda_missing": (
        "{count} AutoCAD drawing{plural} {verb} skipped — ODA File "
        "Converter is not installed."
    ),
    "pdf_unreadable": "{count} PDF{plural} could not be parsed.",
    "file_locked": "{count} file{plural} {verb} locked by another application.",
    "permission_denied": "{count} file{plural} {verb} unreadable (permission denied).",
    "file_missing": "{count} file{plural} disappeared between scan and import.",
    "unsupported_mime": "{count} file{plural} {verb} an unsupported type.",
    "worker_aborted": (
        "The import worker terminated unexpectedly after processing "
        "{count} file{plural}."
    ),
    "unknown": "{count} file{plural} failed for an unclassified reason.",
}


def _classify_ingest_error(exc: BaseException) -> ImportErrorCode:
    """Map a caught exception to a stable :class:`ImportErrorCode`.

    Alpha-11: classification lives here (rather than scattered through
    the worker) so the test suite can exercise every branch directly.
    """
    msg = str(exc)
    msg_lower = msg.lower()
    # ODA File Converter — dwg.py raises FileNotFoundError with that exact
    # phrase. Match on the phrase rather than ``isinstance(exc,
    # FileNotFoundError)`` because FileNotFoundError is also raised for
    # file-missing-at-import-time.
    if isinstance(exc, FileNotFoundError) and "oda file converter" in msg_lower:
        return "oda_missing"
    if isinstance(exc, FileNotFoundError):
        return "file_missing"
    if isinstance(exc, NotImplementedError):
        return "unsupported_mime"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    # Windows sharing violation: WinError 32 surfaces as OSError with
    # winerror=32 OR as a generic OSError with "being used by another
    # process" in the message. Both flavours fall here.
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror == 32:
            return "file_locked"
        if "being used by another process" in msg_lower:
            return "file_locked"
        if getattr(exc, "errno", None) == 13:
            return "permission_denied"
    # PDF parse failures: pypdf raises PdfReadError (a ValueError
    # subclass) and pdfplumber raises various RuntimeError flavours. We
    # match on the substrings rather than importing those modules to
    # avoid a hard dep here.
    #
    # Alpha-12 reviewer fix: alpha-11 used "stream length" as a marker
    # which is broader than PDF context (any HTTP/JSON parser can use
    # that phrase). Tightened to PDF-specific markers only — pdf_header
    # and eof_marker are PDF-format-specific; pypdf/pdfplumber are
    # explicit module names. "stream length" was the over-broad one
    # and is removed.
    if any(t in msg_lower for t in (
        "pypdf",
        "pdfplumber",
        "pdf header",
        "eof marker",
        "pdf trailer",
        "xref",
        "/length is wrong",
    )):
        return "pdf_unreadable"
    return "unknown"


def _format_group_summary(code: ImportErrorCode, count: int) -> str:
    plural = "" if count == 1 else "s"
    verb = "was" if count == 1 else "were"
    template = _GROUP_SUMMARY.get(code, _GROUP_SUMMARY["unknown"])
    return template.format(count=count, plural=plural, verb=verb)


_FILES_PER_GROUP_CAP = 100


def _coalesce_errors(errors: Iterable[ImportErrorEntry]) -> list[ImportErrorGroup]:
    """Group :class:`ImportErrorEntry` records by ``code``.

    Files-per-group is capped at :data:`_FILES_PER_GROUP_CAP`; the
    ``truncated`` flag tells the GUI to render an ``and N more`` tail.
    This keeps the JSON payload bounded even on a 10k-file corpus.
    """
    by_code: dict[ImportErrorCode, list[ImportErrorEntry]] = {}
    for entry in errors:
        by_code.setdefault(entry.code, []).append(entry)
    groups: list[ImportErrorGroup] = []
    for code, entries in by_code.items():
        files_full = [e.file for e in entries]
        truncated = len(files_full) > _FILES_PER_GROUP_CAP
        files = files_full[:_FILES_PER_GROUP_CAP]
        groups.append(
            ImportErrorGroup(
                code=code,
                count=len(entries),
                summary=_format_group_summary(code, len(entries)),
                remediation=_ERROR_REMEDIATION.get(code, ""),
                files=files,
                truncated=truncated,
            )
        )
    # Stable ordering: oda_missing first (it usually has the largest
    # count and the most actionable remediation), then by descending
    # count so the most-impactful group surfaces above.
    priority: dict[ImportErrorCode, int] = {
        "oda_missing": 0,
        "pdf_unreadable": 1,
        "file_locked": 2,
        "permission_denied": 3,
        "file_missing": 4,
        "unsupported_mime": 5,
        "worker_aborted": 6,
        "unknown": 7,
    }
    groups.sort(key=lambda g: (priority.get(g.code, 99), -g.count))
    return groups


def _legacy_error_strings(errors: Iterable[ImportErrorEntry]) -> list[str]:
    """Materialise the alpha-10-shape ``"{file}: {message}"`` list.

    Kept for one release of frontend-bundle backward compatibility.
    Will be removed alongside the deprecated fields in alpha-13.
    """
    return [f"{e.file}: {e.message}" for e in errors]


def _oda_status_now() -> OdaStatus:
    """Snapshot ODA File Converter availability for response embedding."""
    available, binary, version = oda_available()
    return OdaStatus(
        available=available,
        version=version,
        install_url=ODA_INSTALL_URL,
        binary_path=str(binary) if binary is not None else None,
    )


def _run_import_job(job: _ImportJob, *, db_path: Path, paths: Iterable[str]) -> None:
    """Worker body — drives ingest_file over each path, updates the job record.

    Alpha-11 hardening:

    * Per-file INFO logging via :data:`_stdlog` so the operator-visible
      ``backend.log`` shows what the worker is doing in real time
      (alpha-10 silence drove a 30-min triage).
    * Top-level ``BaseException`` catch so a ``KeyboardInterrupt`` /
      ``SystemExit`` / ``MemoryError`` cannot kill the thread silently
      and leave ``status='running'`` forever (alpha-10 finding).
    * Structured per-file error entries with a stable ``code`` field so
      the GUI can coalesce same-cause failures into one row with a
      remediation hint.
    * ``completed`` increments in exactly ONE place (the per-file
      ``finally`` block) — the alpha-10 ``not path.exists()`` branch
      double-incremented because both the inline ``+= 1`` and the
      enclosing ``finally`` ran.
    """
    paths_list = list(paths)  # materialise so we can index for logging
    job.status = "running"
    _stdlog.info(
        "import_job.start job=%s total=%d db=%s",
        job.id, job.total, db_path,
    )
    try:
        # Long busy_timeout: another process (e.g. CLI) might hold the
        # write lock. 30s mirrors the existing ingest endpoint.
        conn = connect(db_path, busy_timeout_ms=30000)
    except Exception as exc:  # pragma: no cover — extremely defensive
        _stdlog.exception("import_job.db_open_failed job=%s", job.id)
        job.status = "failed"
        job.errors.append(
            ImportErrorEntry(
                code="unknown",
                file="<db>",
                message=f"Could not open project DB: {exc}",
            )
        )
        return

    aborted_with: BaseException | None = None
    try:
        for index, raw in enumerate(paths_list, start=1):
            path = Path(raw).expanduser()
            job.current_file = str(path)
            t0 = time.monotonic()
            file_outcome: str = "imported"
            try:
                if not path.exists():
                    job.errors.append(
                        ImportErrorEntry(
                            code="file_missing",
                            file=path.name,
                            message=f"File not found: {path}",
                        )
                    )
                    file_outcome = "missing"
                    # NB: NO `completed += 1` here — the enclosing
                    # `finally` handles it. Alpha-10 had both, which
                    # double-incremented when files vanished between
                    # scan and import.
                    continue
                result = ingest_file(
                    conn,
                    file_path=path,
                    project_root=settings.project_root,
                )
                if result.deduped:
                    job.deduped += 1
                    file_outcome = "deduped"
                else:
                    job.imported += 1
                    file_outcome = "imported"
            except Exception as exc:  # noqa: BLE001 — surface every error to UI
                code = _classify_ingest_error(exc)
                job.errors.append(
                    ImportErrorEntry(
                        code=code,
                        file=path.name,
                        message=str(exc),
                    )
                )
                file_outcome = f"failed:{code}"
                _stdlog.warning(
                    "import_job.file_failed job=%s [%d/%d] file=%s code=%s err=%s",
                    job.id, index, job.total, path.name, code, exc,
                )
            finally:
                job.completed += 1
                elapsed = time.monotonic() - t0
                # Per-file INFO line (or already-WARNING'd in the except).
                # Suppress redundant duplicate when the except branch
                # already logged a warning.
                if not file_outcome.startswith("failed:"):
                    _stdlog.info(
                        "import_job.file_done job=%s [%d/%d] file=%s outcome=%s elapsed=%.2fs",
                        job.id, index, job.total, path.name, file_outcome, elapsed,
                    )
    except BaseException as exc:  # noqa: BLE001 — keep the thread observable
        # Alpha-11: BaseException catches KeyboardInterrupt / SystemExit /
        # MemoryError / asyncio.CancelledError. Without this the worker
        # thread can die silently and the job stays at status='running'
        # while the frontend polls forever (alpha-10 lived this).
        aborted_with = exc
        _stdlog.exception(
            "import_job.aborted job=%s after_completed=%d/%d",
            job.id, job.completed, job.total,
        )
        job.errors.append(
            ImportErrorEntry(
                code="worker_aborted",
                file=job.current_file or "<worker>",
                message=f"{type(exc).__name__}: {exc}",
            )
        )
        # Do NOT re-raise. We want the job record to stay coherent so the
        # frontend can show a clear failure to the user. Re-raising here
        # would also mean the conn.close() below is skipped.
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        job.current_file = None

    if aborted_with is not None:
        job.status = "failed"
    else:
        job.status = (
            "failed"
            if job.errors and job.imported == 0 and job.deduped == 0
            else "succeeded"
        )
    _stdlog.info(
        "import_job.finish job=%s status=%s imported=%d deduped=%d failed=%d completed=%d/%d",
        job.id, job.status, job.imported, job.deduped, len(job.errors),
        job.completed, job.total,
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _state_to_response(state: WizardState) -> SetupStateResponse:
    return SetupStateResponse(
        complete=state.is_complete,
        api_key_set=state.api_key_set,
        first_project_slug=state.first_project_slug,
        first_project_name=state.gui_first_project_name,
        first_project_dir=state.gui_first_project_dir,
        documents_imported=state.documents_imported,
        documents_skipped=state.documents_skipped,
        next_step=state.next_step,  # type: ignore[arg-type]
    )


def _ensure_writeable(target: Path) -> None:
    """Create the directory if needed; raise HTTPException 400 on failure."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        # Probe write permission with a transient file. Avoids leaving
        # litter on success.
        probe = target / ".meridian-writeable-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "projects_dir_not_writeable",
                "message": (
                    f"Folder isn't writeable — try a different one. ({exc})"
                ),
            },
        ) from exc


def _invalid_key_message(detail: str) -> str:
    """PM-language message for an invalid-key outcome."""
    # We do not surface the raw exception text — it can include 401 bodies
    # with internal hostnames. The PM-targeted message stays stable.
    _log.info("wizard.api_key.invalid", detail=detail[:200])
    return (
        "Anthropic rejected this key. Check the key starts with 'sk-ant-' "
        "and that the account has billing set up."
    )


def _unable_to_verify_message(detail: str) -> str:
    _log.info("wizard.api_key.unable_to_verify", detail=detail[:200])
    return (
        "Couldn't reach Anthropic to check the key (network or SDK issue). "
        "Saved it anyway — the first real extraction will surface any "
        "auth errors clearly."
    )


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

wizard_router = APIRouter(prefix="/setup", tags=["setup"])


@wizard_router.get("/state", response_model=SetupStateResponse)
def setup_state() -> SetupStateResponse:
    """Return the current wizard state.

    Public — no auth required (pre-setup, no user / token exists yet).
    Reads from ``~/.meridian/onboarding_state.json`` (``MERIDIAN_WIZARD_STATE_DIR``
    overrides). A user who started in CLI then opens the GUI sees
    CLI-driven progress correctly reflected in ``next_step``.
    """
    return _state_to_response(load_wizard_state())


@wizard_router.get("/defaults", response_model=SetupDefaultsResponse)
def setup_defaults() -> SetupDefaultsResponse:
    """Server-side defaults the frontend pre-fills into form fields.

    Returns ``projects_dir`` resolved from the same ``_meridian_home()``
    chain the rest of the backend uses (MERIDIAN_HOME env, then
    ``C:\\Meridian`` on Windows when present, then ``~/Meridian``), with
    ``/projects`` appended. Always a real, valid filesystem path —
    never a placeholder like ``C:\\Users\\<you>\\...`` (the alpha-5 bug
    class). The frontend submits this verbatim, so the round-trip must
    survive Pydantic + the OS path validators.
    """
    from meridian.config import _meridian_home  # local import — narrow surface

    home = _meridian_home()
    return SetupDefaultsResponse(
        projects_dir=str(home / "projects"),
        home_dir=str(Path.home()),
    )


@wizard_router.get("/runtime", response_model=RuntimeStatusResponse)
def setup_runtime() -> RuntimeStatusResponse:
    """Operator-facing runtime introspection (alpha-12).

    Triage on a slow / hung / silent backend used to require crawling
    Get-Process + Get-NetTCPConnection + reading log paths the operator
    had to know. This endpoint surfaces the same data the backend
    itself knows. Cheap (no DB, no I/O beyond os.getpid + dir lookups)
    so it stays callable when the backend is under load.
    """
    from meridian import __version__ as meridian_version  # local import

    # Snapshot the most-recent folder-import job (if any) — answers
    # "is the worker stuck?" from one HTTP call.
    last_import: dict | None = None
    with _jobs_lock:
        if _jobs:
            # Most-recent by current_file presence > terminal status.
            # In practice the dict insertion order is fine — Python 3.7+
            # dicts preserve insertion order, so the last job started
            # is the last item.
            job_id, job = next(reversed(_jobs.items()))
            # Last-activity heuristic: if current_file is set we know
            # the worker is mid-iteration; otherwise the job is between
            # files or terminal. We don't track per-file timestamps so
            # this is a coarse signal; precise per-file timing lives in
            # backend.log via the alpha-12 _stdlog handler.
            last_import = {
                "job_id": job_id,
                "status": job.status,
                "completed": job.completed,
                "total": job.total,
                "imported": job.imported,
                "deduped": job.deduped,
                "failed_count": len(job.errors),
                "current_file": (
                    Path(job.current_file).name if job.current_file else None
                ),
            }

    # Discover the structlog directory via the alpha-13 thread-safe
    # accessor. Alpha-12's read of the module global was unsynchronised;
    # alpha-13 added current_log_dir() with an RLock so this is now
    # honest (no race between configure_logging and a concurrent
    # /setup/runtime probe).
    structlog_dir: str | None = None
    try:
        from meridian.logging.setup import current_log_dir  # local import

        log_dir = current_log_dir()
        if log_dir is not None:
            structlog_dir = str(log_dir)
    except Exception:  # noqa: BLE001 — best-effort introspection
        pass

    # Alpha-13 auth bypass status. Read at request time so an env-var
    # toggle is reflected immediately on the next probe.
    from meridian.auth.fastapi_dep import auth_disabled as _auth_disabled  # local import

    return RuntimeStatusResponse(
        pid=os.getpid(),
        started_at=_PROCESS_STARTED_AT_ISO,
        uptime_seconds=round(time.monotonic() - _PROCESS_STARTED_MONOTONIC, 3),
        version=meridian_version,
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        platform=sys.platform,
        # Honour MERIDIAN_BACKEND_LOG (set by Install-Meridian.ps1 + the
        # new alpha-12 launcher .bat files) but fall back to None when
        # running in dev tree / gauntlet / pytest where there is no
        # canonical operator-visible log file.
        backend_log_path=os.environ.get("MERIDIAN_BACKEND_LOG"),
        structlog_dir=structlog_dir,
        last_import_job=last_import,
        auth_disabled=_auth_disabled(),
    )


@wizard_router.post(
    "/api-key",
    response_model=ApiKeyResponse,
    responses={
        429: {"description": "Too many validation attempts from this IP."},
    },
)
def setup_api_key(req: ApiKeyRequest, request: Request) -> ApiKeyResponse:
    """Validate + persist an Anthropic API key.

    Three-outcome (per user MEMORY: pass/fail/borderline). On 'valid' or
    'unable_to_verify' the key is persisted to the OS keychain via the
    same store the rest of Meridian uses (see
    ``meridian.auth.secrets.default_store``). On 'invalid' nothing is
    persisted and the wizard's ``api_key_configured`` flag stays False.
    """
    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many validation attempts. Try again in "
                f"{_RATE_LIMIT_WINDOW_SECONDS // 60} minutes."
            ),
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
        )

    outcome, detail = validate_anthropic_key_str(req.key)

    if outcome == "invalid":
        # Do NOT persist; do NOT mark the step complete. Surface the
        # PM-language message; log the underlying detail for ops.
        return ApiKeyResponse(
            outcome="invalid",
            message=_invalid_key_message(detail),
        )

    # Persist on valid OR unable_to_verify. The CLI wizard never persists,
    # but the GUI is the durable-install path and re-prompting on every
    # launch is the worst-possible UX (per UX-discoverability MEMORY).
    _persist_api_key(req.key)

    state = load_wizard_state()
    set_api_key_configured(state)

    if outcome == "valid":
        return ApiKeyResponse(outcome="valid", message="Key validated.")
    return ApiKeyResponse(
        outcome="unable_to_verify",
        message=_unable_to_verify_message(detail),
    )


def _persist_api_key(key: str) -> None:
    """Stash the key in the OS keychain via the ``keyring`` module.

    Uses a wizard-specific service name (``meridian.api_key``) so it
    doesn't collide with the TOTP-secret service (``meridian.totp``).
    Falls back to logging-only if keyring is unavailable — the env-var
    override (``ANTHROPIC_API_KEY``) remains a valid path for advanced
    users and the wizard already accepts that as a separate code path.
    """
    try:
        import keyring  # noqa: PLC0415

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, key)
    except Exception as exc:  # noqa: BLE001 — keyring backends vary wildly
        _log.warning(
            "wizard.api_key.keyring_unavailable",
            error=f"{type(exc).__name__}: {exc}",
        )


@wizard_router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    responses={
        400: {"description": "Projects directory is not writeable."},
        409: {"description": "A project with this slug already exists."},
    },
)
def setup_create_project(req: ProjectCreateRequest) -> ProjectCreateResponse:
    """Create OR ADOPT the user's first project.

    If the wizard's import-folder step has already created a staging
    SQLite file at ``settings.projects_dir/<state.first_project_slug>.sqlite``,
    that file is MOVED to ``<req.projects_dir>/<req.slug>.sqlite`` and its
    ``project.name`` row is updated. This preserves imports that happened
    before the user chose a final projects_dir.

    If no staging exists (user skipped import), falls back to minting a
    fresh DB via ``create_project``.

    Three branches:
        1. No staging DB → ``create_project`` fresh.
        2. Staging at the same path AND same slug → in-place name update.
        3. Staging at a different path or different slug → ``adopt_project``
           (file move + name rewrite).

    Refuses to overwrite a pre-existing project at the target path
    (409). Refuses to run if a folder-import job is still writing to
    the staging DB (409 with ``error: import_in_progress``).
    """
    target_dir = Path(req.projects_dir).expanduser()
    _ensure_writeable(target_dir)

    # Locate any staging DB BEFORE we mutate settings.data_dir, because
    # project_db_path() resolves against settings.projects_dir at call time.
    state = load_wizard_state()
    staging_db: Path | None = None
    if state.cli.first_project_slug:
        candidate = project_db_path(state.cli.first_project_slug)
        if candidate.exists():
            staging_db = candidate

    # I1: Guard against the folder-import / projects-POST race.
    # If a background import job is still writing to the staging DB, refuse
    # the projects step — moving an open SQLite file raises PermissionError
    # on Windows or causes silent WAL corruption on POSIX.
    if staging_db is not None:
        with _jobs_lock:
            for job in _jobs.values():
                if (
                    getattr(job, "db_path", None) == staging_db
                    and job.status in {"pending", "running"}
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "import_in_progress",
                            "message": (
                                f"An import job ({job.id[:8]}) is still "
                                f"writing to the staging project database. "
                                "Wait for it to finish before creating the project."
                            ),
                            "job_id": job.id,
                        },
                    )

    # Now mutate process-wide projects_dir to the user's choice.
    settings.data_dir = target_dir

    final_db_path = project_db_path(req.slug)
    # Case 2 is the scenario where the staging DB and the final DB are the
    # same file — the user accepted the auto-derived slug and the default
    # projects_dir. We must NOT treat this as a collision and refuse; instead
    # we fall through to the in-place name UPDATE below.
    #
    # However we must refuse a DUPLICATE POST (no import-folder step,
    # setup_create_project called twice with same slug). The distinguishing
    # signal: if the DB was created by setup_import_folder, there will be at
    # least one job in _jobs whose db_path matches the staging DB. If the DB
    # was created by a prior setup_create_project (branch 1), no such job
    # exists.
    _staging_created_by_import = False
    if staging_db is not None and staging_db.resolve() == final_db_path.resolve():
        with _jobs_lock:
            _staging_created_by_import = any(
                getattr(j, "db_path", None) == staging_db for j in _jobs.values()
            )
    _is_staging_at_final = (
        staging_db is not None
        and staging_db.resolve() == final_db_path.resolve()
        and _staging_created_by_import
    )
    if final_db_path.exists() and not _is_staging_at_final:
        # Pre-existing project at the target slug that is either unrelated
        # (not our staging DB) or was created by a prior setup_create_project
        # call — refuse to overwrite.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "slug_exists",
                "existing_db_path": str(final_db_path),
            },
        )

    if staging_db is not None and staging_db.resolve() != final_db_path.resolve():
        # Branch 3: adopt the staging DB into the user's chosen
        # location and/or new slug, rewriting project.name.
        from meridian.projects import adopt_project as _adopt  # noqa: PLC0415
        try:
            _adopt(
                old_db_path=staging_db,
                new_db_path=final_db_path,
                new_name=req.name,
            )
        except FileExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "slug_exists", "existing_db_path": str(final_db_path)},
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "staging_db_locked",
                    "message": (
                        f"Could not move the staging project database — "
                        f"it may still be in use. ({exc})"
                    ),
                },
            ) from exc
    elif staging_db is not None and staging_db.resolve() == final_db_path.resolve():
        # Branch 2: same path AND same slug — just update the name in place.
        from meridian.db.connection import connect as _connect, transaction as _txn  # noqa: PLC0415
        conn = _connect(final_db_path, busy_timeout_ms=5000)
        try:
            with _txn(conn):
                conn.execute("UPDATE project SET name = ?", (req.name,))
        finally:
            conn.close()
    else:
        # Branch 1: no staging — user skipped import. Mint fresh.
        try:
            _project_id, final_db_path = create_project(name=req.name)
        except FileExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "slug_exists", "existing_db_path": str(final_db_path)},
            ) from exc

    mark_first_project(
        state,
        slug=req.slug,
        name=req.name,
        projects_dir=str(target_dir),
    )

    return ProjectCreateResponse(
        created=True,
        slug=req.slug,
        db_path=str(final_db_path),
    )


@wizard_router.post("/import", response_model=ImportJobResponse)
def setup_import(req: ImportRequest) -> ImportJobResponse:
    """Kick off a background import job; return a job_id immediately."""
    db_path = project_db_path(req.project_slug)
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {req.project_slug}",
        )

    # Intentionally leaves job.db_path as None: /setup/import operates on
    # an already-confirmed project (post-/setup/projects), not on a wizard
    # staging DB. setup_suggest_project_name's exclusion logic uses
    # job.db_path as the staging-vs-confirmed discriminator — only
    # folder-import jobs (which write a fresh staging DB) should bypass
    # collision detection.
    job = _ImportJob(total=len(req.paths))
    with _jobs_lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_import_job,
        kwargs={"job": job, "db_path": db_path, "paths": list(req.paths)},
        daemon=True,
        name=f"wizard-import-{job.id[:8]}",
    )
    thread.start()
    return ImportJobResponse(job_id=job.id)


@wizard_router.get(
    "/import/{job_id}",
    response_model=ImportJobStatusResponse,
)
def setup_import_status(job_id: str) -> ImportJobStatusResponse:
    """Poll an import job's progress.

    Side-effect on terminal status: when the job first hits ``succeeded``
    we update the persisted ``documents_imported`` counter. This ensures
    that even if the GUI never polls again the count is durable (e.g.
    user closes wizard before the success status is rendered).
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    # On terminal-state transition, fold the import count into wizard state.
    # Use a once-per-job sentinel by removing the job from the dict — a
    # subsequent poll will get a fresh 'succeeded' snapshot via a re-add
    # below. Simpler: track a flag on the job object.
    if job.status == "succeeded" and not job._persisted:
        if job.imported > 0:
            state = load_wizard_state()
            mark_documents_imported(state, count=job.imported)
        # mark persisted regardless, even if imported==0 (all-deduped) —
        # the subsequent poll should not re-mark.
        job._persisted = True

    return ImportJobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        total=job.total,
        completed=job.completed,
        imported=job.imported,
        deduped=job.deduped,
        # Legacy flat list — populated for one release of frontend
        # backward-compat. Will go away in alpha-13.
        errors=_legacy_error_strings(job.errors),
        error_groups=_coalesce_errors(job.errors),
    )


@wizard_router.post(
    "/import/skip",
    response_model=ImportSkipResponse,
)
def setup_import_skip(req: ImportSkipRequest) -> ImportSkipResponse:
    """Mark the document-import step skipped. Idempotent."""
    state = load_wizard_state()
    if state.first_project_slug is None or state.first_project_slug != req.project_slug:
        # Defensive: skipping the step only makes sense after the project
        # has been created. Return 400 so the GUI surfaces it rather than
        # silently advancing.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot skip document import before creating the first project."
            ),
        )
    mark_documents_skipped(state)
    return ImportSkipResponse(acknowledged=True)


@wizard_router.post(
    "/complete",
    response_model=SetupCompleteResponse,
)
def setup_complete() -> SetupCompleteResponse:
    """Idempotent: stamp setup as complete and return the final state."""
    state = load_wizard_state()
    # Defensive: only allow completion when the gates are satisfied. The
    # GUI shouldn't normally hit this branch (the Finish button is gated
    # in the wizard flow), but a misbehaving client deserves a clear 400
    # rather than a confused 'complete' state.
    if not state._has_required_gates():  # noqa: SLF001 — internal helper, intentional
        # Three-outcome: surface the missing gate as a hint.
        next_step = state.next_step
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "setup_incomplete",
                "next_step": next_step,
                "message": (
                    f"Setup not finishable yet — next step is '{next_step}'."
                ),
            },
        )
    mark_setup_complete(state)
    return SetupCompleteResponse(**_state_to_response(load_wizard_state()).model_dump())


# --------------------------------------------------------------------------
# Folder-import endpoints (round-18 / Stream A)
#
# PMs do not think in files; they think in project folders. The
# /setup/import-folder/{scan,POST,GET} trio lets the GUI render
# "we'll ingest these 47 files from <folder>; press Import" instead of
# forcing the user through a per-file picker.
# --------------------------------------------------------------------------


def _validate_folder_path(raw: str) -> Path:
    """Resolve ``raw`` to an existing directory or raise HTTP 400.

    Three failure modes — each surfaced with a distinct error code so the
    GUI can render specific guidance:

    * ``folder_not_found``     — path does not exist on disk.
    * ``folder_not_a_directory`` — path exists but is a regular file.
    * ``folder_access_denied`` — Errno 13 / PermissionError raised while
      probing existence (e.g. Windows-elevated parent directory).
    """
    candidate = Path(raw).expanduser()
    try:
        exists = candidate.exists()
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_access_denied",
                "message": f"Cannot access this folder — permission denied. ({exc})",
            },
        ) from exc
    except OSError as exc:
        # Cover other Errno-13-adjacent failures (Windows long-path,
        # network timeout on a UNC share, etc.).
        if getattr(exc, "errno", None) == 13:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "folder_access_denied",
                    "message": f"Cannot access this folder — permission denied. ({exc})",
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_not_found",
                "message": f"Cannot read this folder. ({exc})",
            },
        ) from exc
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_not_found",
                "message": f"Folder does not exist: {candidate}",
            },
        )
    if not candidate.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_not_a_directory",
                "message": (
                    f"Path is not a directory — pick a folder, not a file: {candidate}"
                ),
            },
        )
    return candidate


@wizard_router.post(
    "/import-folder/scan",
    response_model=FolderScanResponse,
    responses={
        400: {
            "description": (
                "folder_path missing, not a directory, or unreadable. The "
                "detail body's `error` field is one of folder_not_found, "
                "folder_not_a_directory, folder_access_denied."
            )
        },
    },
)
def setup_import_folder_scan(req: FolderScanRequest) -> FolderScanResponse:
    """Walk ``folder_path`` recursively and return a manifest.

    Pure scan — no DB writes, no LLM calls, no ingest. Reuses
    :func:`meridian.ingest.dispatcher.walk_directory` so the canonical
    extension-set lives next to ``ingest_file``.
    """
    folder = _validate_folder_path(req.folder_path)
    walked = walk_directory(folder)
    return FolderScanResponse(
        folder_path=walked.folder_path,
        folder_name=walked.folder_name,
        files_by_kind=walked.files_by_kind,
        skipped=[
            FolderSkipEntry(path=s.path, reason=s.reason)  # type: ignore[arg-type]
            for s in walked.skipped
        ],
        total_ingestable=walked.total_ingestable,
        # Alpha-11: pre-flight ODA detection so the GUI can surface "18
        # of your AutoCAD drawings won't import — install ODA File
        # Converter first" BEFORE the user hits Import. Probes the
        # filesystem only; never raises.
        oda=_oda_status_now(),
    )


@wizard_router.post(
    "/import-folder",
    response_model=ImportJobResponse,
    responses={
        400: {
            "description": (
                "folder_path is missing, not a directory, or unreadable; "
                "OR Idempotency-Key header is malformed (alpha-24)."
            ),
        },
    },
)
def setup_import_folder(
    req: FolderImportRequest, request: Request
) -> ImportJobResponse:
    """Walk ``folder_path`` and queue every supported file for ingestion.

    Auto-creates the project if it does not yet exist (the alpha-2 swapped
    step order asks for the folder BEFORE the project name; the folder name
    becomes the default project name and a project record is created here so
    a downstream rename on /setup/first-project remains optional). Idempotent
    — content_hash dedup is applied per-file in
    :func:`meridian.ingest.ingest_file`; calling this twice on the same
    folder produces a job whose ``deduped`` count equals the file count.
    """
    idempotency_key = _validate_idempotency_key(
        request.headers.get("Idempotency-Key")
    )

    # Idempotency gate — must happen BEFORE any side-effects (project
    # creation, job spawn) so that parallel POSTs with the same key
    # never race into create_project or walk_directory simultaneously.
    #
    # Two-stage strategy:
    #   1. Fast read-only check: if the key is already settled, return
    #      immediately with zero side-effects (the common case after the
    #      first POST has landed).
    #   2. Pre-claim with a candidate UUID: generate the UUID we WOULD
    #      use for the new job, then atomically claim the key. If another
    #      thread wins the claim, we return the winner's job_id without
    #      touching the DB. Only the winner proceeds to create the project
    #      and spawn the job. This closes the alpha-24 parallel-POST race
    #      where both threads passed the fast check simultaneously and then
    #      collided in create_project.
    candidate_id: str | None = None
    if idempotency_key is not None:
        existing = _idempotency_lookup(idempotency_key)
        if existing is not None:
            existing_job_id, age_seconds = existing
            _log.info(
                "wizard.import_folder.idempotent_replay",
                idempotency_token=idempotency_key,
                job_id=existing_job_id,
                age_seconds=round(age_seconds, 3),
            )
            return ImportJobResponse(job_id=existing_job_id)

        # Key not yet recorded — generate a candidate and claim the slot
        # atomically before any DB writes. The loser of the race (claim
        # returns a different ID) returns immediately without side-effects.
        candidate_id = str(uuid.uuid4())
        winner_id = _idempotency_claim(idempotency_key, candidate_id)
        if winner_id != candidate_id:
            _log.info(
                "wizard.import_folder.idempotent_race_loser",
                idempotency_token=idempotency_key,
                winner_job_id=winner_id,
                discarded_candidate=candidate_id,
            )
            return ImportJobResponse(job_id=winner_id)

    folder = _validate_folder_path(req.folder_path)
    slug = _slugify(req.project_name)
    db_path = project_db_path(slug)
    if not db_path.exists():
        # Side-effect: project is created on the fly. Mirrors the setup_create_project
        # path so the wizard state stays consistent (first_project_slug stamped).
        _project_id, db_path = create_project(name=req.project_name)
        state = load_wizard_state()
        mark_first_project(
            state,
            name=req.project_name,
            slug=slug,
            projects_dir=str(db_path.parent),
        )

    walked = walk_directory(folder)
    paths: list[str] = []
    for kind_paths in walked.files_by_kind.values():
        paths.extend(kind_paths)

    job = _ImportJob(total=len(paths))
    # If we pre-claimed an idempotency slot, stamp the job with the exact
    # UUID we registered so the caller can look it up by the right ID.
    if candidate_id is not None:
        job.id = candidate_id
    # Stamp db_path so suggest-name + setup_create_project can identify
    # this as wizard-owned staging — see _ImportJob.db_path docstring.
    job.db_path = db_path

    with _jobs_lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_import_job,
        kwargs={"job": job, "db_path": db_path, "paths": paths},
        daemon=True,
        name=f"wizard-folder-import-{job.id[:8]}",
    )
    thread.start()

    return ImportJobResponse(job_id=job.id)


@wizard_router.get(
    "/import-folder/{job_id}",
    response_model=FolderImportJobStatusResponse,
)
def setup_import_folder_status(job_id: str) -> FolderImportJobStatusResponse:
    """Poll a folder-import job. Same shape as ``/setup/import/{job_id}``
    plus a ``current_file`` field for in-flight progress."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    # Same persistence side-effect as /setup/import/{job_id}: on the first
    # poll that observes a 'succeeded' state, fold the import count into
    # the durable wizard state. Re-polls are no-ops.
    if job.status == "succeeded" and not job._persisted:
        if job.imported > 0:
            state = load_wizard_state()
            mark_documents_imported(state, count=job.imported)
        job._persisted = True

    return FolderImportJobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        total=job.total,
        completed=job.completed,
        imported=job.imported,
        deduped=job.deduped,
        # Legacy flat list — populated for one release of frontend
        # backward-compat. Will go away in alpha-13.
        failed=_legacy_error_strings(job.errors),
        failed_groups=_coalesce_errors(job.errors),
        current_file=job.current_file,
        # ODA availability re-checked at poll time so the GUI's
        # partial-success panel can offer "install ODA and re-scan" as
        # remediation without a separate probe round-trip.
        oda=_oda_status_now(),
    )


# --------------------------------------------------------------------------
# /setup/projects/suggest-name (round-18 / Stream A)
#
# When the GUI's folder picker fires, the wizard wants to suggest a project
# name = folder basename, slugified per ``meridian.projects._slugify`` so
# the suggestion matches what the SQLite filename will actually be. This
# endpoint also detects collision and bumps a numeric suffix until unique
# so the user is never offered a name that will 409 on create.
# --------------------------------------------------------------------------


@wizard_router.post(
    "/projects/suggest-name",
    response_model=SuggestNameResponse,
    responses={
        400: {"description": "folder_path is missing, not a directory, or unreadable."},
    },
)
def setup_suggest_project_name(req: SuggestNameRequest) -> SuggestNameResponse:
    """Suggest a slugified project name from a folder basename.

    Returns ``is_available=True`` when the naive (un-suffixed) slug is
    free, ``False`` when the server had to bump the suffix because a
    project at that slug already exists.

    Excludes the wizard's OWN in-flight staging DB from the collision
    check: the import-folder step creates a staging SQLite at the
    auto-derived slug, but that DB is going to be ADOPTED into whatever
    name the user picks at /setup/projects, so it's not a real
    collision from the user's perspective.

    A DB is considered "staging" (and therefore excluded) if and only if
    it was created by a /setup/import-folder job tracked in ``_jobs``.
    Projects confirmed via /setup/projects are NOT excluded — they are
    real collisions that must still cause the suffix bump.
    """
    folder = _validate_folder_path(req.folder_path)
    base = _slugify(folder.name)

    # Determine the set of DB paths that are wizard-owned staging files.
    # We use the _jobs registry (same predicate as setup_create_project's
    # _staging_created_by_import guard) rather than state.cli.first_project_slug
    # alone, because that field is also stamped by setup_create_project for
    # CONFIRMED projects and must not be exempted from collision detection.
    #
    # Build the resolved set ONCE under the lock (not inside the closure) to
    # avoid k×10000 Path.resolve() filesystem stats in the worst-case
    # suffix-bump loop.  db_path is declared in _ImportJob.__slots__ and set
    # in __init__, so direct attribute access is always safe.
    with _jobs_lock:
        staging_db_paths_resolved: frozenset[Path] = frozenset(
            j.db_path.resolve()
            for j in _jobs.values()
            if j.db_path is not None
        )

    def _slug_taken(slug: str) -> bool:
        db = project_db_path(slug)
        if not db.exists():
            return False
        # If this DB was created by import-folder and is tracked in _jobs,
        # it's the wizard's own staging file — not a real collision from the
        # user's perspective (it'll be adopted into whatever name they pick).
        if db.resolve() in staging_db_paths_resolved:
            return False
        return True

    if not _slug_taken(base):
        return SuggestNameResponse(suggested_name=base, is_available=True)

    # Bump suffix until unique. Cap at a sane number to avoid a runaway loop
    # if someone has 10k projects all sharing a basename — at that point the
    # GUI needs to surface the situation, not silently pick -10001.
    for n in range(2, 10001):
        candidate = f"{base}-{n}"
        if not _slug_taken(candidate):
            return SuggestNameResponse(suggested_name=candidate, is_available=False)

    # Pathological: the user has 10k+ collisions. Surface a clear error
    # rather than spin or fabricate. The same 400-shape pattern as
    # _validate_folder_path above.
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": "name_collision_exhausted",
            "message": (
                "Could not find a free slug after 10,000 attempts — "
                "pick a different folder name."
            ),
        },
    )


__all__ = ["wizard_router"]


# --------------------------------------------------------------------------
# Re-exports for tests — keeps test imports tidy.
# --------------------------------------------------------------------------

# OnboardingState surfaces here for symmetry with the CLI module's import
# path. Tests sometimes import it via meridian.wizard.api when they want to
# stay inside the wizard package's namespace.
_ = (OnboardingState, save_wizard_state)
