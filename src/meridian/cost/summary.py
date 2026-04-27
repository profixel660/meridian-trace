"""Post-run cost summary — pure SQL aggregations over `llm_call.cost_cents`.

No LLM calls are made here; this is a read-only roll-up of what's already in
the project's SQLite store. `cost_cents` is populated by the LLM client via
`litellm.completion_cost(...)` (see meridian.llm.client). Local-provider calls
typically have `cost_cents = 0`; calls that fail before usage data lands have
`cost_cents = NULL` — those are surfaced separately as `unknown_cost_cents`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class PurposeRow:
    purpose: str
    n_calls: int
    cost_cents: int


@dataclass
class ProviderModelRow:
    provider: str
    model: str
    n_calls: int
    cost_cents: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


@dataclass
class JobRow:
    job_id: str
    started_at: str | None
    finished_at: str | None
    status: str | None
    n_calls: int
    cost_cents: int


@dataclass
class CostSummary:
    total_cents: int
    n_calls: int
    by_purpose: list[PurposeRow] = field(default_factory=list)
    by_provider_model: list[ProviderModelRow] = field(default_factory=list)
    by_job: list[JobRow] = field(default_factory=list)
    unknown_cost_cents: int = 0  # count of llm_call rows where cost_cents IS NULL


def summarise_project_cost(conn: sqlite3.Connection) -> CostSummary:
    """Aggregate realised cost across every llm_call in the project."""
    head = conn.execute(
        """
        SELECT
            COALESCE(SUM(cost_cents), 0) AS total_cents,
            COUNT(*)                     AS n_calls,
            SUM(CASE WHEN cost_cents IS NULL THEN 1 ELSE 0 END) AS unknown
        FROM llm_call
        """
    ).fetchone()
    total_cents = int(head["total_cents"] or 0)
    n_calls = int(head["n_calls"] or 0)
    unknown = int(head["unknown"] or 0)

    by_purpose = [
        PurposeRow(
            purpose=row["purpose"],
            n_calls=int(row["n_calls"]),
            cost_cents=int(row["cost_cents"] or 0),
        )
        for row in conn.execute(
            """
            SELECT purpose,
                   COUNT(*)                  AS n_calls,
                   COALESCE(SUM(cost_cents), 0) AS cost_cents
            FROM llm_call
            GROUP BY purpose
            ORDER BY cost_cents DESC, purpose
            """
        ).fetchall()
    ]

    by_provider_model = [
        ProviderModelRow(
            provider=row["provider"],
            model=row["model"],
            n_calls=int(row["n_calls"]),
            cost_cents=int(row["cost_cents"] or 0),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            cache_read_tokens=int(row["cache_read_tokens"] or 0),
            cache_write_tokens=int(row["cache_write_tokens"] or 0),
        )
        for row in conn.execute(
            """
            SELECT provider,
                   model,
                   COUNT(*)                              AS n_calls,
                   COALESCE(SUM(cost_cents), 0)          AS cost_cents,
                   COALESCE(SUM(prompt_tokens), 0)       AS input_tokens,
                   COALESCE(SUM(completion_tokens), 0)   AS output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0)   AS cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens), 0)  AS cache_write_tokens
            FROM llm_call
            GROUP BY provider, model
            ORDER BY cost_cents DESC, provider, model
            """
        ).fetchall()
    ]

    by_job = [
        JobRow(
            job_id=row["job_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            n_calls=int(row["n_calls"]),
            cost_cents=int(row["cost_cents"] or 0),
        )
        for row in conn.execute(
            """
            SELECT
                lc.job_id                    AS job_id,
                ej.started_at                AS started_at,
                ej.finished_at               AS finished_at,
                ej.status                    AS status,
                COUNT(*)                     AS n_calls,
                COALESCE(SUM(lc.cost_cents), 0) AS cost_cents
            FROM llm_call lc
            LEFT JOIN extraction_job ej ON ej.id = lc.job_id
            WHERE lc.job_id IS NOT NULL
            GROUP BY lc.job_id, ej.started_at, ej.finished_at, ej.status
            ORDER BY ej.started_at DESC NULLS LAST, lc.job_id
            """
        ).fetchall()
    ]

    return CostSummary(
        total_cents=total_cents,
        n_calls=n_calls,
        by_purpose=by_purpose,
        by_provider_model=by_provider_model,
        by_job=by_job,
        unknown_cost_cents=unknown,
    )


__all__ = [
    "CostSummary",
    "JobRow",
    "ProviderModelRow",
    "PurposeRow",
    "summarise_project_cost",
]
