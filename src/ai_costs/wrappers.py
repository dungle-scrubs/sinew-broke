"""Real request-forwarding wrappers for OpenAI and Anthropic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ai_costs.models import UsageLedgerEntry
from ai_costs.price_table import compute_anthropic_cost, compute_openai_cost
from ai_costs.providers.base import ProviderError
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import nested_get, resolve_token, safe_int


def load_request_payload(
    body_json: str | None,
    body_file: str | None,
) -> dict[str, Any]:
    """Load a JSON request payload from a string or file."""

    if body_json and body_file:
        raise ProviderError("AIC003", "use either --body-json or --body-file, not both")
    if body_file:
        return json.loads(Path(body_file).read_text())
    if body_json:
        return json.loads(body_json)
    raise ProviderError("AIC003", "request body is required")


def parse_header_pairs(headers: list[str]) -> dict[str, str]:
    """Parse repeated `Name: Value` header arguments."""

    parsed: dict[str, str] = {}
    for header in headers:
        if ":" not in header:
            raise ProviderError("AIC003", f"invalid header '{header}'")
        name, value = header.split(":", 1)
        parsed[name.strip()] = value.strip()
    return parsed


def choose_model(
    explicit_model: str | None,
    payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> str | None:
    """Resolve the model name from CLI flags, request payload, or response payload."""

    return (
        explicit_model
        or payload.get("model")
        or response_payload.get("model")
        or nested_get(response_payload, "model")
    )


def extract_openai_usage(response_payload: dict[str, Any]) -> dict[str, int | None]:
    """Normalize token usage across OpenAI response shapes."""

    usage = response_payload.get("usage") or {}
    return {
        "input_tokens": safe_int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or nested_get(usage, "prompt_tokens")
        ),
        "cached_input_tokens": safe_int(
            nested_get(
                usage,
                "cached_tokens",
                "cached_input_tokens",
                "input_cached_tokens",
            )
        ),
        "output_tokens": safe_int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or nested_get(usage, "completion_tokens")
        ),
    }


def extract_anthropic_usage(response_payload: dict[str, Any]) -> dict[str, int | None]:
    """Normalize token usage from Anthropic messages responses."""

    usage = response_payload.get("usage") or {}
    return {
        "input_tokens": safe_int(usage.get("input_tokens")),
        "cache_read_tokens": safe_int(usage.get("cache_read_input_tokens")),
        "cache_write_tokens": safe_int(usage.get("cache_creation_input_tokens")),
        "output_tokens": safe_int(usage.get("output_tokens")),
    }


def openai_api_key(settings: PluginSettings, explicit_api_key: str | None) -> str:
    """Resolve the OpenAI API key from CLI flags, settings, or env vars."""

    token = resolve_token(
        explicit_api_key or settings.openai_api.token,
        ["OPENAI_API_KEY"],
        [],
        [],
    )
    if not token:
        raise ProviderError("AIC001", "missing OpenAI API key")
    return token


def anthropic_api_key(settings: PluginSettings, explicit_api_key: str | None) -> str:
    """Resolve the Anthropic API key from CLI flags, settings, or env vars."""

    token = resolve_token(
        explicit_api_key or settings.anthropic_api.token,
        ["ANTHROPIC_API_KEY"],
        [],
        [],
    )
    if not token:
        raise ProviderError("AIC001", "missing Anthropic API key")
    return token


def forward_openai_request(
    settings: PluginSettings,
    storage: Storage,
    body_json: str | None,
    body_file: str | None,
    model: str | None,
    endpoint: str,
    base_url: str,
    api_key: str | None,
    account_id: str,
    headers: list[str],
) -> str:
    """Forward an OpenAI request, record a ledger entry, and return raw JSON."""

    payload = load_request_payload(body_json, body_file)
    resolved_key = openai_api_key(settings, api_key)
    request_headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
        **parse_header_pairs(headers),
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}",
            json=payload,
            headers=request_headers,
        )

    if response.status_code >= 400:
        raise ProviderError("AIC003", response.text)

    response_payload = response.json()
    usage = extract_openai_usage(response_payload)
    resolved_model = choose_model(model, payload, response_payload)
    if resolved_model and any(value is not None for value in usage.values()):
        cost_usd = None
        try:
            cost_usd = compute_openai_cost(
                resolved_model,
                usage["input_tokens"],
                usage["cached_input_tokens"],
                usage["output_tokens"],
            )
        except ProviderError:
            cost_usd = None
        storage.insert_ledger_entry(
            UsageLedgerEntry(
                provider="openai_api",
                account_id=account_id,
                model=resolved_model,
                input_tokens=usage["input_tokens"],
                cache_read_tokens=usage["cached_input_tokens"],
                output_tokens=usage["output_tokens"],
                cost_usd=cost_usd,
                source_type="derived_ledger",
                raw_json=response_payload,
            )
        )

    return response.text


def forward_anthropic_request(
    settings: PluginSettings,
    storage: Storage,
    body_json: str | None,
    body_file: str | None,
    model: str | None,
    endpoint: str,
    base_url: str,
    api_key: str | None,
    account_id: str,
    headers: list[str],
) -> str:
    """Forward an Anthropic request, record a ledger entry, and return raw JSON."""

    payload = load_request_payload(body_json, body_file)
    resolved_key = anthropic_api_key(settings, api_key)
    request_headers = {
        "x-api-key": resolved_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        **parse_header_pairs(headers),
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}",
            json=payload,
            headers=request_headers,
        )

    if response.status_code >= 400:
        raise ProviderError("AIC003", response.text)

    response_payload = response.json()
    usage = extract_anthropic_usage(response_payload)
    resolved_model = choose_model(model, payload, response_payload)
    if resolved_model and any(value is not None for value in usage.values()):
        cost_usd = None
        try:
            cost_usd = compute_anthropic_cost(
                resolved_model,
                usage["input_tokens"],
                usage["cache_read_tokens"],
                usage["cache_write_tokens"],
                usage["output_tokens"],
            )
        except ProviderError:
            cost_usd = None
        storage.insert_ledger_entry(
            UsageLedgerEntry(
                provider="anthropic_api",
                account_id=account_id,
                model=resolved_model,
                input_tokens=usage["input_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                output_tokens=usage["output_tokens"],
                cost_usd=cost_usd,
                source_type="derived_ledger",
                raw_json=response_payload,
            )
        )

    return response.text
