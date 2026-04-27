"""TOTP secret-storage backends.

DECISION POINT — pick a default backend per deployment posture:

1. ``EncryptedFileStore`` (this module's v1 default).

   Stores TOTP secret + hashed recovery codes in a JSON blob at
   ``<projects_dir>/_auth/totp.json``. We do NOT encrypt the payload — the
   stdlib ships HMAC and scrypt but no authenticated symmetric cipher
   (AES-GCM, ChaCha20-Poly1305). Implementing real encryption from
   scratch on top of stdlib primitives is exactly the kind of thing the
   crypto community spends careers warning against, so we deliberately
   stop short.

   Confidentiality therefore relies on **filesystem permissions**:

   * POSIX: ``chmod 0600`` (user read/write, no group/other).
   * Windows: best-effort — we set the file's hidden + system flags via
     ``os.chmod`` (no-op on most Python builds) and rely on the user
     profile directory ACL inherited from ``%LOCALAPPDATA%`` /
     ``%USERPROFILE%`` underneath ``projects_dir``. If your projects
     directory lives somewhere world-readable (a shared drive, OneDrive
     in "anyone with link" mode, etc.) the secret is exposed.

   Trade-off: zero new dependencies, simplest-possible operational story,
   but a stolen home-folder backup contains the TOTP seed in cleartext.

2. **Obfuscated-file alternative** (documented, not implemented). Same
   layout, but the JSON blob XOR'd with an scrypt-derived keystream from a
   user passphrase. This is **obfuscation, not encryption** — without
   AEAD a tampered ciphertext decrypts to garbage rather than failing
   loudly, and without a nonce key reuse leaks plaintext XOR plaintext.
   Still strictly better than plaintext if the threat model is "someone
   pokes around my Documents folder", strictly worse than real AES-GCM.
   To enable: subclass ``SecretStore`` and use
   ``hashlib.scrypt(passphrase, salt=stored_salt, n=2**14, r=8, p=1,
   dklen=len(payload))`` as the keystream.

3. ``KeyringStore`` (stub). The right long-term answer: hand the secret
   off to the OS keychain (DPAPI on Windows, Keychain on macOS,
   Secret Service on Linux). Requires the third-party ``keyring``
   package, which the no-new-deps rule rules out for v1. The stub raises
   ``NotImplementedError`` with the install command in the message.

Which to pick is a user/operator decision — see CONTEXT.md §16/§18.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from meridian.config import settings

_AUTH_DIR_NAME = "_auth"
_FILE_NAME = "totp.json"
_SALT_FILE = "recovery_salt"


@runtime_checkable
class SecretStore(Protocol):
    """Protocol every TOTP backend implements."""

    def save(
        self,
        *,
        secret: str,
        recovery_codes_hashed: list[str],
        created_at: str,
    ) -> None: ...

    def load(self) -> dict | None: ...

    def clear(self) -> None: ...

    def is_configured(self) -> bool: ...


def _auth_dir() -> Path:
    p = settings.projects_dir / _AUTH_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _restrict_perms(path: Path) -> None:
    """Best-effort restrict-to-owner permissions.

    Windows + some FS combinations ignore mode bits. The auth dir being
    inside the user-profile-rooted projects_dir is our fallback story;
    surface this as a documented caveat in the CLI.
    """
    import contextlib

    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(path, 0o600)


def get_recovery_salt() -> bytes:
    """Return (creating if necessary) the per-install recovery-code salt.

    Used by :func:`meridian.auth.recovery.hash_recovery_code` to keyed-HMAC
    each code so a stolen ``totp.json`` cannot be brute-forced against a
    global rainbow table.
    """
    import secrets as _sec

    p = _auth_dir() / _SALT_FILE
    if not p.exists():
        p.write_bytes(_sec.token_bytes(32))
        _restrict_perms(p)
    return p.read_bytes()


class EncryptedFileStore:
    """Plaintext JSON + restricted permissions. See module docstring."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_auth_dir() / _FILE_NAME)

    @property
    def path(self) -> Path:
        return self._path

    def save(
        self,
        *,
        secret: str,
        recovery_codes_hashed: list[str],
        created_at: str,
    ) -> None:
        payload = {
            "version": 1,
            "secret": secret,
            "recovery_codes_hashed": list(recovery_codes_hashed),
            "created_at": created_at,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        _restrict_perms(self._path)

    def load(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "secret" not in data:
            return None
        return data

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def is_configured(self) -> bool:
        return self.load() is not None

    def update_recovery_codes(self, hashed: list[str]) -> None:
        """Helper used after a recovery-code burn during verification."""
        existing = self.load()
        if existing is None:
            return
        existing["recovery_codes_hashed"] = list(hashed)
        self.save(
            secret=existing["secret"],
            recovery_codes_hashed=existing["recovery_codes_hashed"],
            created_at=existing.get("created_at", _now()),
        )


class KeyringStore:
    """OS keychain backend (Windows Credential Manager / macOS Keychain /
    Linux Secret Service) via the `keyring` package.

    Decision §3.1 chose this as the v1 default. The TOTP secret never lands
    in the projects directory (which may be on OneDrive / a shared drive);
    the OS handles encryption + access control.

    Storage layout (one entry per logical field):
      service = "meridian.totp"
      usernames: "secret", "recovery_codes_hashed", "created_at"

    Recovery codes JSON are stored as a single string; on read we re-parse.
    """

    _SERVICE = "meridian.totp"
    _USER_SECRET = "secret"
    _USER_CODES = "recovery_codes_hashed"
    _USER_CREATED = "created_at"

    def __init__(self, service: str | None = None) -> None:
        self._service = service or self._SERVICE

    def _kr(self):
        # Lazy import so `from meridian.auth.secrets import ...` doesn't
        # crash if the keyring backend is broken on a given host. The
        # CLI / wizard surface can then offer EncryptedFileStore as a
        # fallback with a clear warning.
        import keyring  # noqa: PLC0415

        return keyring

    def save(
        self,
        *,
        secret: str,
        recovery_codes_hashed: list[str],
        created_at: str,
    ) -> None:
        kr = self._kr()
        kr.set_password(self._service, self._USER_SECRET, secret)
        kr.set_password(
            self._service,
            self._USER_CODES,
            json.dumps(list(recovery_codes_hashed)),
        )
        kr.set_password(self._service, self._USER_CREATED, created_at)

    def load(self) -> dict | None:
        kr = self._kr()
        secret = kr.get_password(self._service, self._USER_SECRET)
        if not secret:
            return None
        codes_raw = kr.get_password(self._service, self._USER_CODES) or "[]"
        try:
            codes = json.loads(codes_raw)
            if not isinstance(codes, list):
                codes = []
        except (json.JSONDecodeError, ValueError):
            codes = []
        created_at = kr.get_password(self._service, self._USER_CREATED) or _now()
        return {
            "version": 1,
            "secret": secret,
            "recovery_codes_hashed": codes,
            "created_at": created_at,
        }

    def clear(self) -> None:
        import contextlib  # noqa: PLC0415

        kr = self._kr()
        for username in (self._USER_SECRET, self._USER_CODES, self._USER_CREATED):
            with contextlib.suppress(kr.errors.PasswordDeleteError):
                kr.delete_password(self._service, username)

    def is_configured(self) -> bool:
        try:
            return self.load() is not None
        except Exception:  # pragma: no cover — keyring backend missing
            return False

    def update_recovery_codes(self, hashed: list[str]) -> None:
        """Mirror EncryptedFileStore's helper used after a recovery-code burn."""
        kr = self._kr()
        kr.set_password(self._service, self._USER_CODES, json.dumps(list(hashed)))


def default_store() -> SecretStore:
    """v1 default per decision §3.1: OS keychain.

    Falls back to the file-based store if the keyring backend is genuinely
    unavailable on this host (rare — every modern OS ships one). The
    fallback emits a structured warning so it's visible in logs.
    """
    try:
        import keyring  # noqa: F401, PLC0415

        return KeyringStore()
    except ImportError:  # pragma: no cover — keyring is now a hard dep
        from meridian.logging import get_logger

        get_logger("meridian.auth.secrets").warning(
            "keyring.unavailable_falling_back_to_file",
            reason="keyring package not importable",
        )
        return EncryptedFileStore()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "EncryptedFileStore",
    "KeyringStore",
    "SecretStore",
    "default_store",
    "get_recovery_salt",
]
