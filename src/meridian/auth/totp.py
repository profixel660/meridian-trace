"""RFC 6238 TOTP — stdlib-only implementation.

CONTEXT.md §16 locks the format at 6-digit codes, 30-second period, single
user, self-enrolled at first launch. The defaults below match that.

The wider RFC 4226 (HOTP) / RFC 6238 (TOTP) algorithms are tiny and well-
specified, so we implement them directly rather than depend on ``pyotp``.

Algorithm — RFC 6238 §4.2:

    T = floor((current_unix_time - T0) / X)
    HOTP(K, T) where K is the shared secret and T is the moving factor.

HOTP (RFC 4226 §5.3):

    HS  = HMAC-SHA1(K, T)              # 20-byte digest
    O   = HS[19] & 0x0f                # dynamic-truncation offset
    P   = HS[O .. O+3]                 # 4 bytes, top bit masked
    code = (P mod 10^digits)           # left-padded with zeros

We default to HMAC-SHA1 to match Google Authenticator / FreeOTP / Authy and
the RFC 6238 Appendix B test vectors. RFC 6238 §1.2 permits SHA-256 / SHA-512
but most authenticator apps still assume SHA-1 unless explicitly told via
the provisioning URI (which we do not advertise — keeping the lowest-common-
denominator default avoids "code rejected" mysteries during enrolment).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets
import struct
import time as _time
from urllib.parse import quote, urlencode

# Locked per CONTEXT.md §16.
DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30
DEFAULT_ALGORITHM = "SHA1"  # RFC 6238 default + universally supported by apps.


def generate_secret(length_bytes: int = 20) -> str:
    """Return a fresh base32-encoded TOTP secret.

    20 bytes = 160 bits — RFC 4226 §4 R6's recommendation and the size the
    HMAC-SHA1 digest naturally produces. Padding is stripped so the secret
    pastes cleanly into authenticator apps.
    """
    raw = _secrets.token_bytes(length_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _b32_decode(secret: str) -> bytes:
    """Decode a (possibly unpadded, possibly lowercase) base32 secret."""
    s = secret.strip().replace(" ", "").upper()
    # base32 input length must be a multiple of 8 — re-pad.
    pad = (-len(s)) % 8
    return base64.b32decode(s + ("=" * pad))


def _hotp(key: bytes, counter: int, *, digits: int, algorithm: str) -> str:
    """RFC 4226 HOTP. Counter is a 64-bit big-endian unsigned int."""
    msg = struct.pack(">Q", counter)
    digest_mod = getattr(hashlib, algorithm.lower())
    mac = hmac.new(key, msg, digest_mod).digest()
    offset = mac[-1] & 0x0F
    truncated = (
        ((mac[offset] & 0x7F) << 24)
        | ((mac[offset + 1] & 0xFF) << 16)
        | ((mac[offset + 2] & 0xFF) << 8)
        | (mac[offset + 3] & 0xFF)
    )
    code = truncated % (10**digits)
    return str(code).zfill(digits)


def totp_code(
    secret: str,
    *,
    time: int | None = None,
    window: int = 0,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Compute a TOTP code for the given secret.

    ``window`` shifts the time-step counter by that many periods (negative or
    positive). Useful for testing skew tolerance; production callers should
    leave it at 0 and let :func:`verify_totp` sweep the window.
    """
    now = _time.time() if time is None else time
    counter = int(now // period) + window
    key = _b32_decode(secret)
    return _hotp(key, counter, digits=digits, algorithm=algorithm)


def verify_totp(
    secret: str,
    code: str,
    *,
    time: int | None = None,
    window: int = 1,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> bool:
    """Verify a code against the secret, allowing ±``window`` periods of skew.

    Default ``window=1`` accepts the previous, current and next 30-second
    bucket — the standard tolerance recommended by RFC 6238 §5.2 for clock
    skew. The comparison uses :func:`hmac.compare_digest` to avoid timing
    leaks.
    """
    submitted = code.strip()
    if not submitted.isdigit() or len(submitted) != digits:
        return False
    for offset in range(-window, window + 1):
        candidate = totp_code(
            secret,
            time=time,
            window=offset,
            digits=digits,
            period=period,
            algorithm=algorithm,
        )
        if hmac.compare_digest(candidate, submitted):
            return True
    return False


def provisioning_uri(
    secret: str,
    *,
    account_name: str,
    issuer: str = "Meridian",
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Build an ``otpauth://totp/...`` URI suitable for QR-encoding.

    Format per the de-facto Google Authenticator key-URI spec:

        otpauth://totp/Issuer:account?secret=...&issuer=Issuer&...
    """
    label = f"{issuer}:{account_name}"
    params = {
        "secret": secret,
        "issuer": issuer,
        "algorithm": algorithm,
        "digits": str(digits),
        "period": str(period),
    }
    return f"otpauth://totp/{quote(label)}?{urlencode(params)}"


__all__ = [
    "DEFAULT_ALGORITHM",
    "DEFAULT_DIGITS",
    "DEFAULT_PERIOD",
    "generate_secret",
    "provisioning_uri",
    "totp_code",
    "verify_totp",
]
