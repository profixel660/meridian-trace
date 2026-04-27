"""API-side TOTP login endpoints.

Mirrors the CLI auth flow (`meridian auth verify`) over HTTP so the Next.js
shell — and any other API client — can mint a session bearer without
shelling out to typer.

DECISION POINT (deferred — see ``OVERNIGHT_REPORT.md`` §3.2 + §3.3):
This module DEFINES the login endpoint but does NOT enforce
``require_session`` on any other route. Wiring enforcement onto existing
project / ingest / extract handlers is a separate operator decision; until
then ``/auth/whoami`` is the only endpoint here that actually requires
authentication.

Wire-up: in ``src/meridian/api/main.py`` add::

    from meridian.auth.login_api import auth_router
    app.include_router(auth_router)

Rate limiting is intentionally minimal — a module-level in-memory bucket
keyed by ``request.client.host``. Meridian is a single-process app per
CONTEXT.md; if/when that changes the bucket needs to move to Redis or the
sessions DB.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from meridian.auth.fastapi_dep import require_session
from meridian.auth.recovery import verify_recovery_code
from meridian.auth.secrets import default_store, get_recovery_salt
from meridian.auth.session import issue_session, revoke_session, verify_session
from meridian.auth.totp import verify_totp
from meridian.logging import get_logger

# --------------------------------------------------------------------------
# Rate limiting — in-memory token bucket, 10 attempts per 5 minutes per IP.
# --------------------------------------------------------------------------

_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
_RATE_LIMIT_MAX_ATTEMPTS = 10

# Module-level state. Per CONTEXT.md the API is a single-process app; this
# is the simplest correct thing. Replace with Redis if/when we go multi-
# process. The lock guards both reads and writes so concurrent uvicorn
# workers on the same process don't race.
_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = Lock()

_log = get_logger("meridian.auth.login")

# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Login payload — exactly one of ``totp_code`` / ``recovery_code``."""

    totp_code: str | None = Field(
        default=None,
        description="6-digit TOTP code from the user's authenticator app.",
    )
    recovery_code: str | None = Field(
        default=None,
        description=(
            "One-time recovery code in ``ABCD-EFGH-IJKL`` form (whitespace "
            "and case-insensitive). Burned on use."
        ),
    )


class LoginResponse(BaseModel):
    bearer_token: str = Field(
        description=(
            "Opaque signed session token — pass back as "
            "``Authorization: Bearer <token>`` on subsequent calls."
        ),
    )
    expires_at: str = Field(
        description="Token expiry as an ISO-8601 UTC timestamp (``Z`` suffix).",
    )


class LogoutResponse(BaseModel):
    revoked: bool = Field(
        description=(
            "True if a live session was revoked. False if the token was "
            "already revoked, expired, malformed, or absent — the call is "
            "idempotent and never errors on a missing token."
        ),
    )


class WhoAmIResponse(BaseModel):
    authenticated: bool = Field(
        description="Always true when this endpoint returns 200 (gated by require_session).",
    )
    expires_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC expiry of the bearer token, if known.",
    )
    mins_remaining: int | None = Field(
        default=None,
        description="Whole minutes until the token expires, floored at zero.",
    )


class StatusResponse(BaseModel):
    enrolled: bool = Field(
        description="True if a TOTP secret has been provisioned on this install.",
    )
    totp_required: bool = Field(
        description=(
            "True if API clients should obtain a session via ``/auth/login`` "
            "before calling protected endpoints. Mirrors ``enrolled`` until "
            "the deferred enforcement decision (§3.2) lands."
        ),
    )


class ErrorResponse(BaseModel):
    detail: str = Field(description="Human-readable error message.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Best-effort source IP for rate-limit bucketing.

    ``request.client`` can be ``None`` for ASGI test clients / lifespan
    paths; bucket those under a sentinel so tests still exercise the limiter.
    """
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed; False if the bucket is full.

    Sliding-window over the last ``_RATE_LIMIT_WINDOW_SECONDS`` seconds.
    """
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, deque())
        # Drop expired attempts from the left.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_ATTEMPTS:
            return False
        bucket.append(now)
        return True


