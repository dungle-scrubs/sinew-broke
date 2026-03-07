"""Plugin settings loaded from `SINEW_PLUGIN_SETTINGS_JSON`."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class ProviderSettings(BaseModel):
    """Configuration for a single provider adapter."""

    enabled: bool = False
    account_id: str = "default"
    token: str | None = None
    auth_file: str | None = None
    base_url: str | None = None


class PluginSettings(BaseModel):
    """Top-level plugin settings."""

    runtime_dir: str | None = None
    secret_env_file: str | None = None
    openrouter: ProviderSettings = Field(default_factory=ProviderSettings)
    claude_code: ProviderSettings = Field(default_factory=ProviderSettings)
    anthropic_api: ProviderSettings = Field(default_factory=ProviderSettings)
    openai_api: ProviderSettings = Field(default_factory=ProviderSettings)
    gpt_subscription: ProviderSettings = Field(default_factory=ProviderSettings)
    glm: ProviderSettings = Field(default_factory=ProviderSettings)
    minimax: ProviderSettings = Field(default_factory=ProviderSettings)


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
