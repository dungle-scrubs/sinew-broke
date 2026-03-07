from __future__ import annotations

from pathlib import Path

from ai_costs.models import UsageLedgerEntry
from ai_costs.storage import Storage


def test_aggregate_cost_rolls_up_today_month_lifetime(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    storage.insert_ledger_entry(
        UsageLedgerEntry(
            provider="openai_api",
            model="gpt-5",
            cost_usd=1.25,
            source_type="derived_ledger",
        )
    )
    storage.insert_ledger_entry(
        UsageLedgerEntry(
            provider="openai_api",
            model="gpt-5-mini",
            cost_usd=0.75,
            source_type="derived_ledger",
        )
    )

    rollup = storage.aggregate_cost("openai_api")

    assert rollup.today_usd == 2.0
    assert rollup.month_usd == 2.0
    assert rollup.lifetime_usd == 2.0
