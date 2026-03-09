"""Claude Code subscription adapter via Anthropic OAuth usage."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ai_costs.models import AccountSnapshot, WindowMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client, get_json
from ai_costs.settings import (
    DEFAULT_CLAUDE_CONFIG_DIR,
    PluginSettings,
    ProviderSettings,
    discover_claude_code_configs,
    single_provider_config,
)
from ai_costs.storage import Storage
from ai_costs.utils import (
    keychain_secret,
    load_json_file,
    nested_get,
    normalize_timestamp,
    now_iso,
    resolve_token,
    safe_float,
)

WINDOW_KEYS = [
    ("five_hour", "5h"),
    ("seven_day", "7d"),
    ("seven_day_sonnet", "7d sonnet"),
    ("seven_day_opus", "7d opus"),
]
CLAUDE_TOKEN_ENV_NAMES = ["ANTHROPIC_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]
CLAUDE_STATUS_ENV_BLACKLIST = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
]
CLAUDE_TOKEN_JSON_KEYS = ["access", "access_token", "accessToken", "token", "oauth_token"]
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"


class ClaudeCodeAdapter:
    """Fetch Claude Code subscription usage from Anthropic OAuth."""

    spec = AdapterSpec(provider="claude_code", display_name="Claude Code")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        """Fetch one Claude Code account snapshot.

        :param settings: Plugin settings scoped to one Claude account.
        :param storage: Snapshot storage backend.
        :returns: Current Claude Code snapshot.
        :throws ProviderError: If the Claude profile is not authenticated.
        """

        config = single_provider_config(settings.claude_code)
        if not config.enabled:
            return AccountSnapshot(
                provider=self.spec.provider,
                account_id=config.account_id,
                display_name=self.spec.display_name,
                capabilities=["subscription_window"],
                source_type="oauth_usage",
                status="disabled",
                notes=["provider disabled"],
            )

        config_dir = claude_config_dir(config)
        metadata = claude_profile_metadata(config_dir)
        auth_status = claude_auth_status(config_dir)
        token = resolve_token(
            config.token,
            CLAUDE_TOKEN_ENV_NAMES,
            claude_token_files(config, config_dir),
            CLAUDE_TOKEN_JSON_KEYS,
            keychain_services=[CLAUDE_KEYCHAIN_SERVICE],
            keychain_accounts=claude_keychain_accounts(metadata, auth_status),
            allow_generic_keychain=config_dir.name == Path(DEFAULT_CLAUDE_CONFIG_DIR).name,
        )
        notes = claude_profile_notes(metadata, auth_status, config_dir)
        if not token:
            if notes != [f"profile {config_dir.name}"]:
                return AccountSnapshot(
                    provider=self.spec.provider,
                    account_id=config.account_id,
                    display_name=self.spec.display_name,
                    capabilities=["subscription_window"],
                    source_type="oauth_usage",
                    status="incomplete",
                    notes=notes,
                )
            raise ProviderError(
                "AIC001",
                "missing Claude Code OAuth token; run `claude login` or configure claude_code.auth_file",
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        }
        with build_client() as client:
            payload = get_json(
                client,
                "https://api.anthropic.com/api/oauth/usage",
                headers,
                "Claude Code auth expired; run `claude login` again",
            )

        windows: list[WindowMetrics] = []
        for key, label in WINDOW_KEYS:
            raw_window = nested_get(payload, key)
            if not isinstance(raw_window, dict):
                continue
            used = safe_float(
                nested_get(raw_window, "used_percent", "percent_used", "utilization")
            )
            if used is None:
                used_value = safe_float(nested_get(raw_window, "used", "used_count"))
                limit_value = safe_float(nested_get(raw_window, "limit", "max"))
                if used_value is not None and limit_value not in (None, 0):
                    used = (used_value / limit_value) * 100.0
            windows.append(
                WindowMetrics(
                    kind=label,
                    used_percent=used,
                    resets_at=normalize_timestamp(
                        nested_get(raw_window, "resets_at", "reset_at", "end_at")
                    ),
                )
            )

        if not windows:
            raise ProviderError("AIC003", "unsupported Claude OAuth payload")

        extra_spend = safe_float(
            nested_get(payload, "extra_usage", "spend", "amount_usd")
        )
        notes = ["oauth_usage", *notes]
        if extra_spend is not None:
            notes.append(f"extra usage ${extra_spend:.2f}")

        return AccountSnapshot(
            provider=self.spec.provider,
            account_id=config.account_id,
            display_name=self.spec.display_name,
            capabilities=["subscription_window"],
            source_type="oauth_usage",
            status="ok",
            updated_at=now_iso(),
            windows=windows,
            notes=notes,
            raw_payload=payload,
        )


def claude_auth_diagnostics(settings: PluginSettings) -> list[dict[str, Any]]:
    """Inspect Claude auth resolution for every configured profile.

    :param settings: Plugin settings containing Claude Code config.
    :returns: One diagnostic payload per Claude profile.
    """

    raw = settings.claude_code
    if isinstance(raw, list):
        configs = [config.model_copy(update={"enabled": True}) for config in raw]
    else:
        configs = discover_claude_code_configs(raw.model_copy(update={"enabled": True}))
    return [claude_profile_diagnostic(config) for config in configs]


def claude_profile_diagnostic(config: ProviderSettings) -> dict[str, Any]:
    """Collect token-resolution diagnostics for one Claude profile.

    :param config: One Claude provider configuration.
    :returns: Diagnostic payload with source inspection details.
    """

    config_dir = claude_config_dir(config)
    metadata = claude_profile_metadata(config_dir)
    auth_status_result = claude_auth_status_result(config_dir)
    auth_status = auth_status_result["payload"]
    file_candidates = claude_token_files(config, config_dir)
    keychain_accounts = claude_keychain_accounts(metadata, auth_status)
    allow_generic_keychain = config_dir.name == Path(DEFAULT_CLAUDE_CONFIG_DIR).name

    env_sources, env_resolution = inspect_env_token_sources(CLAUDE_TOKEN_ENV_NAMES)
    file_sources, file_resolution = inspect_file_token_sources(
        file_candidates,
        CLAUDE_TOKEN_JSON_KEYS,
    )
    keychain_sources, keychain_resolution = inspect_keychain_token_sources(
        CLAUDE_KEYCHAIN_SERVICE,
        keychain_accounts,
        CLAUDE_TOKEN_JSON_KEYS,
        allow_generic_keychain,
    )
    resolution_source = (
        "explicit"
        if bool(config.token)
        else env_resolution or file_resolution or keychain_resolution
    )

    return {
        "accountId": config.account_id,
        "configDir": str(config_dir),
        "authStatus": auth_status,
        "authStatusCheck": {
            "returncode": auth_status_result["returncode"],
            "hasStdout": auth_status_result["hasStdout"],
        },
        "metadata": {
            "hasOauthAccount": isinstance(metadata.get("oauthAccount"), dict),
            "email": nested_get(metadata, "emailAddress"),
            "organizationName": nested_get(metadata, "organizationName"),
            "billingType": nested_get(metadata, "billingType"),
        },
        "resolution": {
            "resolved": resolution_source is not None,
            "source": resolution_source,
            "allowGenericKeychain": allow_generic_keychain,
        },
        "sources": {
            "explicitToken": {"configured": bool(config.token)},
            "environment": env_sources,
            "files": file_sources,
            "keychain": {
                "service": CLAUDE_KEYCHAIN_SERVICE,
                "accounts": keychain_accounts,
                "checks": keychain_sources,
            },
        },
    }


def claude_config_dir(config: ProviderSettings) -> Path:
    """Resolve the Claude config directory for one provider config.

    :param config: Provider configuration.
    :returns: Resolved Claude config directory.
    """

    if config.config_dir:
        return Path(config.config_dir).expanduser()
    if config.auth_file:
        return Path(config.auth_file).expanduser().parent
    return Path(DEFAULT_CLAUDE_CONFIG_DIR).expanduser()


def claude_auth_status_result(config_dir: Path) -> dict[str, Any]:
    """Return raw profile-scoped auth status command metadata.

    :param config_dir: Claude config directory to query.
    :returns: Parsed auth payload plus command metadata.
    """

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    for env_name in CLAUDE_STATUS_ENV_BLACKLIST:
        env.pop(env_name, None)
    result = subprocess.run(
        ["claude", "auth", "status", "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    stdout = (result.stdout or "").strip()
    payload: dict[str, Any] = {}
    if stdout:
        try:
            loaded = json.loads(stdout)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}
    return {
        "returncode": result.returncode,
        "hasStdout": bool(stdout),
        "payload": payload,
    }


def claude_auth_status(config_dir: Path) -> dict[str, Any]:
    """Return sanitized `claude auth status --json` output for one profile.

    :param config_dir: Claude config directory to query.
    :returns: Parsed auth status payload, or an empty dict on failure.
    """

    return claude_auth_status_result(config_dir)["payload"]


def claude_token_files(config: ProviderSettings, config_dir: Path) -> list[str | Path]:
    """Return Claude token file candidates in descending priority order.

    :param config: Provider configuration.
    :param config_dir: Claude config directory.
    :returns: Candidate token file paths.
    """

    candidates: list[str | Path] = []
    if config.auth_file:
        candidates.append(config.auth_file)
    candidates.extend(
        [
            config_dir / ".credentials.json",
            config_dir / ".claude.json",
            claude_tallow_auth_file(config_dir),
        ]
    )

    unique: list[str | Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(Path(candidate).expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def claude_tallow_auth_file(config_dir: Path) -> Path:
    """Map a Claude config directory to the matching Tallow auth file.

    :param config_dir: Claude config directory.
    :returns: Matching Tallow auth.json path.
    """

    tallow_dir = config_dir.with_name(config_dir.name.replace(".claude", ".tallow", 1))
    return tallow_dir / "auth.json"


def claude_profile_metadata(config_dir: Path) -> dict[str, Any]:
    """Load Claude profile metadata from the profile-local state file.

    :param config_dir: Claude config directory.
    :returns: Parsed `.claude.json` content.
    """

    return load_json_file(config_dir / ".claude.json") or {}


def claude_keychain_accounts(
    metadata: dict[str, Any], auth_status: dict[str, Any]
) -> list[str]:
    """Return likely Keychain account identifiers for one Claude profile.

    :param metadata: Profile-local Claude metadata.
    :param auth_status: Parsed `claude auth status --json` output.
    :returns: Candidate account identifiers.
    """

    oauth_account = metadata.get("oauthAccount")
    candidates = [
        auth_status.get("email"),
        auth_status.get("orgId"),
    ]
    if isinstance(oauth_account, dict):
        candidates.extend(
            [
                oauth_account.get("accountUuid"),
                oauth_account.get("emailAddress"),
                oauth_account.get("organizationUuid"),
            ]
        )
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def claude_profile_notes(
    metadata: dict[str, Any], auth_status: dict[str, Any], config_dir: Path
) -> list[str]:
    """Build human-readable notes from Claude profile metadata and auth status.

    :param metadata: Profile-local Claude metadata.
    :param auth_status: Parsed `claude auth status --json` output.
    :param config_dir: Claude config directory.
    :returns: Human-readable note list.
    """

    notes = [f"profile {config_dir.name}"]
    oauth_account = metadata.get("oauthAccount")
    seen: set[str] = {notes[0]}

    def add_note(value: str | None, prefix: str = "") -> None:
        """Append one note if it is present and not already included.

        :param value: Raw note value.
        :param prefix: Optional prefix label.
        :returns: Nothing.
        """

        if not isinstance(value, str) or not value:
            return
        note = f"{prefix}{value}" if prefix else value
        if note in seen:
            return
        seen.add(note)
        notes.append(note)

    if isinstance(oauth_account, dict):
        add_note(oauth_account.get("emailAddress"))
        add_note(oauth_account.get("organizationName"))
        add_note(oauth_account.get("billingType"), "billing ")

    if auth_status.get("loggedIn") is True:
        add_note(auth_status.get("email"))
        add_note(auth_status.get("orgName") or auth_status.get("orgId"))
        add_note(auth_status.get("subscriptionType"), "subscription ")
        add_note(auth_status.get("authMethod"), "auth ")
    elif auth_status.get("loggedIn") is False:
        add_note("logged out")

    return notes


def inspect_env_token_sources(env_names: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    """Inspect environment-variable token candidates.

    :param env_names: Candidate environment variable names.
    :returns: Per-variable diagnostics and the winning source label.
    """

    diagnostics: list[dict[str, Any]] = []
    resolution: str | None = None
    for env_name in env_names:
        present = bool(os.environ.get(env_name))
        diagnostics.append({"name": env_name, "present": present})
        if present and resolution is None:
            resolution = f"env:{env_name}"
    return diagnostics, resolution


def inspect_file_token_sources(
    file_candidates: list[str | Path], json_keys: list[str]
) -> tuple[list[dict[str, Any]], str | None]:
    """Inspect file-based token candidates without exposing secret values.

    :param file_candidates: Candidate JSON file paths.
    :param json_keys: Token-like JSON keys to look for.
    :returns: Per-file diagnostics and the winning source label.
    """

    diagnostics: list[dict[str, Any]] = []
    resolution: str | None = None
    for candidate in file_candidates:
        path = Path(candidate).expanduser()
        payload = load_json_file(path)
        token_key = first_token_key(payload, json_keys) if payload else None
        diagnostics.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "json": payload is not None,
                "hasToken": token_key is not None,
                "tokenKey": token_key,
            }
        )
        if token_key is not None and resolution is None:
            resolution = f"file:{path}"
    return diagnostics, resolution


def inspect_keychain_token_sources(
    service: str,
    accounts: list[str],
    json_keys: list[str],
    allow_generic: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    """Inspect Keychain token candidates without exposing secret values.

    :param service: Keychain service name.
    :param accounts: Candidate account identifiers.
    :param json_keys: Token-like JSON keys to look for.
    :param allow_generic: Whether to probe the service without an account.
    :returns: Per-keychain-check diagnostics and the winning source label.
    """

    diagnostics: list[dict[str, Any]] = []
    resolution: str | None = None

    def inspect(account: str | None) -> None:
        """Inspect one Keychain lookup target.

        :param account: Optional Keychain account identifier.
        :returns: Nothing.
        """

        nonlocal resolution
        secret = keychain_secret(service, account)
        shape = describe_secret_shape(secret, json_keys)
        diagnostics.append(
            {
                "account": account,
                **shape,
            }
        )
        if shape["hasToken"] and resolution is None:
            label = account or "generic"
            resolution = f"keychain:{service}:{label}"

    for account in accounts:
        inspect(account)
    if allow_generic:
        inspect(None)
    return diagnostics, resolution


def describe_secret_shape(secret: str | None, json_keys: list[str]) -> dict[str, Any]:
    """Classify a secret value without returning the secret itself.

    :param secret: Raw secret text.
    :param json_keys: Token-like JSON keys to look for.
    :returns: Shape metadata describing whether a token was found.
    """

    if not secret:
        return {"found": False, "format": None, "hasToken": False, "tokenKey": None}
    try:
        payload = json.loads(secret)
    except json.JSONDecodeError:
        return {"found": True, "format": "raw", "hasToken": True, "tokenKey": None}
    if not isinstance(payload, dict):
        return {"found": True, "format": "json", "hasToken": False, "tokenKey": None}
    token_key = first_token_key(payload, json_keys)
    return {
        "found": True,
        "format": "json",
        "hasToken": token_key is not None,
        "tokenKey": token_key,
    }


def first_token_key(payload: dict[str, Any], json_keys: list[str]) -> str | None:
    """Return the first present token-like key from a JSON payload.

    :param payload: Parsed JSON payload.
    :param json_keys: Candidate token keys.
    :returns: Matching key name, if any.
    """

    for json_key in json_keys:
        value = payload.get(json_key)
        if isinstance(value, str) and value:
            return json_key
        nested_value = nested_get(payload, json_key)
        if isinstance(nested_value, str) and nested_value:
            return json_key
    return None
