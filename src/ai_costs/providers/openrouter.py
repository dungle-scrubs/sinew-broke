"""OpenRouter credits and cost adapter."""

from __future__ import annotations

from ai_costs.models import AccountSnapshot, CostMetrics, CreditsMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import nested_get, percent_used, resolve_token, safe_float


class OpenRouterAdapter:
    """Fetch OpenRouter credits and enrich with key metadata when available."""

    spec = AdapterSpec(provider="openrouter", display_name="OpenRouter")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        config = settings.openrouter
        if not config.enabled:
            return AccountSnapshot(
                provider=self.spec.provider,
                account_id=config.account_id,
                display_name=self.spec.display_name,
                capabilities=["credits", "cost_usd"],
                source_type="authoritative_api",
                status="disabled",
                notes=["provider disabled"],
            )

        token = resolve_token(
            config.token,
            ["OPENROUTER_API_KEY"],
            [],
            [],
        )
        if not token:
            raise ProviderError("AIC001", "missing OpenRouter API key")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        with build_client() as client:
            credits_response = client.get(
                "https://openrouter.ai/api/v1/credits",
                headers=headers,
            )
            credits_response.raise_for_status()
            credits_payload = credits_response.json()

            key_payload: dict[str, object] = {}
            try:
                key_response = client.get(
                    "https://openrouter.ai/api/v1/key",
                    headers=headers,
                )
                key_response.raise_for_status()
                key_payload = key_response.json()
            except Exception:
                key_payload = {}

        total = safe_float(nested_get(credits_payload, "total_credits", "credit_limit"))
        used = safe_float(nested_get(credits_payload, "total_usage", "usage"))
        balance = (total - used) if total is not None and used is not None else None
        notes = ["authoritative credits endpoint"]
        key_limit = safe_float(nested_get(key_payload, "limit", "credit_limit"))
        if key_limit is not None:
            notes.append(f"key limit ${key_limit:.2f}")

        snapshot = AccountSnapshot(
            provider=self.spec.provider,
            account_id=config.account_id,
            display_name=self.spec.display_name,
            capabilities=["credits", "cost_usd"],
            source_type="authoritative_api",
            status="ok",
            cost=CostMetrics(lifetime_usd=used),
            credits=CreditsMetrics(
                used_usd=used,
                total_usd=total,
                balance_usd=balance,
            ),
            notes=notes,
            raw_payload={
                "credits": credits_payload,
                "key": key_payload,
                "used_percent": percent_used(used, total),
            },
        )
        storage.record_authoritative_delta(
            provider=snapshot.provider,
            account_id=snapshot.account_id,
            new_total=used,
            source_type=snapshot.source_type,
            raw_json=snapshot.raw_payload,
        )
        rollup = storage.aggregate_cost(snapshot.provider, snapshot.account_id)
        snapshot.cost = CostMetrics(
            today_usd=rollup.today_usd,
            month_usd=rollup.month_usd,
            lifetime_usd=used,
        )
        return snapshot
