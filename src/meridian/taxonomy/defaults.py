"""Locked default taxonomies seeded into every new project.

Authority: CONTEXT.md §5. Each project may extend these via the user-approval
flow; defaults themselves are not edited.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal, NamedTuple

from meridian.db.connection import transaction


class _TradeDefault(NamedTuple):
    value: str
    category_hint: Literal["specialist", "cross_cutting", "vendor"]


# CONTEXT.md §5 — locked specialist trades.
_SPECIALIST = [
    "Electrical",
    "Mechanical",
    "Hydraulic",
    "Fire",
    "Telecommunications",
    "DCS",
    "Security",
    "Carpentry",
    "Formwork",
    "Concrete",
    "Steel",
]

# CONTEXT.md §5 — cross-cutting role.
_CROSS_CUTTING = ["General Contractor / Principal"]

# CONTEXT.md §5 — equipment vendors (per OSE class). Initial seed; new vendors
# added via the taxonomy-approval flow as new OSE classes appear.
_VENDORS = [
    "Chiller Vendor",
    "Generator Vendor",
    "Busway Vendor",
    "PDU Vendor",
    "PTU Vendor",
    "Fan Wall Vendor",
    "HRU Vendor",
    "Kiosk Transformer Vendor",
    "CDU Vendor",
]

DEFAULT_TRADES: list[_TradeDefault] = (
    [_TradeDefault(v, "specialist") for v in _SPECIALIST]
    + [_TradeDefault(v, "cross_cutting") for v in _CROSS_CUTTING]
    + [_TradeDefault(v, "vendor") for v in _VENDORS]
)

# CONTEXT.md §5 — services.
DEFAULT_SERVICES: list[str] = [
    "Power distribution",
    "Lighting",
    "HVAC",
    "Fire detection & suppression",
    "Comms/ICT",
    "Security/access control",
    "Hydraulics",
    "DCS / Controls",
]

# CONTEXT.md §4 / §5 — narrow category vocabulary.
DEFAULT_CATEGORIES: list[str] = [
    "design",
    "procurement",
    "delivery",
    "builders_works",
]

# CONTEXT.md §5 BOD section — initial discipline → service mappings.
DEFAULT_SERVICE_MAPPINGS: list[tuple[str, str | None]] = [
    ("DCE Mechanical Engineering (ME)", "HVAC"),
    ("DCE Electrical Engineering (EE)", "Power distribution"),
    ("DCE Building Automation, Monitoring & Control (BAMC)", "DCS / Controls"),
    ("DCE Fire Protection", "Fire detection & suppression"),
    ("DCE Fire Protection System", "Fire detection & suppression"),
    ("DCE Plumbing", "Hydraulics"),
    ("DCE Security", "Security/access control"),
    ("DCE Telecommunications", "Comms/ICT"),
    ("DCE Telecom", "Comms/ICT"),
    ("Comms / ICT", "Comms/ICT"),
    ("Background Information", None),
    ("Construction Management", None),
    ("Schedule", None),
]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def seed(conn: sqlite3.Connection) -> None:
    """Idempotently seed locked default taxonomies into a fresh project DB."""
    now = _now()
    with transaction(conn):
        for trade in DEFAULT_TRADES:
            conn.execute(
                """
                INSERT OR IGNORE INTO trade_taxonomy
                  (id, value, category_hint, source, created_at)
                VALUES (?, ?, ?, 'default', ?)
                """,
                (_new_id(), trade.value, trade.category_hint, now),
            )

        for service in DEFAULT_SERVICES:
            conn.execute(
                """
                INSERT OR IGNORE INTO service_taxonomy
                  (id, value, source, created_at)
                VALUES (?, ?, 'default', ?)
                """,
                (_new_id(), service, now),
            )

        for category in DEFAULT_CATEGORIES:
            conn.execute(
                """
                INSERT OR IGNORE INTO category_taxonomy
                  (id, value, source, created_at)
                VALUES (?, ?, 'default', ?)
                """,
                (_new_id(), category, now),
            )

        # Resolve service ids and seed mappings.
        rows = conn.execute("SELECT id, value FROM service_taxonomy").fetchall()
        service_id_by_value = {r["value"]: r["id"] for r in rows}

        for disc_text, service_value in DEFAULT_SERVICE_MAPPINGS:
            service_id = service_id_by_value.get(service_value) if service_value else None
            conn.execute(
                """
                INSERT OR IGNORE INTO service_mapping
                  (id, disc_section_text, service_id, confirmed_at)
                VALUES (?, ?, ?, ?)
                """,
                (_new_id(), disc_text, service_id, now),
            )


__all__ = [
    "DEFAULT_CATEGORIES",
    "DEFAULT_SERVICES",
    "DEFAULT_SERVICE_MAPPINGS",
    "DEFAULT_TRADES",
    "seed",
]
