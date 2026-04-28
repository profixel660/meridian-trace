"""Alpha-21 regression gate: wizard endpoints reachable from BOTH the
frontend's /api/setup/... path AND the legacy /setup/... path used by
Status-Meridian.bat and the release gauntlet.

Background. Alpha-20 moved the JSON API under ``/api`` to disambiguate
the SPA URL space from the API URL space. The frontend's ``apiFetch``
prepends ``API_BASE = "/api"`` to every call, including wizard calls
like ``POST /setup/api-key``. The wizard router was still mounted at
the bare ``/setup`` prefix, so the frontend's ``POST /api/setup/api-key``
landed at no FastAPI route — fell through to ``StaticFiles`` — Starlette
returns 405 Method Not Allowed for non-GET/HEAD on a static mount.

Result: every wizard step was unreachable from the GUI. The "Connect
your Claude AI key" page returned the alpha-21 error before the user
could even start the workflow.

Alpha-21 fix: dual-mount the wizard router. Frontend uses ``/api/setup/*``;
Status-Meridian.bat and the gauntlet keep using ``/setup/*``. Both
should work and resolve to the same handler.

Tests pin down both contracts so a future "consolidate URLs" refactor
can't silently break either surface.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_wizard_state_reachable_at_legacy_setup(fastapi_client: TestClient) -> None:
    """GET /setup/state — preserved for Status-Meridian.bat and the gauntlet."""
    response = fastapi_client.get("/setup/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "next_step" in body, body


def test_wizard_state_reachable_at_api_prefix(fastapi_client: TestClient) -> None:
    """GET /api/setup/state — the frontend path after alpha-20."""
    response = fastapi_client.get("/api/setup/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "next_step" in body, body


def test_wizard_runtime_reachable_at_legacy_setup(fastapi_client: TestClient) -> None:
    """GET /setup/runtime — Status-Meridian.bat probes this path. Must
    keep working at the legacy URL even after alpha-21's dual mount.
    """
    response = fastapi_client.get("/setup/runtime")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "version" in body, body


def test_wizard_api_key_post_reachable_at_api_prefix(
    fastapi_client: TestClient,
) -> None:
    """POST /api/setup/api-key — the exact alpha-20 GUI bug.

    We don't care here whether the validation passes (it won't with a
    fake key); we care that the route is wired and the handler runs.
    The pre-alpha-21 bug was 405 Method Not Allowed because StaticFiles
    intercepted the POST. Anything other than 405 (200, 400, 422 — all
    "the wizard handler ran and decided something") proves the route is
    live.
    """
    response = fastapi_client.post(
        "/api/setup/api-key",
        json={"key": "sk-ant-fake-test-key-not-real"},
    )
    assert response.status_code != 405, (
        f"POST /api/setup/api-key returned 405 Method Not Allowed — "
        "alpha-20 GUI regression. The wizard router is not mounted under /api. "
        f"Body: {response.text[:300]}"
    )
    # Either the validator returned a JSON outcome (200) or rate-limited
    # (429) or rejected the body shape (422). All are "the route is live".
    assert response.status_code in (200, 422, 429), (
        f"unexpected status {response.status_code}: {response.text[:300]}"
    )


def test_wizard_api_key_post_still_reachable_at_legacy_setup(
    fastapi_client: TestClient,
) -> None:
    """POST /setup/api-key — the legacy path (was working pre-alpha-20)
    must keep working post-alpha-21. Locks in the dual-mount contract.
    """
    response = fastapi_client.post(
        "/setup/api-key",
        json={"key": "sk-ant-fake-test-key-not-real"},
    )
    assert response.status_code != 405, response.text
    assert response.status_code in (200, 422, 429), response.text
