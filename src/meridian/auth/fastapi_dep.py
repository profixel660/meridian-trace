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
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from meridian.auth.session import verify_session


def require_session(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Raise 401 unless the request carries a valid session token.

    Expected header form::

        Authorization: Bearer <token>

    Tokens are minted by :func:`meridian.auth.session.issue_session` after
    a successful TOTP verification.
    """
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


__all__ = ["require_session"]
