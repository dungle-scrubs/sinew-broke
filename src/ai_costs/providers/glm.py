"""GLM quota adapter via z.ai / BigModel endpoints."""

from __future__ import annotations

from ai_costs.models import AccountSnapshot, WindowMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import nested_get, now_iso, percent_used, resolve_token, safe_float


class GLMAdapter:
    """Fetch GLM quota visibility without fabricating spend."""

    spec = AdapterSpec(provider="glm", display_name="GLM")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        config = settings.glm
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
            ["Z_AI_API_KEY", "BIGMODEL_API_KEY"],
            [],
            [],
        )
        if not token:
            raise ProviderError("AIC001", "missing GLM quota token")

        base_url = config.base_url or "https://api.z.ai"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        with build_client() as client:
            response = client.get(
                f"{base_url}/api/monitor/usage/quota/limit",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        total = safe_float(nested_get(payload, "total_quota", "quota_limit", "limit"))
        used = safe_float(nested_get(payload, "used_quota", "quota_used", "used"))
        details = nested_get(payload, "usageDetails", "usage_details")
        notes = ["quota_only"]
        if isinstance(details, list):
            notes.append(f"{len(details)} usage detail rows")

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
                    kind="quota",
                    used_percent=percent_used(used, total),
                    resets_at=nested_get(payload, "reset_at", "resets_at", "end_at"),
                )
            ],
            notes=notes,
            raw_payload=payload,
        )
