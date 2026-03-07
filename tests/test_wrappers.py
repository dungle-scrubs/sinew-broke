from __future__ import annotations

import json
from pathlib import Path

from ai_costs.price_table import compute_anthropic_cost, compute_openai_cost
from ai_costs.wrappers import (
    extract_anthropic_usage,
    extract_openai_usage,
    load_request_payload,
)


def test_load_request_payload_from_file(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"model": "gpt-5", "input": "hello"}))

    payload = load_request_payload(None, str(path))

    assert payload["model"] == "gpt-5"


def test_extract_openai_usage_supports_responses_shape() -> None:
    payload = {
        "usage": {
            "input_tokens": 100,
            "input_token_details": {"cached_tokens": 25},
            "output_tokens": 20,
        }
    }

    usage = extract_openai_usage(payload)

    assert usage == {
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 20,
    }


def test_extract_anthropic_usage_supports_cache_fields() -> None:
    payload = {
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
            "output_tokens": 20,
        }
    }

    usage = extract_anthropic_usage(payload)

    assert usage == {
        "input_tokens": 100,
        "cache_read_tokens": 50,
        "cache_write_tokens": 10,
        "output_tokens": 20,
    }


def test_compute_openai_cost_uses_price_table() -> None:
    cost = compute_openai_cost("gpt-5", 1_000_000, 1_000_000, 1_000_000)

    assert cost == 11.375


def test_compute_anthropic_cost_uses_price_table() -> None:
    cost = compute_anthropic_cost(
        "claude-sonnet-4-5",
        1_000_000,
        1_000_000,
        1_000_000,
        1_000_000,
    )

    assert cost == 22.05
