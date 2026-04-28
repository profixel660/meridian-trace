"""Alpha-20 regression gate: API endpoints under /api, SPA at /projects/<slug>.

Background. Alpha-19 fixed the runtime-slug bug but exposed a deeper
architectural collision: every ``/projects/{name}/<verb>`` JSON endpoint
sat at the same URL space the SPA uses for browser navigation. A click
on the dashboard's "Quarantine" link sent the browser to
``/projects/bod-2/quarantine``. FastAPI matched that against the
``/projects/{name}/quarantine`` JSON endpoint (registered first), and
the user got raw JSON instead of the React shell.

Alpha-20 fix: every ``/projects/{name}/<verb>`` route now lives under
``/api/projects/{name}/<verb>``. The bare ``/projects/<slug>/...`` URL
space is owned exclusively by the SPA — the FastAPI SPA fallback (or
StaticFiles when a real placeholder file exists) wins because there is
no longer an API route at the same path.

Tests:
    1. ``GET /api/projects/<name>/coverage`` returns JSON 200.
    2. ``GET /projects/<name>/coverage`` (browser-style URL) does NOT
       match an API route — it MUST NOT return JSON. Either 404
       (no static file at this path) or HTML (SPA fallback served the
       placeholder index.html). Anything `application/json` is a
       regression.
    3. ``GET /api/projects/<name>/quarantine`` JSON 200.
    4. ``GET /projects/<name>/quarantine`` MUST NOT return JSON.
    5. ``GET /api/projects`` (list) JSON 200.
    6. ``GET /projects`` (list page in SPA) MUST NOT return the API
       JSON list — the static-export ``/projects/index.html`` should win.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient


_API_VERBS_THAT_MUST_NOT_LEAK = (
    "coverage",
    "quarantine",
    "master",
    "audit",
    "questions",
    "conflicts",
    "sources",
)


def test_api_coverage_under_api_prefix(
    fastapi_client: TestClient,
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
) -> None:
    name, _db, conn, _src_id = fresh_project_with_sample_doc
    conn.close()
    response = fastapi_client.get(f"/api/projects/{name}/coverage")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert "deliverable_status" in body, body


def test_browser_path_serves_html_not_json(
    fastapi_client: TestClient,
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
) -> None:
    """The bare /projects/<name>/<verb> path must serve the SPA shell, not JSON.

    Two assertions per verb:
      1. response is NOT ``application/json`` (the alpha-19 collision —
         a JSON endpoint matching the same URL).
      2. response is 200 with ``text/html`` — the alpha-18 SPA fallback
         served the placeholder index.html. A 404 here means the SPA
         fallback is broken (the alpha-20 reviewer caught exactly this
         regression when a draft of the patch moved the fallback's
         ``@app.get`` decorators onto a router that no longer reached
         ``app.routes``).

    The test relies on the worktree containing a built static export at
    ``apps/web/out`` — without it, ``settings.web_dir`` is None and the
    SPA fallback never registers, so this test is skipped. The release
    gauntlet always runs ``npm run build`` before pytest, so this is a
    safe assumption in CI.
    """
    from meridian.config import settings

    if settings.web_dir is None:
        import pytest

        pytest.skip("settings.web_dir not resolvable; build the web app first")

    name, _db, conn, _src_id = fresh_project_with_sample_doc
    conn.close()

    for verb in _API_VERBS_THAT_MUST_NOT_LEAK:
        response = fastapi_client.get(f"/projects/{name}/{verb}")
        ctype = response.headers.get("content-type", "")
        assert not ctype.startswith("application/json"), (
            f"GET /projects/{name}/{verb} leaked JSON (content-type={ctype}) — "
            "alpha-19 collision regression. The /api prefix exists to keep "
            "the SPA URL space disjoint from the JSON API."
        )
        assert response.status_code == 200, (
            f"GET /projects/{name}/{verb} returned {response.status_code}; "
            "expected 200 from the alpha-18 SPA fallback. The fallback "
            "may be missing or registered on the wrong router."
        )
        assert "text/html" in ctype, (
            f"GET /projects/{name}/{verb} content-type={ctype}; "
            "expected text/html (the SPA placeholder shell)."
        )


def test_api_taxonomy_pending_under_api_prefix(
    fastapi_client: TestClient,
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
) -> None:
    name, _db, conn, _src_id = fresh_project_with_sample_doc
    conn.close()
    response = fastapi_client.get(f"/api/projects/{name}/taxonomy/pending")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")


def test_api_projects_list_under_api_prefix(
    fastapi_client: TestClient,
) -> None:
    response = fastapi_client.get("/api/projects")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), list)


def test_legacy_projects_list_does_not_match_api(
    fastapi_client: TestClient,
) -> None:
    """GET /projects must NOT match the API list endpoint.

    In production the static export serves the projects-index page HTML
    at this URL. In TestClient with no StaticFiles mount it 404s. What
    we forbid is a 200 response with the API list shape (which would
    indicate the alpha-19 collision regressed). FastAPI's default 404
    handler returns ``application/json`` with ``{"detail": "Not Found"}``,
    so we can't simply assert ``not application/json``. We assert the
    response is not a successful API list.
    """
    response = fastapi_client.get("/projects")
    if response.status_code == 200:
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        assert body is None or not isinstance(body, list), (
            "GET /projects leaked the JSON API list — alpha-19 collision "
            "regression (the API endpoint was supposed to move to /api)."
        )
