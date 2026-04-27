"""Generate the Ed25519 keypair used to sign Meridian licenses (§3.8).

Run this ONCE. Outputs:
  - The PRIVATE key written to a path you choose (NOT inside the repo,
    NOT inside OneDrive — script refuses both with a clear message).
  - The PUBLIC key printed to stdout in two formats:
      * 32-byte raw, hex-encoded — paste into
        `src/meridian/licensing/verify.py` (replaces `_PLACEHOLDER_PUBLIC_KEY`)
      * PEM (SubjectPublicKeyInfo) — for documentation / sharing

Usage::

    python scripts/gen_license_keypair.py --out C:\\keys\\meridian_license_private.pem

After running:
  1. Save the private key file to your password manager (1Password,
     Bitwarden, etc.) as a secure-note attachment.
  2. Copy the file to an encrypted USB drive, store offline.
  3. Securely delete the on-disk copy. The password manager + USB are
     the canonical copies.
  4. Paste the printed hex public key into verify.py.
  5. Commit the verify.py change. The private key is NEVER committed.

Threat model recap (§3.8): if the private key leaks, an attacker can mint
unlimited licenses — that's the only secret in the licensing scheme. Loss
of the key is also bad (you can't issue new licenses; existing licenses
keep working until expiry). Two backups = one extra step that prevents
both failure modes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
except ImportError:
    print(
        "ERROR: `cryptography` not installed.\n"
        "Run one of:\n"
        "    uv pip install cryptography\n"
        "    pip install cryptography\n"
        "    uv sync --extra license   # if you've added the optional extra",
        file=sys.stderr,
    )
    sys.exit(2)


_REPO_MARKERS = (".git", "pyproject.toml")


def _find_repo_root(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if any((parent / m).exists() for m in _REPO_MARKERS):
            return parent
    return None


def _refuse_unsafe_destination(dest: Path) -> None:
    """Hard-stop the script if the chosen path is unsafe.

    Two failure modes we refuse:
      1. OneDrive-synced — the key would replicate to OneDrive's servers
         and any other devices on the user's account.
      2. Inside the Meridian repo — risk of accidental git commit.
    """
    p = dest.resolve()
    if "OneDrive" in p.as_posix():
        raise SystemExit(
            f"REFUSED: destination {p} is inside a OneDrive-synced folder. "
            "Pick a local-only path (e.g. C:\\keys\\meridian_license_private.pem)."
        )
    repo = _find_repo_root(Path(__file__).resolve())
    if repo is not None:
        try:
            p.relative_to(repo)
        except ValueError:
            pass
        else:
            raise SystemExit(
                f"REFUSED: destination {p} is inside the Meridian repo at {repo}. "
                "Private key must NEVER be checked into git. Pick a path outside the repo."
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Ed25519 license-signing keypair for Meridian.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Where to write the PRIVATE key (PEM format). Must be OUTSIDE OneDrive and the repo.",
    )
    args = parser.parse_args()

    _refuse_unsafe_destination(args.out)

    if args.out.exists():
        raise SystemExit(
            f"REFUSED: {args.out} already exists. "
            "Pick a fresh path; this script will not overwrite an existing key."
        )

    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()

    pem_private = sk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(pem_private)
    try:
        args.out.chmod(0o600)
    except OSError:
        # chmod has limited semantics on Windows. Not fatal.
        pass

    pem_public = pk.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    raw_public = pk.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    hex_public = raw_public.hex()

    print(f"Private key written to {args.out}")
    print()
    print("=" * 72)
    print("PASTE THIS HEX into src/meridian/licensing/verify.py")
    print("(replace `_PLACEHOLDER_PUBLIC_KEY = \"00\" * 32` with this exact string)")
    print("=" * 72)
    print(f'_PLACEHOLDER_PUBLIC_KEY: str = "{hex_public}"')
    print("=" * 72)
    print()
    print("PEM form (for your records / off-machine documentation):")
    print(pem_public.decode("utf-8").strip())
    print()
    print("NEXT STEPS:")
    print(f"  1. Save {args.out.name} to your password manager (secure-note attachment).")
    print("  2. Copy to an encrypted USB drive, store offline.")
    print(f"  3. Delete {args.out} from disk once both backups are in place.")
    print("  4. Paste the hex line above into src/meridian/licensing/verify.py.")
    print("  5. Commit the verify.py change. NEVER commit the private key.")


if __name__ == "__main__":
    main()
