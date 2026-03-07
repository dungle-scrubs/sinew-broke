"""Base provider abstractions and shared error types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ai_costs.models import AccountSnapshot
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage


class ProviderError(Exception):
    """Provider-specific failure with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AdapterSpec:
    """Static metadata for one provider adapter."""

    provider: str
    display_name: str


class ProviderAdapter(Protocol):
    """Interface implemented by all provider adapters."""

    spec: AdapterSpec

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        """Fetch and normalize provider data."""


def build_client() -> httpx.Client:
    """Create the shared HTTP client used by provider adapters."""

    return httpx.Client(timeout=10.0, follow_redirects=True)


def get_json(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    unauthorized_message: str,
) -> dict[str, Any]:
    """Fetch JSON and map auth failures onto stable provider error codes."""

    response = client.get(url, headers=headers)
    if response.status_code in {401, 403}:
        raise ProviderError("AIC002", unauthorized_message)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise ProviderError("AIC003", str(error)) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise ProviderError("AIC003", "provider returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ProviderError("AIC003", "provider returned non-object JSON")
    return payload
