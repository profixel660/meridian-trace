"""License signature verification (CONTEXT.md §17).

Ed25519-signed license payloads. The private key is held by Peter and never
ships with the app; the public key is embedded as a constant in
:mod:`meridian.licensing.verify` and is the **only** thing the app needs to
validate a license string.

Optional dependency: ``cryptography`` (install via ``meridian[license]``).
The module loads even when the library is absent — verification will return
``LicenseStatus.malformed`` with a clear "install cryptography" message
rather than crashing on import.

Public API:

- :class:`LicensePayload` — parsed license dataclass
- :class:`LicenseStatus` — verification outcome enum
- :class:`LicenseVerification` — `(status, payload, message)` triple
- :func:`verify_license` — verify a license string
- :func:`load_license_from_disk` — read installed license
- :func:`install_license` — copy a license file into place
- :func:`revocation_list_path` — offline revocation cache path
"""

from __future__ import annotations

from meridian.licensing.verify import (
    LicensePayload,
    LicenseStatus,
    LicenseVerification,
    install_license,
    load_license_from_disk,
    revocation_list_path,
    verify_license,
)

__all__ = [
    "LicensePayload",
    "LicenseStatus",
    "LicenseVerification",
    "install_license",
    "load_license_from_disk",
    "revocation_list_path",
    "verify_license",
]
