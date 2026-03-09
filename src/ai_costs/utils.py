"""Shared helpers for parsing provider payloads and local auth state."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


def safe_float(value: Any) -> float | None:
    """Convert a loosely typed value to float when possible."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    """Convert a loosely typed value to int when possible."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""

    return datetime.now(UTC).isoformat()


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into a UTC-aware datetime."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def age_minutes(iso_timestamp: str) -> int:
    """Return the age of an ISO-8601 timestamp in minutes."""

    delta = datetime.now(UTC) - parse_timestamp(iso_timestamp)
    return max(int(delta.total_seconds() // 60), 0)


def load_json_file(path: str | Path) -> dict[str, Any] | None:
    """Load a JSON file if it exists and parses successfully."""

    file_path = Path(path).expanduser()
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def nested_get(payload: dict[str, Any], *keys: str) -> Any:
    """Return the first present top-level or nested key match."""

    for key in keys:
        if key in payload:
            return payload[key]
    for value in payload.values():
        if isinstance(value, dict):
            nested = nested_get(value, *keys)
            if nested is not None:
                return nested
    return None


def normalize_timestamp(value: Any) -> str | None:
    """Normalize a string, integer, or float timestamp into ISO-8601 UTC."""

    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC).isoformat()
    return None


@lru_cache(maxsize=32)
def keychain_secret(service: str, account: str | None = None) -> str | None:
    """Read a generic-password secret from macOS Keychain."""

    command = ["security", "find-generic-password", "-w", "-s", service]
    if account:
        command.extend(["-a", account])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def token_from_payload(payload: dict[str, Any], json_keys: Iterable[str]) -> str | None:
    """Return the first matching token-like value from a JSON payload."""

    for json_key in json_keys:
        value = payload.get(json_key)
        if isinstance(value, str) and value:
            return value
        nested_value = nested_get(payload, json_key)
        if isinstance(nested_value, str) and nested_value:
            return nested_value
    return None


def resolve_token(
    explicit_token: str | None,
    env_names: Iterable[str],
    file_candidates: Iterable[str | Path],
    json_keys: Iterable[str],
    keychain_services: Iterable[str] = (),
    keychain_accounts: Iterable[str] = (),
    allow_generic_keychain: bool = True,
) -> str | None:
    """Resolve a secret from direct settings, env vars, files, or Keychain."""

    if explicit_token:
        return explicit_token

    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value

    for file_candidate in file_candidates:
        payload = load_json_file(file_candidate)
        if not payload:
            continue
        token = token_from_payload(payload, json_keys)
        if token:
            return token

    account_candidates = [account for account in keychain_accounts if account]
    for service in keychain_services:
        for account in account_candidates:
            secret = keychain_secret(service, account)
            if not secret:
                continue
            try:
                payload = json.loads(secret)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                token = token_from_payload(payload, json_keys)
                if token:
                    return token
            if secret:
                return secret
        if not allow_generic_keychain:
            continue
        secret = keychain_secret(service)
        if not secret:
            continue
        try:
            payload = json.loads(secret)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            token = token_from_payload(payload, json_keys)
            if token:
                return token
        if secret:
            return secret

    return None


def percent_used(used: float | None, total: float | None) -> float | None:
    """Compute used percent when both used and total are available."""

    if used is None or total in (None, 0):
        return None
    return max(min((used / total) * 100.0, 100.0), 0.0)
