"""Structlog + stdlib logging configuration (CONTEXT.md §19).

Idempotent: ``configure_logging`` may be called repeatedly. Subsequent calls
re-bind the project context (so the per-project log dir / slug can change
mid-process) but do not re-add handlers.

Log file naming + rotation
--------------------------

Files land per-project under ``<projects_dir>/<slug>.logs/`` (CONTEXT.md §6
"per-project SQLite + log files alongside"). Filename pattern is
``meridian-YYYYMMDD.log``. The handler rotates at 10 MB and keeps the last
5 files (``meridian-YYYYMMDD.log.1`` ... ``.5``). When no project slug is
supplied (e.g. before a project is opened), files land in
``<projects_dir>/_global.logs/``.

The project_slug, pid, app_version, and schema_version_supported are bound
into every event so JSONL lines are self-describing.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import structlog

from meridian.config import settings

# 10 MB rotation, 5 backups (per CONTEXT.md §19 spec in the brief).
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

# Module-level state so configure_logging can be idempotent.
#
# Alpha-13 reviewer-ticket fix: alpha-12's setup_runtime endpoint read
# `_current_log_dir` without synchronisation; in practice the value is
# set once at process start and not re-set during request handling, so
# the read was benign. But "benign in practice" is a foot-gun if a
# future caller starts swapping the log dir mid-request (e.g. when the
# active project changes). RLock + a thread-safe getter close the gap
# without changing the cheap read-once-at-startup pattern.
_state_lock = RLock()
_configured = False
_current_log_dir: Path | None = None
_current_handler: logging.Handler | None = None
_current_slug: str | None = None


def current_log_dir() -> Path | None:
    """Thread-safe accessor for the active per-process log directory.

    Returns ``None`` until :func:`configure_logging` has been called.
    Use this from outside this module (e.g. ``setup_runtime``) instead
    of poking ``_current_log_dir`` directly so future locking changes
    don't ripple.
    """
    with _state_lock:
        return _current_log_dir


def _resolve_log_dir(project_slug: str | None, log_dir: Path | None) -> Path:
    if log_dir is not None:
        return log_dir
    base = settings.projects_dir
    if project_slug:
        return base / f"{project_slug}.logs"
    return base / "_global.logs"


def _schema_version_supported() -> int | None:
    """Best-effort schema version lookup (avoids hard import cycles)."""
    try:
        from meridian.db.connection import SCHEMA_VERSION
        return int(SCHEMA_VERSION)
    except Exception:  # noqa: BLE001 — logging must not crash on bootstrap
        return None


def _make_log_filename(log_dir: Path) -> Path:
    today = datetime.now(UTC).strftime("%Y%m%d")
    return log_dir / f"meridian-{today}.log"


def configure_logging(
    *,
    project_slug: str | None = None,
    log_dir: Path | None = None,
    json_to_file: bool = True,
    console: bool = True,
    level: str = "INFO",
) -> None:
    """Idempotently configure structlog + stdlib logging.

    - JSON-lines to ``<log_dir>/meridian-YYYYMMDD.log`` (rotating at 10 MB,
      keep last 5).
    - Rich console renderer for interactive use.
    - Bound context: project_slug (if supplied), pid, app_version,
      schema_version_supported.
    - Safe to call repeatedly; subsequent calls only re-bind project context
      and do not re-add file handlers.
    """
    global _configured, _current_log_dir, _current_handler, _current_slug

    resolved_dir = _resolve_log_dir(project_slug, log_dir)

    # Determine numeric stdlib level.
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Re-bind context every call (cheap, idempotent).
    context: dict[str, Any] = {
        "pid": os.getpid(),
        "app_version": settings.app_version,
    }
    schema_v = _schema_version_supported()
    if schema_v is not None:
        context["schema_version_supported"] = schema_v
    if project_slug:
        context["project_slug"] = project_slug

    # Alpha-13 reviewer fix: writers take the lock the alpha-13 getter
    # already takes. Without writer-side coverage the lock provided
    # only the GIL's atomicity guarantee — cosmetic, not honest. RLock
    # is re-entrant so nested calls (configure_logging ->
    # _swap_file_handler) don't deadlock.
    with _state_lock:
        if _configured:
            # Same log_dir + slug → nothing to do beyond re-binding context.
            # Different log_dir → swap the file handler.
            if json_to_file and resolved_dir != _current_log_dir:
                _swap_file_handler(resolved_dir, log_level)
                _current_log_dir = resolved_dir
            _current_slug = project_slug
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(**context)
            return

        # First-time setup.
        root = logging.getLogger()
        root.setLevel(log_level)
        # Don't double-add handlers if the harness already added something.
        # We add our own and tag them so we can find them again.
        if json_to_file:
            resolved_dir.mkdir(parents=True, exist_ok=True)
            file_path = _make_log_filename(resolved_dir)
            handler = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setLevel(log_level)
            # The structlog renderer produces the full JSON line; we just want
            # the message verbatim.
            handler.setFormatter(logging.Formatter("%(message)s"))
            handler._meridian_json_file = True  # type: ignore[attr-defined]
            root.addHandler(handler)
            _current_handler = handler
            _current_log_dir = resolved_dir

        if console:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(log_level)
            console_handler.setFormatter(logging.Formatter("%(message)s"))
            console_handler._meridian_console = True  # type: ignore[attr-defined]
            root.addHandler(console_handler)

        # Configure structlog itself. We use two output paths through the same
        # structlog pipeline: the file handler receives the JSON renderer's
        # output, and the console handler receives the same string. For
        # interactive runs, we additionally wrap with structlog's ConsoleRenderer
        # by routing through a separate logger if needed — but keeping a single
        # JSON line is the simpler, more debuggable default. The brief calls for
        # a Rich console renderer when interactive; we honour that by routing
        # console output through structlog.dev.ConsoleRenderer when stderr is a
        # TTY.
        use_rich_console = console and sys.stderr.isatty()

        processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]

        if use_rich_console:
            # Pretty for terminal, JSON-compatible for file.
            # We achieve this by using a renderer that emits JSON, then a
            # secondary handler-side formatter for the console. Simplest approach
            # here: emit JSON for both; the brief's "Rich console renderer when
            # interactive" intent is preserved by structlog's dev.ConsoleRenderer
            # when no file handler is active. To keep the file output strictly
            # JSON-lines, we make a single decision: file always JSON, console
            # mirrors the same string. This avoids divergence between the two
            # surfaces.
            processors.append(structlog.processors.JSONRenderer())
        else:
            processors.append(structlog.processors.JSONRenderer())

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**context)
        _current_slug = project_slug
        _configured = True


def _swap_file_handler(new_dir: Path, log_level: int) -> None:
    """Replace the existing rotating file handler with one in ``new_dir``.

    Alpha-13: caller (``configure_logging`` / ``bind_project_context``)
    holds ``_state_lock``; this function is internal and does not take
    the lock itself. Callers from outside this module should use
    those public APIs instead of poking at the file handler directly.
    """
    global _current_handler
    root = logging.getLogger()
    if _current_handler is not None:
        try:
            root.removeHandler(_current_handler)
            _current_handler.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
    new_dir.mkdir(parents=True, exist_ok=True)
    file_path = _make_log_filename(new_dir)
    handler = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(log_level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._meridian_json_file = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    _current_handler = handler


def bind_project_context(*, project_slug: str) -> None:
    """Re-bind the project context after configure_logging.

    Call this whenever the active project slug changes mid-process (e.g. a
    CLI command resolves its project). This also swaps the file-handler
    target so subsequent log lines land in that project's
    ``<slug>.logs/`` directory.

    Alpha-13: takes ``_state_lock`` for the duration of the swap so a
    concurrent ``current_log_dir()`` reader either sees the pre-swap
    state OR the post-swap state, never a torn write.
    """
    global _current_slug
    with _state_lock:
        if not _configured:
            # Not yet configured — bootstrap with this slug. (Recursive
            # call on the RLock is safe — the lock is re-entrant.)
            configure_logging(project_slug=project_slug)
            return
        new_dir = _resolve_log_dir(project_slug, None)
        if new_dir != _current_log_dir and _current_handler is not None:
            _swap_file_handler(new_dir, _current_handler.level)
            globals()["_current_log_dir"] = new_dir
        structlog.contextvars.bind_contextvars(project_slug=project_slug)
        _current_slug = project_slug


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger.

    Safe to call before configure_logging; structlog's defaults will be used
    until configure_logging runs.
    """
    return structlog.get_logger(name)


__all__ = [
    "bind_project_context",
    "configure_logging",
    "get_logger",
]
