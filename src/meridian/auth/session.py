"""Signed-token session management.

A session is a short opaque ID plus an HMAC-SHA256 signature over
``session_id || expiry_epoch``. The signing key is per-install (auto-
generated on first use) and stored alongside other auth state at
``<projects_dir>/_auth/session_key``.

On-disk session metadata is intentionally tiny: a JSON ``{session_id:
expiry_epoch}`` dict at ``<projects_dir>/_auth/sessions.json`` for the sole
purpose of supporting :func:`revoke_session`. The token itself is self-
verifying (signature + expiry encoded inside it), so a missing sessions
file means "all sessions implicitly revoked" — fail-closed.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
from hashlib import sha256
from pathlib import Path

from meridian.config import settings

_AUTH_DIR_NAME = "_auth"
_SESSIONS_FILE = "sessions.json"
_KEY_FILE = "session_key"


def _auth_dir() -> Path:
    p = settings.projects_dir / _AUTH_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _signing_key() -> bytes:
    key_path = _auth_dir() / _KEY_FILE
    if not key_path.exists():
        key_path.write_bytes(secrets.token_bytes(32))
        _restrict_perms(key_path)
    return key_path.read_bytes()


def _restrict_perms(path: Path) -> None:
    """Best-effort 0600 on POSIX; no-op on Windows (NTFS ACLs left default).

    See ``secrets.py`` for the full discussion of why we don't try to set
    Windows ACLs from stdlib alone.
    """
    import contextlib
    import os

    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(path, 0o600)


def _load_sessions() -> dict[str, int]:
    p = _auth_dir() / _SESSIONS_FILE
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text("utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}


def _save_sessions(data: dict[str, int]) -> None:
    p = _auth_dir() / _SESSIONS_FILE
    p.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    _restrict_perms(p)


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def _sign(session_id: str, expiry_epoch: int) -> bytes:
    msg = f"{session_id}.{expiry_epoch}".encode("ascii")
    return hmac.new(_signing_key(), msg, sha256).digest()


def issue_session(*, valid_for_seconds: int = 8 * 3600) -> tuple[str, str]:
    """Mint a new session.

    Returns ``(session_id, signed_token)``. The ``session_id`` is opaque to
    the caller — keep it if you intend to revoke this specific session
    later. The ``signed_token`` is what goes in the ``Authorization: Bearer``
    header.
    """
    session_id = _b64e(secrets.token_bytes(16))
    expiry = int(time.time()) + int(valid_for_seconds)
    sigs = _sign(session_id, expiry)
    token = f"{session_id}.{expiry}.{_b64e(sigs)}"
    sessions = _load_sessions()
    sessions[session_id] = expiry
    _save_sessions(sessions)
    return session_id, token


def verify_session(token: str) -> bool:
    """Verify signature + expiry + revocation status."""
    if not token or token.count(".") != 2:
        return False
    session_id, expiry_str, sig_b64 = token.split(".")
    try:
        expiry = int(expiry_str)
        provided_sig = _b64d(sig_b64)
    except ValueError:
        return False
    expected = _sign(session_id, expiry)
    if not hmac.compare_digest(expected, provided_sig):
        return False
    if time.time() > expiry:
        return False
    sessions = _load_sessions()
    return session_id in sessions


def revoke_session(session_id: str) -> None:
    """Drop a session id from the persisted map. No-op if absent."""
    sessions = _load_sessions()
    if session_id in sessions:
        del sessions[session_id]
        _save_sessions(sessions)


def revoke_all_sessions() -> int:
    """Drop every session. Returns the count revoked."""
    sessions = _load_sessions()
    n = len(sessions)
    _save_sessions({})
    return n


__all__ = [
    "issue_session",
    "revoke_all_sessions",
    "revoke_session",
    "verify_session",
]
