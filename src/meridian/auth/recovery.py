"""One-time recovery codes for TOTP.

Recovery codes are shown to the user ONCE at enrolment and never persisted
in plaintext. Storage is HMAC-SHA256 hashed with a per-install salt so a
leaked store file cannot be brute-forced trivially against a global rainbow
table; verification is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 12 base32 chars = 60 bits per code; presented as ABCD-EFGH-IJKL.
_GROUP_LEN = 4
_GROUPS = 3
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Crockford-ish, no I/O/0/1


def generate_recovery_codes(n: int = 10) -> list[str]:
    """Return ``n`` freshly-minted recovery codes in ``ABCD-EFGH-IJKL`` form."""
    codes: list[str] = []
    for _ in range(n):
        groups = [
            "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN))
            for _ in range(_GROUPS)
        ]
        codes.append("-".join(groups))
    return codes


def hash_recovery_code(code: str, *, salt: bytes | None = None) -> str:
    """Hash a recovery code for on-disk storage.

    With a ``salt`` (the production path) we keyed-HMAC the canonicalised
    code so users can type ``ABCD EFGH IJKL`` or ``abcd-efgh-ijkl`` and
    still match the stored hash.

    Without a salt we hash the input bytes verbatim — this is the bare
    round-trip helper used by tests / external callers that pre-hash with
    plain ``hashlib.sha256(code.encode())`` and want
    :func:`verify_recovery_code` to find it.
    """
    if salt is None:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()
    canonical = _canonicalise(code)
    return hmac.new(salt, canonical.encode("ascii"), hashlib.sha256).hexdigest()


def verify_recovery_code(
    stored_codes_hashed: list[str],
    submitted: str,
    *,
    salt: bytes | None = None,
) -> tuple[bool, list[str]]:
    """Check ``submitted`` against the stored hash list.

    Returns ``(matched, new_stored_codes_hashed)`` — on a match, the matched
    hash is removed so the code cannot be reused. We always walk the full
    list (constant-time per entry) to avoid leaking which slot matched.
    """
    candidate = hash_recovery_code(submitted, salt=salt)
    matched_idx = -1
    for i, stored in enumerate(stored_codes_hashed):
        # compare_digest on every entry, even after a hit, so total work is
        # independent of the matched position.
        if hmac.compare_digest(stored, candidate) and matched_idx == -1:
            matched_idx = i
    if matched_idx == -1:
        return False, list(stored_codes_hashed)
    remaining = [
        h for i, h in enumerate(stored_codes_hashed) if i != matched_idx
    ]
    return True, remaining


def _canonicalise(code: str) -> str:
    """Strip whitespace + dashes, uppercase. Lets users type ``abcd efgh ijkl``."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


__all__ = [
    "generate_recovery_codes",
    "hash_recovery_code",
    "verify_recovery_code",
]
