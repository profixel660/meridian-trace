"""Meridian TOTP authentication scaffold.

Implements RFC 6238 TOTP, recovery codes, session tokens, ASCII QR rendering
and a pluggable secret-storage backend — all using only the Python standard
library (no `pyotp`, `qrcode`, `keyring`, or `cryptography` dependency).

See CONTEXT.md §16 for the locked design (single-user, self-enrolled at first
launch, 6-digit codes, one-time recovery codes shown at enrolment).

Decision points deferred to the user — see module docstrings:

* ``secrets.py``  — secret-storage backend (plaintext-with-permissions vs
  scrypt-XOR obfuscation vs OS keychain).
* ``fastapi_dep.py`` — whether to apply ``require_session`` globally to the
  existing API routes.
"""

from meridian.auth.totp import (
    generate_secret,
    provisioning_uri,
    totp_code,
    verify_totp,
)

__all__ = [
    "generate_secret",
    "provisioning_uri",
    "totp_code",
    "verify_totp",
]
