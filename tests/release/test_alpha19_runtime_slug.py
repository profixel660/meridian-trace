"""Alpha-19 regression gate: every [name] page reads the slug at runtime.

Background. The frontend is a Next.js static export. The build pre-renders
``/projects/[name]/...`` with the placeholder slug ``_`` because project
names are runtime-only data. Alpha-18 added a FastAPI SPA fallback so URLs
like ``/projects/bod-2/...`` serve that same ``_`` HTML, leaving the React
app to hydrate from the URL.

The alpha-19 bug class: any page that reads its slug via ``use(params)``
gets the build-time ``_``, then issues API calls to ``/projects/_/...``
which all 404. The fix is to read from ``window.location.pathname`` via
the ``useRuntimeProjectSlug`` hook.

This test is a structural gate: every ``[name]/**/page.tsx`` MUST import
the hook and MUST NOT use ``use(params)`` for the slug. If a future page
is added that reads the slug from params, this test fails before the
release ships.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGES_GLOB = "apps/web/src/app/projects/[[]name[]]/**/page.tsx"

_USE_PARAMS_PATTERN = re.compile(
    r"const\s*\{\s*name\s*\}\s*=\s*use\s*\(\s*params\s*\)",
)


def _collect_pages() -> list[Path]:
    pages = sorted(REPO_ROOT.glob(PAGES_GLOB))
    assert pages, f"expected at least one page under {PAGES_GLOB}"
    return pages


def test_every_name_page_imports_runtime_slug_hook() -> None:
    missing: list[str] = []
    for page in _collect_pages():
        text = page.read_text(encoding="utf-8")
        if "useRuntimeProjectSlug" not in text:
            missing.append(str(page.relative_to(REPO_ROOT)))
    assert not missing, (
        "These [name] pages do not import useRuntimeProjectSlug — "
        "they will read the static-export placeholder `_` slug at runtime "
        "and 404 on every API call:\n  " + "\n  ".join(missing)
    )


def test_no_name_page_reads_slug_from_params() -> None:
    offenders: list[str] = []
    for page in _collect_pages():
        text = page.read_text(encoding="utf-8")
        if _USE_PARAMS_PATTERN.search(text):
            offenders.append(str(page.relative_to(REPO_ROOT)))
    assert not offenders, (
        "These [name] pages still read the slug via use(params) — "
        "in static-export + SPA-fallback mode this resolves to the "
        "placeholder `_`, not the runtime slug:\n  " + "\n  ".join(offenders)
    )
