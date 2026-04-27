"""Crash-report send path (CONTEXT.md §19).

Pipeline:

1. Local crash dossier already exists on disk
   (built by :mod:`meridian.errors.crash_report`).
2. :func:`prepare_crash_payload` loads it and runs an additional defensive
   secret-redaction pass — better to over-redact than leak. Adds tool
   version + OS fields.
3. :func:`send_crash_report` POSTs the payload, but **only** if the
   :func:`get_crash_opt_in` flag is True. Wraps every network exception
   into a :class:`SendResult` rather than raising — the host CLI must
   never crash trying to report a crash.

The opt-in flag lives in ``<projects_dir>/_meridian/crash_opt_in.json`` so
it is process-wide (a crash can happen with no project open). It is **off
by default** — the user must explicitly run ``meridian crash opt-in
--enable``.

Pure stdlib (``urllib.request``).
"""

from __future__ import annotations

import json
import platform
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meridian.config import settings
from meridian.logging import get_logger

_log = get_logger("meridian.crash")


# ---------------------------------------------------------------------------
# Placeholders — REPLACE before shipping a real release.
# Search the repo for ``_PLACEHOLDER_`` to find every deferred wire-up site.
# ---------------------------------------------------------------------------

# DEFERRED §3.6: replace with real endpoint when chosen.
# Recommendation in OVERNIGHT_REPORT §3.6 is "(C) Direct email via a
# serverless SMTP function" for v1, with a Cloudflare Worker as the most
# likely shape. Until decided, the placeholder URL points to the IANA-
# reserved ``invalid`` TLD so any accidental send resolves to nothing.
_PLACEHOLDER_CRASH_ENDPOINT: str = "https://crash.meridian.invalid/v1/report"

_DEFAULT_TIMEOUT_S = 15
_USER_AGENT = "meridian-crash/1"

# Filenames under <projects_dir>/_meridian/.
_OPT_IN_FILENAME = "crash_opt_in.json"

# How far back to look for crash dossier files.
# crash_report.write_crash_report writes them as
# <log_dir>/crash_<UTC-iso>.json — so we glob ``crash_*.json`` under the
# global + per-project log directories.
_CRASH_FILENAME_GLOB = "crash_*.json"


# ---------------------------------------------------------------------------
# Defensive secret-redaction (over-redact rather than leak).
# The dossier was already redacted by build_error_context, but this is the
# last line of defence before bytes hit the network.
# ---------------------------------------------------------------------------

_REDACTION = "[REDACTED]"

# Common API-key shapes.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),                  # Anthropic
    re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_\-]{16,}"),           # OpenAI / generic sk-
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),         # Bearer tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key ID
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),                       # AWS STS key ID
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),                     # GitHub PAT
    re.compile(r"\bxox[abp]-[A-Za-z0-9\-]{10,}"),              # Slack tokens
    # KEY=value forms for common credential env vars.
    re.compile(
        r"((?:AWS_(?:ACCESS|SECRET)_(?:KEY_ID|ACCESS_KEY)|"
        r"ANTHROPIC_API_KEY|OPENAI_API_KEY|"
        r"GOOGLE_API_KEY|AZURE_OPENAI_API_KEY|"
        r"aws_access_key_id|aws_secret_access_key))"
        r"\s*[:=]\s*[\S]+"
    ),
]


def _redact_string(value: str) -> str:
    """Run every pattern over a string. KEY=value patterns keep the key."""
    out = value
    # The credential KEY=value pattern is the last in the list and uses a
    # capture group — we want to preserve the key name. Run it with a
    # group-aware substitution; run the others as plain replacements.
    for pat in _PATTERNS[:-1]:
        out = pat.sub(_REDACTION, out)
    out = _PATTERNS[-1].sub(rf"\1={_REDACTION}", out)
    return out


def _redact(obj: Any) -> Any:
    """Recursively redact strings inside dict/list/tuple."""
    if isinstance(obj, str):
        return _redact_string(obj)
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v) for v in obj)
    return obj


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SendResult:
    """Outcome of :func:`send_crash_report`. Never raises — always returned.

    - ``success`` True → the endpoint accepted (HTTP 2xx).
    - ``status_code`` is None when the request never reached the server
      (DNS failure, timeout, opt-in disabled).
    - ``message`` is human-readable; safe to print to the CLI.
    """

    success: bool
    status_code: int | None
    message: str


# ---------------------------------------------------------------------------
# Path helpers + opt-in flag
# ---------------------------------------------------------------------------


def _meridian_dir() -> Path:
    return settings.projects_dir / "_meridian"


def crash_opt_in_path() -> Path:
    """Where the opt-in flag is persisted (process-wide)."""
    return _meridian_dir() / _OPT_IN_FILENAME


def get_crash_opt_in() -> bool:
    """Read the opt-in flag. Default: False (must be enabled explicitly)."""
    path = crash_opt_in_path()
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("crash_reporting_enabled", False))


def set_crash_opt_in(enabled: bool) -> Path:
    """Persist the opt-in flag, returning the path written."""
    path = crash_opt_in_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "crash_reporting_enabled": bool(enabled),
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _log.info("crash.opt_in_changed", enabled=bool(enabled))
    return path


