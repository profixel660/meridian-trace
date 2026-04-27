"""Tests for the SQLite ``busy_timeout`` PRAGMA wired into ``connect()``.

Round-15 hardening for Hazard 2 in ``docs/concurrency-analysis.md`` —
``connect()`` now sets ``PRAGMA busy_timeout`` so transient writer
collisions wait instead of immediately raising ``database is locked``.

These tests verify the PRAGMA is actually applied (it's a low-level
engine hint that's easy to drop silently in a refactor).
"""

from __future__ import annotations

import pytest

from meridian.db.connection import connect


def test_busy_timeout_set_after_connect() -> None:
    """The default ``busy_timeout_ms=5000`` must be applied to the connection.

    Round-15 default — a 5-second wait is the right balance for
    short reviewer writes (Hazard 2 mitigation).
    """
    conn = connect(":memory:")
    try:
        (value,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()
    assert value == 5000


def test_busy_timeout_overridable() -> None:
    """Callers can override ``busy_timeout_ms`` (used by long-running writers)."""
    conn = connect(":memory:", busy_timeout_ms=1234)
    try:
        (value,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()
    assert value == 1234


def test_busy_timeout_rejects_negative() -> None:
    """Negative values are a programmer error — fail fast at ``connect()``."""
    with pytest.raises(ValueError, match="busy_timeout_ms must be >= 0"):
        connect(":memory:", busy_timeout_ms=-1)


def test_busy_timeout_zero_disables_wait() -> None:
    """``busy_timeout_ms=0`` is the legacy "raise immediately" behaviour."""
    conn = connect(":memory:", busy_timeout_ms=0)
    try:
        (value,) = conn.execute("PRAGMA busy_timeout").fetchone()
    finally:
        conn.close()
    assert value == 0
