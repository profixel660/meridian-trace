"""Ed25519 license signature verification (CONTEXT.md §17).

License string format::

    base64url(payload_json) + "." + base64url(signature)

Where ``payload_json`` is a UTF-8 JSON object with the fields described by
:class:`LicensePayload`, and ``signature`` is the raw 64-byte Ed25519
signature over the **canonical UTF-8 bytes of the base64url-encoded
payload** (i.e. the same bytes that appear before the ``.`` in the license
string). Signing the encoded form avoids JSON re-canonicalisation
ambiguity at verify time.

Optional dependency
-------------------

This module relies on the ``cryptography`` package for Ed25519. It is **not**
listed as a hard dependency in ``pyproject.toml`` — install via
``meridian[license]`` (or plain ``pip install cryptography``) to enable
verification. When the library is absent, :func:`verify_license` returns
``LicenseStatus.malformed`` with a ``message`` that explains how to install
it, so the host CLI never crashes on import.

Three-outcome discipline
------------------------

For UI surfaces, callers should map :class:`LicenseStatus` to:

- ``valid`` → pass
- ``expired`` / ``signature_invalid`` / ``revoked`` → fail
- ``malformed`` → **borderline / needs review** (e.g. the user pasted
  whitespace, the cryptography lib is missing, the file is corrupt)

Surface ``malformed`` to the user as "this license could not be parsed,
please re-paste or contact support" rather than a silent failure.
"""

from __future__ import annotations

import base64
import binascii
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from meridian.config import settings
from meridian.logging import get_logger

_log = get_logger("meridian.licensing")


# ---------------------------------------------------------------------------
# Placeholders — REPLACE before shipping a real release.
# Search the repo for ``_PLACEHOLDER_`` to find every deferred wire-up site.
# ---------------------------------------------------------------------------

# DEFERRED §3.8: replace with real Ed25519 pubkey when generated.
# 32-byte Ed25519 public key, hex-encoded (64 hex chars). The placeholder is
# all-zeros so any signature check against it is guaranteed to fail (safe
# default — refuses every license until a real key is wired in).
_PLACEHOLDER_PUBLIC_KEY: str = "00" * 32

# Where the installed license is stored on disk.
_LICENSE_FILENAME = "license.txt"
# Local revocation cache: a JSON list of revoked license IDs (e.g. customer_id
# values). Populated out-of-band — there is **no** revocation network endpoint
# (CONTEXT.md §17: "expiry is the kill switch"). This file exists so support
# can drop an updated revocation list onto a customer's machine if a leaked
# license needs killing before its expiry.
_REVOCATION_FILENAME = "license_revocations.json"


# ---------------------------------------------------------------------------
# Enums + dataclasses
# ---------------------------------------------------------------------------


class LicenseStatus(StrEnum):
    """Verification outcome.

    Three-outcome mapping (see module docstring):

    - ``valid`` → pass
    - ``expired`` / ``signature_invalid`` / ``revoked`` → fail
    - ``malformed`` → borderline / needs review
    """

    valid = "valid"
    expired = "expired"
    signature_invalid = "signature_invalid"
    malformed = "malformed"
    revoked = "revoked"


@dataclass(frozen=True)
class LicensePayload:
    """Decoded license payload.

    ``issued_at`` and ``expires_at`` are ISO-8601 UTC strings. ``features`` is
    a free-form list of capability tags (e.g. ``["xref", "evidence_pack"]``).
    ``signature`` is the raw signature bytes (post base64url decode); kept on
    the dataclass so callers can audit what was checked.
    """

    customer_id: str
    customer_name: str
    plan: str
    issued_at: str
    expires_at: str
    features: list[str] = field(default_factory=list)
    signature: bytes = b""

    def days_remaining(self, *, now: datetime | None = None) -> int | None:
        """Whole days from ``now`` until ``expires_at``. None if unparseable."""
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        n = now or datetime.now(UTC)
        delta = exp - n
        return delta.days


@dataclass(frozen=True)
class LicenseVerification:
    """Result triple — status, parsed payload (when decodable), human message."""

    status: LicenseStatus
    payload: LicensePayload | None
    message: str


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _meridian_dir() -> Path:
    """``<projects_dir>/_meridian/`` — process-wide licensing/state directory.

    The leading underscore keeps it out of the project listing (the projects
    UI scans ``<projects_dir>/*.sqlite`` files).
    """
    return settings.projects_dir / "_meridian"


def license_path() -> Path:
    """Path to the installed license file."""
    return _meridian_dir() / _LICENSE_FILENAME


def revocation_list_path() -> Path:
    """Path to the local revocation cache (offline-checkable)."""
    return _meridian_dir() / _REVOCATION_FILENAME


# ---------------------------------------------------------------------------
# base64url helpers (URL-safe, padding-tolerant)
# ---------------------------------------------------------------------------


def _b64url_decode(data: str) -> bytes:
    """Decode a base64url string, repadding as needed."""
    s = data.strip()
    # URL-safe alphabet uses '-' and '_'; standard b64decode with urlsafe variant.
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))