# ---------------------------------------------------------------------------
# Local dossier discovery
# ---------------------------------------------------------------------------


def list_local_dossiers() -> list[Path]:
    """Return all local ``crash_*.json`` files, newest first.

    Looks under ``<projects_dir>/_global.logs/`` and every
    ``<projects_dir>/<slug>.logs/`` directory — see
    :mod:`meridian.logging.setup` for where the writer puts them.
    """
    base = settings.projects_dir
    if not base.exists():
        return []
    out: list[Path] = []
    for log_dir in base.glob("*.logs"):
        if log_dir.is_dir():
            out.extend(log_dir.glob(_CRASH_FILENAME_GLOB))
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------


def _tool_version() -> str:
    """Best-effort tool version. Mirrors meridian.evidence.builder._tool_version."""
    pyproject = settings.project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    return settings.app_version


def prepare_crash_payload(dossier_path: Path) -> dict[str, Any]:
    """Load a dossier file, redact, and bundle for sending.

    Adds ``tool_version``, ``os_info``, and ``payload_version`` so the
    receiving service can route old-shape reports without ambiguity.

    Raises ``FileNotFoundError`` if the dossier is missing,
    ``json.JSONDecodeError`` if it isn't valid JSON. (These are programmer
    errors at this layer — :func:`send_crash_report` catches them.)
    """
    dossier_path = Path(dossier_path)
    text = dossier_path.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(
            f"Crash dossier must be a JSON object, got {type(raw).__name__}"
        )

    redacted = _redact(raw)

    payload = {
        "payload_version": 1,
        "sent_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": _tool_version(),
        "os_info": {
            "platform": sys.platform,
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_version": sys.version.split()[0],
        },
        "dossier": redacted,
        "dossier_filename": dossier_path.name,
    }
    return payload


# ---------------------------------------------------------------------------
# Network send
# ---------------------------------------------------------------------------


def send_crash_report(
    dossier_path: Path,
    *,
    endpoint_url: str | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    dry_run: bool = False,
) -> SendResult:
    """POST a redacted payload to the crash endpoint.

    Refuses to send when the opt-in flag is False. Refuses to send when
    ``endpoint_url`` resolves to the placeholder URL **and** ``dry_run`` is
    False — without a real endpoint there's no meaningful network call to
    make. Use ``dry_run=True`` to exercise the full pipeline (assembly +
    redaction) without touching the network.

    Never raises. Network errors → ``SendResult(success=False, ...)``.
    """
    # ---- Opt-in gate ----
    if not get_crash_opt_in():
        return SendResult(
            success=False,
            status_code=None,
            message=(
                "Crash reporting is disabled. Run "
                "'meridian crash opt-in --enable' to turn it on; you can "
                "preview any payload first with 'meridian crash preview'."
            ),
        )

    # ---- Build payload (catches dossier read/parse errors) ----
    try:
        payload = prepare_crash_payload(dossier_path)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as exc:
        return SendResult(
            success=False,
            status_code=None,
            message=f"Could not prepare crash payload: {exc}",
        )

    body = json.dumps(payload).encode("utf-8")
    url = endpoint_url or _PLACEHOLDER_CRASH_ENDPOINT

    if dry_run:
        return SendResult(
            success=True,
            status_code=None,
            message=(
                f"Dry run — would POST {len(body)} bytes to {url}. "
                "No network call made."
            ),
        )

    if url == _PLACEHOLDER_CRASH_ENDPOINT:
        # Refuse to send to the placeholder — easy way to leak in
        # development. Make the failure mode loud.
        return SendResult(
            success=False,
            status_code=None,
            message=(
                "Crash endpoint URL is still the placeholder "
                f"({_PLACEHOLDER_CRASH_ENDPOINT}). Wire up the real "
                "endpoint (CONTEXT.md §3.6) or pass --dry-run."
            ),
        )

    req = urllib.request.Request(  # noqa: S310 — caller-controlled URL
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            status = int(resp.status)
            ok = 200 <= status < 300
            _log.info(
                "crash.sent",
                url=url,
                status=status,
                bytes=len(body),
                dossier=str(dossier_path),
            )
            return SendResult(
                success=ok,
                status_code=status,
                message=(
                    f"Endpoint accepted the report (HTTP {status})."
                    if ok
                    else f"Endpoint returned HTTP {status}."
                ),
            )
    except urllib.error.HTTPError as exc:
        _log.info("crash.http_error", url=url, status=exc.code)
        return SendResult(
            success=False,
            status_code=exc.code,
            message=f"Endpoint returned HTTP {exc.code}: {exc.reason}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _log.info("crash.network_error", url=url, error=str(exc))
        return SendResult(
            success=False,
            status_code=None,
            message=f"Could not reach the crash endpoint: {exc}",
        )


__all__ = [
    "SendResult",
    "crash_opt_in_path",
    "get_crash_opt_in",
    "list_local_dossiers",
    "prepare_crash_payload",
    "send_crash_report",
    "set_crash_opt_in",
]
