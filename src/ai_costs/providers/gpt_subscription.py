"""GPT subscription adapter via ChatGPT/Codex OAuth usage."""

from __future__ import annotations

from ai_costs.models import AccountSnapshot, CreditsMetrics, WindowMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client, get_json
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import (
    nested_get,
    normalize_timestamp,
    now_iso,
    resolve_token,
    safe_float,
    safe_int,
)


def normalized_window_kind(default_label: str, raw_window: dict[str, object]) -> str:
    """Map generic API window names onto readable time windows when possible."""

    seconds = safe_int(raw_window.get("limit_window_seconds"))
    if seconds == 18_000:
        return "5h"
    if seconds == 604_800:
        return "7d"
    return default_label


class GPTSubscriptionAdapter:
    """Fetch ChatGPT/Codex subscription usage windows."""

    spec = AdapterSpec(provider="gpt_subscription", display_name="GPT Subscription")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        config = settings.gpt_subscription
        if not config.enabled:
            return AccountSnapshot(
                provider=self.spec.provider,
                account_id=config.account_id,
                display_name=self.spec.display_name,
                capabilities=["subscription_window", "credits"],
                source_type="oauth_usage",
                status="disabled",
                notes=["provider disabled"],
            )

        token = resolve_token(
            config.token,
            ["OPENAI_OAUTH_TOKEN", "CODEX_OAUTH_TOKEN"],
            [config.auth_file or "~/.codex/auth.json"],
            ["access_token", "token", "id_token"],
        )
        if not token:
            raise ProviderError(
                "AIC001",
                "missing GPT subscription OAuth token; run `codex login` or configure gpt_subscription.auth_file",
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        with build_client() as client:
            payload = get_json(
                client,
                "https://chatgpt.com/backend-api/wham/usage",
                headers,
                "GPT subscription auth expired; run `codex login` again",
            )

        rate_limit = payload.get("rate_limit") or nested_get(
            payload, "rate_limit"
        )
        if not isinstance(rate_limit, dict):
            raise ProviderError(
                "AIC003", "unsupported GPT subscription payload: missing rate_limit"
            )

        raw_windows: list[tuple[str, dict[str, object]]] = []
        for key in ["primary_window", "secondary_window"]:
            raw_window = rate_limit.get(key)
            if isinstance(raw_window, dict):
                raw_windows.append((key.replace("_", " "), raw_window))

        windows = [
            WindowMetrics(
                kind=normalized_window_kind(label, raw_window),
                used_percent=safe_float(
                    raw_window.get("used_percent")
                    or raw_window.get("percent_used")
                ),
                resets_at=normalize_timestamp(
                    raw_window.get("resets_at")
                    or raw_window.get("reset_at")
                    or raw_window.get("end_at")
                ),
            )
            for label, raw_window in raw_windows
        ]
        if not windows:
            raise ProviderError("AIC003", "unsupported GPT subscription payload")

        credits_total = safe_float(
            nested_get(payload, "total_credits", "credits_total")
        )
        credits_balance = safe_float(
            nested_get(payload, "remaining_credits", "credits_remaining")
        )
        credits_used = (
            (credits_total - credits_balance)
            if credits_total is not None and credits_balance is not None
            else None
        )

        return AccountSnapshot(
            provider=self.spec.provider,
            account_id=config.account_id,
            display_name=self.spec.display_name,
            capabilities=["subscription_window", "credits"],
            source_type="oauth_usage",
            status="ok",
            updated_at=now_iso(),
            credits=CreditsMetrics(
                used_usd=credits_used,
                total_usd=credits_total,
                balance_usd=credits_balance,
            )
            if any(value is not None for value in [credits_total, credits_balance])
            else None,
            windows=windows,
            notes=[
                nested_get(payload, "plan_type", "plan", "planType") or "oauth_usage"
            ],
            raw_payload=payload,
        )
