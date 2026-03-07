"""Versioned price-table loading and token-cost calculation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ai_costs.providers.base import ProviderError


@lru_cache(maxsize=8)
def load_price_table(provider: str) -> dict[str, Any]:
    """Load a provider price table from the bundled JSON files."""

    path = Path(__file__).with_name("pricing") / f"{provider}.json"
    if not path.exists():
        raise ProviderError("AIC005", f"missing pricing table for {provider}")
    return json.loads(path.read_text())


def resolve_model_pricing(provider: str, model: str) -> dict[str, float]:
    """Resolve the pricing record for one model."""

    table = load_price_table(provider)
    models = table.get("models", {})
    pricing = models.get(model)
    if not isinstance(pricing, dict):
        raise ProviderError("AIC005", f"missing pricing for model '{model}'")
    return {key: float(value) for key, value in pricing.items()}


def per_million(rate: float, tokens: int | None) -> float:
    """Convert a per-million token rate into a concrete USD cost."""

    if tokens is None:
        return 0.0
    return (tokens / 1_000_000.0) * rate


def compute_openai_cost(
    model: str,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
) -> float:
    """Compute OpenAI cost from the versioned price table."""

    pricing = resolve_model_pricing("openai", model)
    return round(
        per_million(pricing["input_per_million_usd"], input_tokens)
        + per_million(
            pricing.get("cached_input_per_million_usd", 0.0), cached_input_tokens
        )
        + per_million(pricing["output_per_million_usd"], output_tokens),
        6,
    )


def compute_anthropic_cost(
    model: str,
    input_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    output_tokens: int | None,
) -> float:
    """Compute Anthropic cost from the versioned price table."""

    pricing = resolve_model_pricing("anthropic", model)
    return round(
        per_million(pricing["input_per_million_usd"], input_tokens)
        + per_million(pricing.get("cache_read_per_million_usd", 0.0), cache_read_tokens)
        + per_million(
            pricing.get("cache_write_per_million_usd", 0.0), cache_write_tokens
        )
        + per_million(pricing["output_per_million_usd"], output_tokens),
        6,
    )
