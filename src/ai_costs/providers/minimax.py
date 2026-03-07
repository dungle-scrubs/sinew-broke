"""MiniMax coding-plan quota adapter."""

from __future__ import annotations

from ai_costs.models import AccountSnapshot, WindowMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import nested_get, now_iso, percent_used, resolve_token, safe_float


class MiniMaxAdapter:
    """Fetch MiniMax plan remains using the token-based API path."""

    spec = AdapterSpec(provider="minimax", display_name="MiniMax")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        config = settings.minimax
        if not config.enabled:
            return AccountSnapshot(
                provider=self.spec.provider,
                account_id=config.account_id,
                display_name=self.spec.display_name,
                capabilities=["quota_only"],
                source_type="quota_only",
                status="disabled",
                notes=["provider disabled"],
            )

        token = resolve_token(
            config.token,
            ["MINIMAX_API_KEY"],
            [],
            [],
        )
        if not token:
            raise ProviderError("AIC001", "missing MiniMax API key")

        base_url = config.base_url or "https://api.minimax.io"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        with build_client() as client:
            response = client.get(
                f"{base_url}/v1/coding_plan/remains",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        total = safe_float(nested_get(payload, "total", "total_prompts", "limit"))
        remaining = safe_float(
            nested_get(payload, "remain", "remaining", "remaining_prompts")
        )
        used = (
            (total - remaining) if total is not None and remaining is not None else None
        )

        return AccountSnapshot(
            provider=self.spec.provider,
            account_id=config.account_id,
            display_name=self.spec.display_name,
            capabilities=["quota_only"],
            source_type="quota_only",
            status="ok",
            updated_at=now_iso(),
            windows=[
                WindowMetrics(
                    kind="plan",
                    used_percent=percent_used(used, total),
                    resets_at=nested_get(
                        payload, "reset_at", "resets_at", "expires_at"
                    ),
                )
            ],
            notes=[nested_get(payload, "plan_name", "plan") or "quota_only"],
            raw_payload=payload,
        )
