"""OpenAI API cost adapter using admin billing APIs when available."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from ai_costs.models import AccountSnapshot, CostMetrics
from ai_costs.providers.base import AdapterSpec, ProviderError, build_client, get_json
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import now_iso, safe_float


class OpenAIAPIAdapter:
    """Report OpenAI API spend from admin billing APIs or the local ledger."""

    spec = AdapterSpec(provider="openai_api", display_name="OpenAI API")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        config = settings.openai_api
        if not config.enabled:
            return AccountSnapshot(
                provider=self.spec.provider,
                account_id=config.account_id,
                display_name=self.spec.display_name,
                capabilities=["cost_usd"],
                source_type="derived_ledger",
                status="disabled",
                notes=["provider disabled"],
            )

        admin_key = os.environ.get("OPENAI_ADMIN_KEY")
        if admin_key:
            return self.fetch_authoritative(config.account_id)

        rollup = storage.aggregate_cost(self.spec.provider, config.account_id)
        has_data = bool(rollup.lifetime_usd)
        notes = ["derived_ledger"]
        if not has_data:
            notes.append("route OpenAI requests through ai-costs-openai")

        return AccountSnapshot(
            provider=self.spec.provider,
            account_id=config.account_id,
            display_name=self.spec.display_name,
            capabilities=["cost_usd"],
            source_type="derived_ledger",
            status="ok" if has_data else "incomplete",
            updated_at=now_iso(),
            cost=rollup,
            notes=notes,
        )

    def fetch_authoritative(self, account_id: str) -> AccountSnapshot:
        """Fetch current OpenAI organization costs with the admin key."""

        admin_key = os.environ.get("OPENAI_ADMIN_KEY")
        if not admin_key:
            raise ProviderError("AIC001", "missing OpenAI admin key")

        now = datetime.now(UTC)
        start_of_month = int(
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        day_count = max(now.day, 1)
        headers = {
            "Authorization": f"Bearer {admin_key}",
            "Accept": "application/json",
        }
        with build_client() as client:
            payload = get_json(
                client,
                (
                    "https://api.openai.com/v1/organization/costs"
                    f"?start_time={start_of_month}&bucket_width=1d&limit={day_count}"
                ),
                headers,
                "OpenAI admin key unauthorized for organization costs",
            )

        month_total = 0.0
        today_total = 0.0
        today_day = now.date().isoformat()
        for bucket in payload.get("data", []):
            if not isinstance(bucket, dict):
                continue
            bucket_total = 0.0
            for result in bucket.get("results", []):
                if not isinstance(result, dict):
                    continue
                amount = result.get("amount", {})
                if isinstance(amount, dict):
                    bucket_total += safe_float(amount.get("value")) or 0.0
            month_total += bucket_total
            if str(bucket.get("start_time_iso", "")).startswith(today_day):
                today_total += bucket_total

        return AccountSnapshot(
            provider=self.spec.provider,
            account_id=account_id,
            display_name=self.spec.display_name,
            capabilities=["cost_usd"],
            source_type="authoritative_api",
            status="ok",
            updated_at=now_iso(),
            cost=CostMetrics(
                today_usd=round(today_total, 4),
                month_usd=round(month_total, 4),
                lifetime_usd=None,
            ),
            notes=["authoritative admin costs API"],
            raw_payload=payload,
        )