def _b64url_encode(data: bytes) -> str:
    """Encode bytes as base64url (no padding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Cryptography import guard
# ---------------------------------------------------------------------------


def _load_ed25519():
    """Return the ``Ed25519PublicKey`` class, or None if unavailable.

    Kept inside a function so the module imports cleanly even when
    ``cryptography`` is not installed.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        return None
    return Ed25519PublicKey


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def _is_revoked(customer_id: str) -> bool:
    """Check the local revocation list (offline)."""
    path = revocation_list_path()
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, list):
        return False
    return customer_id in {str(x) for x in raw}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_license(
    license_string: str,
    *,
    public_key: bytes | None = None,
    now: datetime | None = None,
) -> LicenseVerification:
    """Verify a license string. Never raises — returns a status enum.

    ``public_key`` is 32 raw Ed25519 public-key bytes. When None, the
    constant ``_PLACEHOLDER_PUBLIC_KEY`` is used (which is all-zeros until
    a real key is wired in — see CONTEXT.md §3.8).
    """
    # ---- Decode the license string envelope ----
    if not isinstance(license_string, str) or "." not in license_string:
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            "License string must be of the form '<payload>.<signature>'.",
        )
    payload_b64, _, sig_b64 = license_string.strip().partition(".")
    if not payload_b64 or not sig_b64:
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            "License string is missing the payload or signature segment.",
        )

    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
    except (binascii.Error, ValueError):
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            "License string is not valid base64url.",
        )

    # ---- Parse the payload JSON ----
    try:
        raw = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            "License payload is not valid UTF-8 JSON.",
        )
    if not isinstance(raw, dict):
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            "License payload must be a JSON object.",
        )

    required = ("customer_id", "customer_name", "plan", "issued_at", "expires_at")
    missing = [k for k in required if k not in raw]
    if missing:
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            f"License payload missing required fields: {', '.join(missing)}.",
        )

    features = raw.get("features", []) or []
    if not isinstance(features, list):
        return LicenseVerification(
            LicenseStatus.malformed,
            None,
            "License 'features' must be a list.",
        )

    payload = LicensePayload(
        customer_id=str(raw["customer_id"]),
        customer_name=str(raw["customer_name"]),
        plan=str(raw["plan"]),
        issued_at=str(raw["issued_at"]),
        expires_at=str(raw["expires_at"]),
        features=[str(f) for f in features],
        signature=signature,
    )

    # ---- Resolve public key ----
    if public_key is None:
        try:
            public_key = bytes.fromhex(_PLACEHOLDER_PUBLIC_KEY)
        except ValueError:
            return LicenseVerification(
                LicenseStatus.malformed,
                payload,
                "Embedded public key constant is not valid hex.",
            )
    if len(public_key) != 32:
        return LicenseVerification(
            LicenseStatus.malformed,
            payload,
            f"Ed25519 public key must be 32 bytes, got {len(public_key)}.",
        )

    # ---- Cryptography check ----
    ed25519_public_key_cls = _load_ed25519()
    if ed25519_public_key_cls is None:
        return LicenseVerification(
            LicenseStatus.malformed,
            payload,
            (
                "License verification requires the 'cryptography' package. "
                "Install with: pip install meridian[license]"
            ),
        )

    try:
        pk = ed25519_public_key_cls.from_public_bytes(public_key)
        # Sign over the encoded form (the bytes before the '.') — this avoids
        # JSON re-canonicalisation pitfalls.
        pk.verify(signature, payload_b64.encode("ascii"))
    except Exception:  # noqa: BLE001 — cryptography raises InvalidSignature
        _log.info(
            "license.signature_invalid",
            customer_id=payload.customer_id,
        )
        return LicenseVerification(
            LicenseStatus.signature_invalid,
            payload,
            "License signature did not verify against the embedded public key.",
        )

    # ---- Revocation check (offline) ----
    if _is_revoked(payload.customer_id):
        _log.info("license.revoked", customer_id=payload.customer_id)
        return LicenseVerification(
            LicenseStatus.revoked,
            payload,
            f"License for {payload.customer_id} is on the local revocation list.",
        )

    # ---- Expiry check ----
    n = now or datetime.now(UTC)
    try:
        exp = datetime.fromisoformat(payload.expires_at.replace("Z", "+00:00"))
    except ValueError:
        return LicenseVerification(
            LicenseStatus.malformed,
            payload,
            f"License 'expires_at' is not ISO-8601: {payload.expires_at!r}.",
        )
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp < n:
        return LicenseVerification(
            LicenseStatus.expired,
            payload,
            f"License expired on {payload.expires_at}.",
        )

    return LicenseVerification(
        LicenseStatus.valid,
        payload,
        f"Valid — {payload.plan} for {payload.customer_name}.",
    )


# ---------------------------------------------------------------------------
# Disk operations
# ---------------------------------------------------------------------------


def load_license_from_disk() -> str | None:
    """Return the installed license string, or None if not installed."""
    path = license_path()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def install_license(source: Path) -> Path:
    """Copy a license file into the canonical location, returning the path.

    The caller should validate it with :func:`verify_license` afterwards.
    """
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"License file not found: {source}")
    dest_dir = _meridian_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _LICENSE_FILENAME
    shutil.copyfile(source, dest)
    _log.info("license.installed", path=str(dest))
    return dest


__all__ = [
    "LicensePayload",
    "LicenseStatus",
    "LicenseVerification",
    "install_license",
    "license_path",
    "load_license_from_disk",
    "revocation_list_path",
    "verify_license",
]
