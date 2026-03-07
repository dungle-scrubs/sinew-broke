"""Derived Anthropic API cost adapter backed by the local ledger."""

from __future__ import annotations

from ai_costs.models import AccountSnapshot
from ai_costs.providers.base import AdapterSpec
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import now_iso


class AnthropicAPIAdapter:
    """Report Anthropic API spend from the local usage ledger."""

    spec = AdapterSpec(provider="anthropic_api", display_name="Anthropic API")

    def fetch(self, settings: PluginSettings, storage: Storage) -> AccountSnapshot:
        config = settings.anthropic_api
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

        rollup = storage.aggregate_cost(self.spec.provider, config.account_id)
        has_data = bool(rollup.lifetime_usd)
        notes = ["derived_ledger"]
        if not has_data:
            notes.append("route Anthropic requests through ai-costs-anthropic")

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
