"""Per-provider/model token-rate table for pre-run cost estimation.

Rates are stored in **cents per 1,000,000 tokens** (cents/M) — the same units
the Anthropic / OpenAI public pricing pages use, scaled to integer cents to
avoid float drift. Conversion to actual cost:

    cost_cents = tokens * rate_cents_per_million / 1_000_000

Local providers (anything in `LOCAL_PROVIDERS`) intentionally have rates of 0 —
the spend is electrons + GPU time, not API dollars.

If the (provider, model) pair is unknown, `lookup_rate` returns `None` and the
preview surfaces "rate unknown" rather than guessing. This is deliberate: a
silent guess on an unknown model can mislead the user about real spend.

Rate sources (verified at the time of writing — please re-verify against the
provider's current pricing page if you suspect drift):
- Anthropic Claude pricing: https://www.anthropic.com/pricing
- OpenAI API pricing: https://openai.com/api/pricing/
"""

from __future__ import annotations

from meridian.config import LOCAL_PROVIDERS

# (provider, model) -> (input_cents_per_million, output_cents_per_million)
_RATES: dict[tuple[str, str], tuple[int, int]] = {
    # ── Anthropic ────────────────────────────────────────────────────────────
    # Sonnet 4.6 — $3 / $15 per million tokens
    ("anthropic", "claude-sonnet-4-6"): (300, 1500),
    # Haiku 4.5 — $0.80 / $4 per million tokens
    ("anthropic", "claude-haiku-4-5-20251001"): (80, 400),
    # Opus 4.7 — $15 / $75 per million tokens
    ("anthropic", "claude-opus-4-7"): (1500, 7500),
    # ── OpenAI ───────────────────────────────────────────────────────────────
    # GPT-4o — $2.50 / $10 per million tokens
    ("openai", "gpt-4o"): (250, 1000),
    # GPT-4o-mini — $0.15 / $0.60 per million tokens
    ("openai", "gpt-4o-mini"): (15, 60),
}


def lookup_rate(provider: str, model: str) -> tuple[int, int] | None:
    """Return (input_cents_per_million, output_cents_per_million) or None.

    Local providers always return (0, 0) regardless of the model id — local
    inference is not metered in dollars. Unknown cloud (provider, model) pairs
    return None so callers can surface "rate unknown" rather than guessing.
    """
    if provider in LOCAL_PROVIDERS:
        return (0, 0)
    return _RATES.get((provider, model))


__all__ = ["lookup_rate"]
