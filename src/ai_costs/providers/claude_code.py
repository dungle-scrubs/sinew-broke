"""Claude Code subscription adapter via Anthropic OAuth usage."""

from __future__ import annotations

from ai_costs.models import AccountSnapshot, WindowMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client, get_json
from ai_costs.settings import PluginSettings, single_provider_config
from ai_costs.storage import Storage
from ai_costs.utils import (
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


class ClaudeCodeAdapter:
    """Fetch Claude Code subscription usage from Anthropic OAuth."""

    spec = AdapterSpec(provider="claude_code", display_name="Claude Code")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
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

        token = resolve_token(
            config.token,
            ["ANTHROPIC_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"],
            [config.auth_file or "~/.claude/.credentials.json"],
            ["access_token", "accessToken", "token", "oauth_token"],
            keychain_services=["Claude Code-credentials"],
        )
        if not token:
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
        notes = ["oauth_usage"]
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
