"""FastAPI dependency for TOTP-backed session auth.

DECISION POINT — this dependency is **not** wired into any existing route.

Whether to apply ``require_session`` globally (e.g. via
``app = FastAPI(..., dependencies=[Depends(require_session)])`` in
``src/meridian/api/main.py``) or per-route (sprinkling
``Depends(require_session)`` on individual handlers) is an operator
decision:

* **Global** — matches CONTEXT.md §16's intent (per-session lock on the
  whole API) but immediately breaks any local automation / scripts the
  user may already be running against the API. Recommended once those
  scripts have been updated to obtain a session token via
  ``meridian auth enroll`` + ``meridian auth verify``.

* **Per-route** — start with the obviously-sensitive handlers (project
  create, ingest, export, anything with side effects) and leave read-only
  introspection unauthenticated for an interim period.

* **Off, plus license-gate only** — CONTEXT.md §18 designates the
  license check as the real auth control; TOTP is a per-session
  convenience lock. If the license gate is enforced elsewhere it may be
  acceptable to leave the API open on localhost-only deployments.

The recommended default once the user has decided is global enforcement
on any non-localhost binding, per-route on localhost-only.

ALPHA-13 DEBUG BYPASS
---------------------

When the ``MERIDIAN_AUTH_DISABLED`` environment variable is set to
``"1"``, ``require_session`` short-circuits to a no-op. This lets an
SME walk the post-setup surface during alpha debugging without enrolling
TOTP. Production deployments MUST NOT set this var; ``api/main.py``
emits a loud WARNING at startup when the bypass is active so an
operator who deploys with it enabled cannot miss the leak.

The check happens at REQUEST time (not module-import time) so the var
can be toggled live by editing ``C:\\Meridian\\.env`` and restarting the
backend — no rebuild required. ``/setup/runtime`` exposes the current
state via ``auth_disabled`` so the frontend can render a debug-mode
banner.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Header, HTTPException, status

from meridian.auth.session import verify_session

# Public sentinel — the env var name in one place so callers can probe
# the same string without typo risk.
AUTH_DISABLED_ENV_VAR = "MERIDIAN_AUTH_DISABLED"


def auth_disabled() -> bool:
    """Return True iff the alpha-13 debug bypass is currently active.

    Read from the env at call time, NOT cached, so an operator can flip
    the var live by editing ``.env`` + restarting (or even
    ``$env:MERIDIAN_AUTH_DISABLED='1'`` in the running shell on the
    test backend's process tree, since uvicorn inherits parent env).
    """
    return os.environ.get(AUTH_DISABLED_ENV_VAR) == "1"


def require_session(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Raise 401 unless the request carries a valid session token.

    Expected header form::

        Authorization: Bearer <token>

    Tokens are minted by :func:`meridian.auth.session.issue_session` after
    a successful TOTP verification.

    Honours the alpha-13 ``MERIDIAN_AUTH_DISABLED=1`` env var as a
    no-op short-circuit. See module docstring.
    """
    if auth_disabled():
        # Debug mode. The startup warning in api/main.py covers the
        # "operator forgot to disable this in prod" case.
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>').",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not verify_session(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token invalid, expired, or revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )


__all__ = ["AUTH_DISABLED_ENV_VAR", "auth_disabled", "require_session"]