def _isoformat(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _constant_time_fail(ip: str, reason: str) -> HTTPException:
    """Build the single 401 we return for every failure mode.

    We log the *internal* reason (format-invalid vs wrong-value vs not-
    enrolled) for ops, but the wire response is a single opaque "Invalid
    credentials." regardless — clients must not be able to distinguish
    these states.
    """
    _log.warning("auth.login.failure", ip=ip, reason=reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_session_id(token: str) -> str | None:
    """Pull the session-id segment out of a signed bearer token.

    The token format is ``<session_id>.<expiry>.<sig>`` (see
    ``meridian.auth.session.issue_session``). Returns ``None`` if the token
    is malformed.
    """
    if not token or token.count(".") != 2:
        return None
    return token.split(".", 1)[0]


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials."},
        429: {
            "model": ErrorResponse,
            "description": "Too many login attempts from this IP.",
        },
    },
)
def login(payload: LoginRequest, request: Request) -> LoginResponse:
    """Exchange a TOTP code or recovery code for a session bearer token.

    Exactly one of ``totp_code`` / ``recovery_code`` must be supplied.
    Failure modes (missing field, both supplied, format-invalid, wrong
    value, not enrolled) all return the same opaque 401.
    """
    ip = _client_ip(request)

    if not _check_rate_limit(ip):
        _log.warning("auth.login.rate_limited", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many login attempts. Try again in "
                f"{_RATE_LIMIT_WINDOW_SECONDS // 60} minutes."
            ),
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
        )

    # Exactly-one validation — both branches collapse into the same 401 so
    # the caller cannot distinguish "you sent both" from "wrong value".
    has_totp = bool(payload.totp_code)
    has_recovery = bool(payload.recovery_code)
    if has_totp == has_recovery:  # both true OR both false
        raise _constant_time_fail(ip, reason="missing_or_ambiguous_credential")

    store = default_store()
    data = store.load()
    if data is None:
        # Not enrolled — same 401 to avoid revealing enrolment state to a
        # network attacker. ``/auth/status`` is the discoverable way to
        # learn enrolment state and is intentionally separate.
        raise _constant_time_fail(ip, reason="not_enrolled")

    secret = data.get("secret")
    if not isinstance(secret, str) or not secret:
        raise _constant_time_fail(ip, reason="store_corrupt")

    # --- TOTP path -----------------------------------------------------
    if has_totp:
        assert payload.totp_code is not None  # for type-checker
        if not verify_totp(secret, payload.totp_code):
            raise _constant_time_fail(ip, reason="totp_mismatch")
        session_id, token = issue_session()
        expires_at = _expiry_from_token(token)
        _log.info(
            "auth.login.success",
            ip=ip,
            method="totp",
            session_id=session_id,
        )
        return LoginResponse(bearer_token=token, expires_at=expires_at)

    # --- Recovery-code path -------------------------------------------
    assert payload.recovery_code is not None
    salt = get_recovery_salt()
    matched, remaining = verify_recovery_code(
        data.get("recovery_codes_hashed", []),
        payload.recovery_code,
        salt=salt,
    )
    if not matched:
        raise _constant_time_fail(ip, reason="recovery_mismatch")

    # Burn the used recovery code by writing the trimmed list back.
    if hasattr(store, "update_recovery_codes"):
        store.update_recovery_codes(remaining)

    session_id, token = issue_session()
    expires_at = _expiry_from_token(token)
    _log.info(
        "auth.login.success",
        ip=ip,
        method="recovery",
        session_id=session_id,
        recovery_codes_remaining=len(remaining),
    )
    return LoginResponse(bearer_token=token, expires_at=expires_at)


def _expiry_from_token(token: str) -> str:
    """Decode the expiry epoch baked into a freshly-minted token."""
    # Format: <session_id>.<expiry>.<sig> — see session.issue_session.
    try:
        _, expiry_str, _ = token.split(".")
        return _isoformat(int(expiry_str))
    except (ValueError, IndexError):
        # Should be unreachable for tokens we just minted; fall back to "now".
        return _isoformat(int(time.time()))


@auth_router.post(
    "/logout",
    response_model=LogoutResponse,
)
def logout(
    authorization: Annotated[str | None, Header()] = None,
) -> LogoutResponse:
    """Revoke the bearer token in the ``Authorization`` header.

    Idempotent: a missing, malformed, expired, or already-revoked token
    returns ``{revoked: false}`` rather than an error so well-behaved
    clients can call this on every logout-button click without conditionals.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return LogoutResponse(revoked=False)
    token = authorization.split(" ", 1)[1].strip()

    # Only count a true revoke if the token was actually live; otherwise the
    # call is a no-op and ``revoked`` stays false. We check verify_session
    # FIRST so an attacker can't use this endpoint as an oracle for "is this
    # token shape valid?" — verify_session is constant-time.
    was_live = verify_session(token)
    session_id = _extract_session_id(token)
    if session_id is not None:
        # revoke_session is itself a no-op if the id isn't present, so this
        # is safe to call even when ``was_live`` is False.
        revoke_session(session_id)
    _log.info("auth.logout", revoked=was_live)
    return LogoutResponse(revoked=was_live)


@auth_router.get(
    "/whoami",
    response_model=WhoAmIResponse,
    dependencies=[Depends(require_session)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token."},
    },
)
def whoami(
    authorization: Annotated[str | None, Header()] = None,
) -> WhoAmIResponse:
    """Return token-liveness metadata for the caller's bearer token.

    Reaching this handler means ``require_session`` already accepted the
    token; we re-parse it here purely to surface the expiry to the client
    for "session expires in N minutes" UI affordances.
    """
    # Parse out the expiry — we know this succeeds because require_session
    # already validated the signature and shape, but defend against the
    # impossible case anyway so we never 500.
    expires_at: str | None = None
    mins_remaining: int | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token.count(".") == 2:
            try:
                _, expiry_str, _ = token.split(".")
                expiry_epoch = int(expiry_str)
                expires_at = _isoformat(expiry_epoch)
                remaining = max(0, expiry_epoch - int(time.time()))
                mins_remaining = remaining // 60
            except ValueError:
                pass
    return WhoAmIResponse(
        authenticated=True,
        expires_at=expires_at,
        mins_remaining=mins_remaining,
    )


@auth_router.get(
    "/status",
    response_model=StatusResponse,
)
def status_() -> StatusResponse:
    """Public probe: has TOTP been enrolled on this install?

    Called by the web shell on first load to decide whether to render the
    login form vs the enrolment-required empty state. Intentionally NOT
    behind ``require_session`` — it's the discoverability hook for the
    whole auth flow.
    """
    enrolled = default_store().is_configured()
    return StatusResponse(
        enrolled=enrolled,
        # Mirrors `enrolled` for now. Once the §3.2 enforcement decision
        # lands this becomes a config flag (`settings.auth.enforce_api`).
        totp_required=enrolled,
    )


__all__ = [
    "LoginRequest",
    "LoginResponse",
    "LogoutResponse",
    "StatusResponse",
    "WhoAmIResponse",
    "auth_router",
]
