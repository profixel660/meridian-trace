"""Production-client smoke tests for licensing / updates / crash modules.

These modules talk to placeholder external services until the real CDN /
endpoint / pubkey are wired in (CONTEXT.md §3.5/3.6/3.8). The contract that
must hold *now* is the safe-default behaviour:

  * malformed input never raises — returns a structured ``malformed`` status,
  * unreachable network endpoints return ``None`` rather than crashing,
  * the placeholder crash endpoint refuses to send (loud failure mode),
  * the secret-redaction pass strips common credential shapes from any
    payload assembled for sending.

Every test is fully offline. The single network test points at TCP port 1
(IANA-reserved ``tcpmux``), guaranteed unreachable in any deployment.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meridian.crash.sender import (
    _PLACEHOLDER_CRASH_ENDPOINT,
    SendResult,
    prepare_crash_payload,
    send_crash_report,
    set_crash_opt_in,
)
from meridian.licensing.verify import (
    _PLACEHOLDER_PUBLIC_KEY,
    LicenseStatus,
    verify_license,
)
from meridian.updates.client import check_for_updates

# ---------------------------------------------------------------------------
# Licensing
# ---------------------------------------------------------------------------


def test_license_malformed_returns_malformed_status(tmp_projects_dir: Path) -> None:
    """A non-license string must surface as ``LicenseStatus.malformed`` — never raise."""
    result = verify_license("not-a-real-license-string")
    assert result.status == LicenseStatus.malformed, (
        f"unparseable input should map to 'malformed', got {result.status!r}"
    )
    assert result.payload is None
    assert isinstance(result.message, str) and result.message, (
        "malformed verdict must include a human-readable message"
    )


def test_license_unreachable_pubkey_uses_placeholder(tmp_projects_dir: Path) -> None:
    """Confirm the all-zeros placeholder pubkey constant exists and behaves safely.

    Per the module docstring + ``_PLACEHOLDER_PUBLIC_KEY``: the placeholder
    is 32 zero bytes so any real signature attempt against it is guaranteed
    to fail (safe default — refuses every license until a real key is wired
    in). Passing a structurally well-formed but unsigned license should
    therefore route to ``signature_invalid``, never silently to ``valid``.
    """
    # Constant invariant: 64 hex chars of zero.
    assert _PLACEHOLDER_PUBLIC_KEY == "00" * 32, (
        "placeholder pubkey should be the all-zeros sentinel until §3.8 is wired"
    )

    # Build a structurally-valid license envelope: base64url(payload).base64url(sig).
    # The signature is junk — we only care that the verifier reaches the
    # cryptography step and rejects it cleanly.
    import base64

    payload_json = json.dumps({
        "customer_id": "cust-test",
        "customer_name": "Test Customer",
        "plan": "pro",
        "issued_at": "2025-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "features": [],
    })
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).rstrip(b"=").decode("ascii")
    sig_b64 = base64.urlsafe_b64encode(b"\x00" * 64).rstrip(b"=").decode("ascii")
    license_string = f"{payload_b64}.{sig_b64}"

    result = verify_license(license_string)
    # Two acceptable safe-default outcomes:
    #   - signature_invalid: cryptography is installed and rejected the sig.
    #   - malformed: cryptography is missing — module degrades to malformed
    #                with an install-hint message (see _load_ed25519).
    assert result.status in {LicenseStatus.signature_invalid, LicenseStatus.malformed}, (
        f"placeholder pubkey must NEVER produce a 'valid' verdict; "
        f"got {result.status!r}"
    )


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------


def test_updates_check_unreachable_returns_none(tmp_projects_dir: Path) -> None:
    """Pointing the manifest fetch at TCP port 1 must return None, never raise."""
    # Port 1 (tcpmux) is reserved + guaranteed-unreachable on every platform.
    # Use a 2s timeout so this test stays well under the suite budget.
    result = check_for_updates(manifest_url="http://127.0.0.1:1/", timeout_s=2)
    assert result is None, (
        f"unreachable manifest URL must return None, got {result!r}"
    )


def test_updates_check_invalid_json_returns_none(
    tmp_projects_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200-OK with non-JSON body must return None, never raise."""

    class _FakeResp:
        """Minimal urllib response stub — supports context-manager + .read()."""

        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_urlopen(req: object, timeout: float = 0) -> _FakeResp:  # noqa: ARG001
        return _FakeResp(b"<html>not json at all</html>")

    monkeypatch.setattr(
        "meridian.updates.client.urllib.request.urlopen", _fake_urlopen
    )
    result = check_for_updates(manifest_url="http://example.invalid/", timeout_s=2)
    assert result is None, (
        f"non-JSON body must degrade to None, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Crash sender
# ---------------------------------------------------------------------------


def _write_fake_dossier(tmp_path: Path, payload: dict) -> Path:
    """Materialise a crash dossier JSON file under tmp_path and return its path."""
    dossier_path = tmp_path / "crash_2026-04-27T10-00-00.json"
    dossier_path.write_text(json.dumps(payload), encoding="utf-8")
    return dossier_path


def test_crash_send_refuses_placeholder_url(
    tmp_projects_dir: Path,
    tmp_path: Path,
) -> None:
    """``send_crash_report`` must refuse to POST to the placeholder URL."""
    # Opt-in must be on; otherwise the opt-in gate fires first and we'd be
    # asserting the wrong refusal path.
    set_crash_opt_in(True)

    dossier = _write_fake_dossier(
        tmp_path,
        {
            "exception": "RuntimeError",
            "message": "synthetic test failure",
            "trace": [],
        },
    )

    result = send_crash_report(dossier, endpoint_url=_PLACEHOLDER_CRASH_ENDPOINT)
    assert isinstance(result, SendResult)
    assert result.success is False, (
        "placeholder endpoint must refuse to send (loud failure mode)"
    )
    assert result.status_code is None
    assert "placeholder" in result.message.lower(), (
        f"refuse message should explain *why* it refused; got {result.message!r}"
    )


def test_crash_payload_redacts_secrets(
    tmp_projects_dir: Path,
    tmp_path: Path,
) -> None:
    """``prepare_crash_payload`` must strip common credential shapes."""
    fake_openai = "sk-test-fake-key-xxxxxxxxxxxxxxxxxx"  # length>=16 to hit the regex
    fake_bearer = "Bearer abc.def.ghi.jklmnop"

    dossier = _write_fake_dossier(
        tmp_path,
        {
            "exception": "ValueError",
            "message": f"call failed with {fake_openai}",
            "headers": {"Authorization": fake_bearer},
            "trace": [
                {"file": "x.py", "line": 1, "code": f"key = '{fake_openai}'"},
            ],
        },
    )

    payload = prepare_crash_payload(dossier)
    serialised = json.dumps(payload)

    assert fake_openai not in serialised, (
        f"openai-shaped key must be redacted; payload still contains "
        f"{fake_openai!r}"
    )
    assert fake_bearer not in serialised, (
        f"bearer token must be redacted; payload still contains "
        f"{fake_bearer!r}"
    )
    # Sanity: the redaction marker should appear at least once.
    assert "[REDACTED]" in serialised, (
        "expected the [REDACTED] sentinel to appear in the redacted payload"
    )


# Silence unused-import lint — kept for symmetry with the other e2e files.
_ = io
_ = urllib.error
_ = urllib.request
