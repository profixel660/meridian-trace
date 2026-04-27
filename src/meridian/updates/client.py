"""Auto-update manifest fetcher (CONTEXT.md §18).

Manifest format (JSON)::

    {
      "latest_version": "1.2.0",
      "download_url":   "https://example.invalid/meridian-1.2.0-windows.exe",
      "signature":      "<base64url Ed25519 sig over the .exe>",
      "release_notes":  "What changed in this release...",
      "minimum_supported_from": "0.5.0"
    }

``minimum_supported_from`` lets the CDN signal "users older than this MUST
update" — useful if a security fix lands and silently-skipping the upgrade
is dangerous. The CLI surfaces this distinctly in :func:`check_for_updates`
output but does not enforce it (force-upgrade is the installer's job).

This module only **checks**. Downloading + installing the new version is
deferred until the installer technology decision (§3.7).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from meridian.config import settings
from meridian.logging import get_logger

_log = get_logger("meridian.updates")


# ---------------------------------------------------------------------------
# Placeholders — REPLACE before shipping a real release.
# Search the repo for ``_PLACEHOLDER_`` to find every deferred wire-up site.
# ---------------------------------------------------------------------------

# §3.5 RESOLVED 2026-04-27: GitHub Releases on the profixel660/meridian-trace
# repo. Updates are published as a release asset named ``manifest.json``;
# ``releases/latest/download/<asset>`` is the canonical permalink GitHub
# rewrites to the most recent tagged release. No GitHub API tokens needed
# (works against a public repo) and no rate limit beyond the asset CDN's.
# Once the first release is tagged, this URL serves a real manifest. Until
# then, GitHub returns 404 and ``check_for_updates`` cleanly returns None.
_DEFAULT_MANIFEST_URL: str = (
    "https://github.com/profixel660/meridian-trace/releases/latest/download/manifest.json"
)

# Backwards-compatible alias for code that imported the old name.
_PLACEHOLDER_MANIFEST_URL: str = _DEFAULT_MANIFEST_URL

# How long to wait for the manifest fetch (seconds). Short — we don't want
# to block the CLI start for a network round-trip if the user is offline.
_DEFAULT_TIMEOUT_S = 10

# User-Agent presented to the manifest endpoint. Identifies the client so
# CDN logs can attribute checks to the tool rather than "unknown".
_USER_AGENT = "meridian-updater/1"

# Persisted "skip this version" cache.
_SKIPPED_FILENAME = "updates_skipped.json"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateManifest:
    """Parsed manifest as returned by the CDN."""

    latest_version: str
    download_url: str
    signature: str
    release_notes: str
    minimum_supported_from: str | None = None


@dataclass(frozen=True)
class UpdateAvailable:
    """Returned by :func:`check_for_updates` when an update exists."""

    current_version: str
    manifest: UpdateManifest
    is_mandatory: bool  # True iff current_version < manifest.minimum_supported_from


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _meridian_dir() -> Path:
    """``<projects_dir>/_meridian/`` — process-wide state directory."""
    return settings.projects_dir / "_meridian"


def _skipped_path() -> Path:
    return _meridian_dir() / _SKIPPED_FILENAME


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def _current_version() -> str:
    """Read ``[project].version`` from pyproject.toml; fall back to settings."""
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


_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.\-]+))?"
)


def _parse_semver(v: str) -> tuple[int, int, int, str] | None:
    """Parse ``MAJOR.MINOR.PATCH[-pre]``. Returns None on parse failure.

    Build metadata (``+build``) is ignored per SemVer 2.0.0 §10.
    """
    if not isinstance(v, str):
        return None
    s = v.strip().lstrip("v")
    s = s.split("+", 1)[0]
    m = _SEMVER_RE.match(s)
    if not m:
        return None
    return (
        int(m.group("major")),
        int(m.group("minor")),
        int(m.group("patch")),
        m.group("pre") or "",
    )


def _compare_semver(a: str, b: str) -> int:
    """Return -1 if a<b, 0 if a==b, 1 if a>b. Unparseable inputs sort earliest.

    Pre-release ordering follows SemVer 2.0.0 §11: a pre-release version
    has **lower** precedence than the same version without a pre-release tag
    (e.g. ``1.0.0-alpha < 1.0.0``). Within pre-releases, dotted identifiers
    compare numerically when both are digits, lexically otherwise.
    """
    pa, pb = _parse_semver(a), _parse_semver(b)
    if pa is None and pb is None:
        return 0
    if pa is None:
        return -1
    if pb is None:
        return 1
    # Compare major.minor.patch first.
    for ai, bi in zip(pa[:3], pb[:3], strict=True):
        if ai != bi:
            return -1 if ai < bi else 1
    # Then pre-release: empty (release) > non-empty (pre-release).
    pre_a, pre_b = pa[3], pb[3]
    if pre_a == pre_b:
        return 0
    if not pre_a:
        return 1
    if not pre_b:
        return -1
    # Both pre-releases: compare dot-separated identifiers.
    a_ids = pre_a.split(".")
    b_ids = pre_b.split(".")
    for ai, bi in zip(a_ids, b_ids, strict=False):
        ai_is_num = ai.isdigit()
        bi_is_num = bi.isdigit()
        if ai_is_num and bi_is_num:
            ai_v: int | str = int(ai)
            bi_v: int | str = int(bi)
        elif ai_is_num != bi_is_num:
            # Numeric identifiers always have lower precedence than alphanumeric.
            return -1 if ai_is_num else 1
        else:
            ai_v, bi_v = ai, bi
        if ai_v != bi_v:
            return -1 if ai_v < bi_v else 1  # type: ignore[operator]
    # All equal so far → fewer identifiers wins.
    if len(a_ids) != len(b_ids):
        return -1 if len(a_ids) < len(b_ids) else 1
    return 0


# ---------------------------------------------------------------------------
# Skipped-version persistence
# ---------------------------------------------------------------------------


def list_skipped_versions() -> list[str]:
    """Return the list of versions the user has chosen to skip."""
    path = _skipped_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    skipped = raw.get("skipped", [])
    if not isinstance(skipped, list):
        return []
    return [str(v) for v in skipped]


def record_skipped_version(version: str) -> Path:
    """Append a version to the skip list. Idempotent. Returns the file path."""
    path = _skipped_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict = {"skipped": [], "updated_at": ""}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
                if not isinstance(current.get("skipped"), list):
                    current["skipped"] = []
        except (OSError, json.JSONDecodeError):
            pass
    skipped = list({*current.get("skipped", []), version})
    current["skipped"] = sorted(skipped)
    current["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    _log.info("updates.skipped_version", version=version)
    return path


# ---------------------------------------------------------------------------
# Manifest fetch + check
# ---------------------------------------------------------------------------


def _fetch_manifest(url: str, *, timeout_s: int) -> UpdateManifest | None:
    """GET the manifest URL, parse JSON. Returns None on any failure."""
    req = urllib.request.Request(  # noqa: S310 — URL is a constant we control
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        _log.info("updates.fetch_failed", url=url, error=str(exc))
        return None

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        _log.info("updates.parse_failed", url=url, error=str(exc))
        return None

    if not isinstance(raw, dict):
        _log.info("updates.malformed_manifest", url=url, reason="not an object")
        return None

    try:
        return UpdateManifest(
            latest_version=str(raw["latest_version"]),
            download_url=str(raw["download_url"]),
            signature=str(raw.get("signature", "")),
            release_notes=str(raw.get("release_notes", "")),
            minimum_supported_from=(
                str(raw["minimum_supported_from"])
                if raw.get("minimum_supported_from")
                else None
            ),
        )
    except (KeyError, TypeError) as exc:
        _log.info("updates.malformed_manifest", url=url, error=str(exc))
        return None


def check_for_updates(
    *,
    manifest_url: str | None = None,
    current_version: str | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    include_skipped: bool = False,
) -> UpdateAvailable | None:
    """Fetch the manifest and decide whether an update is available.

    Returns ``None`` when:

    - the running version is already >= the manifest's ``latest_version``,
    - the manifest could not be fetched / parsed (logged, never raised),
    - the user has previously skipped this version (unless
      ``include_skipped=True``).

    Returns an :class:`UpdateAvailable` otherwise. ``is_mandatory=True`` when
    the running version is older than ``minimum_supported_from``.
    """
    url = manifest_url or _PLACEHOLDER_MANIFEST_URL
    cur = current_version or _current_version()

    manifest = _fetch_manifest(url, timeout_s=timeout_s)
    if manifest is None:
        return None

    if _compare_semver(cur, manifest.latest_version) >= 0:
        _log.info(
            "updates.up_to_date",
            current_version=cur,
            latest_version=manifest.latest_version,
        )
        return None

    if not include_skipped and manifest.latest_version in list_skipped_versions():
        _log.info("updates.skipped", version=manifest.latest_version)
        return None

    is_mandatory = False
    if manifest.minimum_supported_from:
        is_mandatory = _compare_semver(cur, manifest.minimum_supported_from) < 0

    _log.info(
        "updates.available",
        current_version=cur,
        latest_version=manifest.latest_version,
        is_mandatory=is_mandatory,
    )
    return UpdateAvailable(
        current_version=cur,
        manifest=manifest,
        is_mandatory=is_mandatory,
    )


__all__ = [
    "UpdateAvailable",
    "UpdateManifest",
    "check_for_updates",
    "list_skipped_versions",
    "record_skipped_version",
]
