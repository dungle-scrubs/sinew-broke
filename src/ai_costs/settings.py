"""Plugin settings loaded from `SINEW_PLUGIN_SETTINGS_JSON`."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

CLAUDE_WORK_DIRS_PATH = Path("~/.config/claude-work-dirs")
DEFAULT_CLAUDE_AUTH_FILE = "~/.claude/.credentials.json"


class ProviderSettings(BaseModel):
    """Configuration for a single provider adapter."""

    enabled: bool = False
    account_id: str = "default"
    token: str | None = None
    auth_file: str | None = None
    base_url: str | None = None


class PluginSettings(BaseModel):
    """Top-level plugin settings.

    Provider fields that accept ``list[ProviderSettings]`` allow tracking
    multiple accounts for the same provider (e.g. two Claude Code
    subscriptions with different auth files).
    """

    runtime_dir: str | None = None
    secret_env_file: str | None = None
    openrouter: ProviderSettings = Field(default_factory=ProviderSettings)
    claude_code: ProviderSettings | list[ProviderSettings] = Field(
        default_factory=ProviderSettings
    )
    anthropic_api: ProviderSettings = Field(default_factory=ProviderSettings)
    openai_api: ProviderSettings = Field(default_factory=ProviderSettings)
    gpt_subscription: ProviderSettings | list[ProviderSettings] = Field(
        default_factory=ProviderSettings
    )
    glm: ProviderSettings = Field(default_factory=ProviderSettings)
    minimax: ProviderSettings = Field(default_factory=ProviderSettings)


def single_provider_config(
    config: ProviderSettings | list[ProviderSettings],
) -> ProviderSettings:
    """Return one provider config for adapter fetch calls.

    Service-level fanout expands multi-account providers before calling an
    adapter, but direct adapter calls still see the union-typed settings field.
    This helper narrows that union to one concrete config for type checking.
    """

    if isinstance(config, list):
        return config[0] if config else ProviderSettings()
    return config


def claude_account_id(auth_file: str | None) -> str:
    """Derive a stable Claude account label from a credentials file path."""

    raw = auth_file or DEFAULT_CLAUDE_AUTH_FILE
    return Path(raw).expanduser().parent.name or "default"


def parse_claude_work_dirs(path: Path | None = None) -> list[str]:
    """Return discovered Claude config directories from claude-work-dirs."""

    resolved = (path or CLAUDE_WORK_DIRS_PATH).expanduser()
    if not resolved.exists():
        return []

    config_dirs: list[str] = []
    for line in resolved.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        _, separator, config_dir = stripped.partition(":")
        if not separator or not config_dir.strip():
            continue
        config_dirs.append(config_dir.strip())
    return config_dirs


def normalized_claude_auth_file(auth_file: str | None) -> str:
    """Normalize a Claude auth file path for deduplication."""

    return str(Path(auth_file or DEFAULT_CLAUDE_AUTH_FILE).expanduser())


def discover_claude_code_configs(config: ProviderSettings) -> list[ProviderSettings]:
    """Expand a Claude Code config with auto-discovered work-dir accounts."""

    if not config.enabled:
        return [config]
    if config.account_id != "default" or config.auth_file is not None or config.token:
        return [config]

    primary = config.model_copy()
    primary.auth_file = DEFAULT_CLAUDE_AUTH_FILE
    primary.account_id = claude_account_id(primary.auth_file)

    configs = [primary]
    for config_dir in parse_claude_work_dirs():
        auth_file = str(Path(config_dir).expanduser() / ".credentials.json")
        if not Path(auth_file).exists():
            continue
        configs.append(
            ProviderSettings(
                account_id=claude_account_id(auth_file),
                auth_file=auth_file,
                base_url=config.base_url,
                enabled=True,
            )
        )

    unique_configs: list[ProviderSettings] = []
    seen_auth_files: set[str] = set()
    for candidate in configs:
        auth_key = normalized_claude_auth_file(candidate.auth_file)
        if auth_key in seen_auth_files:
            continue
        seen_auth_files.add(auth_key)
        unique_configs.append(candidate)
    return unique_configs


def plugin_root() -> Path:
    """Return the root directory of the sinew-broke plugin project."""

    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def opchain_env(env_file: str) -> dict[str, str]:
    """Resolve secret env vars through opchain without printing their values."""

    command = [
        "opchain",
        "op",
        "run",
        "--env-file",
        env_file,
        "--",
        "python3",
        "-c",
        (
            "import json, os; "
            "keys=['OPENAI_API_KEY','OPENAI_ADMIN_KEY','ANTHROPIC_API_KEY','OPENROUTER_API_KEY','CLAUDE_CODE_OAUTH_TOKEN']; "
            "print(json.dumps({k: os.environ.get(k) for k in keys if os.environ.get(k)}))"
        ),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return {key: value for key, value in payload.items() if isinstance(value, str)}


def hydrate_secret_env(settings: PluginSettings) -> None:
    """Populate provider API keys from opchain when they are not already set."""

    env_file = settings.secret_env_file or str(plugin_root() / ".env.op.local")
    path = Path(env_file).expanduser()
    if not path.exists():
        return
    for key, value in opchain_env(str(path)).items():
        os.environ.setdefault(key, value)


def load_settings() -> PluginSettings:
    """Load plugin settings from the Sinew environment."""

    raw = os.environ.get("SINEW_PLUGIN_SETTINGS_JSON", "{}")
    payload = json.loads(raw or "{}")
    settings = PluginSettings.model_validate(payload)
    if not settings.runtime_dir:
        settings.runtime_dir = os.environ.get("SINEW_RUNTIME_DIR")
    hydrate_secret_env(settings)
    return settings


def runtime_dir(settings: PluginSettings) -> Path:
    """Resolve the runtime directory used for SQLite and caches."""

    raw = (
        settings.runtime_dir
        or os.environ.get("SINEW_RUNTIME_DIR")
        or "~/Library/Application Support/sinew/plugins/sinew-broke"
    )
    return Path(raw).expanduser().resolve()
