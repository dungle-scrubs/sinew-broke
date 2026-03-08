from __future__ import annotations

from pathlib import Path

from ai_costs.models import (
    AccountSnapshot,
    CostMetrics,
    CreditsMetrics,
    UsageLedgerEntry,
    WindowMetrics,
)
from ai_costs.service import (
    build_label,
    build_popup_body,
    build_popup_columns,
    build_subscription_rows,
    category_label,
    collect_snapshots,
    detail_lines,
    detail_rows,
    expand_provider_configs,
    format_until,
    format_usd,
    popup_height,
    primary_metric,
    quota_warning_count,
)
from ai_costs.settings import PluginSettings, ProviderSettings
from ai_costs.storage import Storage


def test_build_label_uses_cost_rollups_when_cost_providers_exist(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path)
    snapshots = [
        AccountSnapshot(
            provider="openai_api",
            display_name="OpenAI API",
            capabilities=["cost_usd"],
            source_type="derived_ledger",
            status="ok",
            cost=CostMetrics(today_usd=1.0, month_usd=5.0, lifetime_usd=5.0),
        )
    ]
    storage.insert_ledger_entry(
        UsageLedgerEntry(
            provider="openai_api",
            model="gpt-5",
            cost_usd=1.0,
            source_type="derived_ledger",
        )
    )

    assert build_label(snapshots, storage) == "AI $1.00 · MTD $5.00"


def test_quota_warning_count_only_counts_high_usage_windows() -> None:
    snapshots = [
        AccountSnapshot(
            provider="glm",
            display_name="GLM",
            capabilities=["quota_only"],
            source_type="quota_only",
            status="ok",
            windows=[WindowMetrics(kind="quota", used_percent=81.0)],
        ),
        AccountSnapshot(
            provider="minimax",
            display_name="MiniMax",
            capabilities=["quota_only"],
            source_type="quota_only",
            status="ok",
            windows=[WindowMetrics(kind="quota", used_percent=50.0)],
        ),
    ]

    assert quota_warning_count(snapshots) == 1


def test_build_popup_body_keeps_provider_order_stable() -> None:
    snapshots = [
        AccountSnapshot(
            provider="claude_code",
            display_name="Claude Code",
            capabilities=["subscription_window"],
            source_type="oauth_usage",
            status="ok",
            windows=[WindowMetrics(kind="5h", used_percent=12.0)],
        ),
        AccountSnapshot(
            provider="openrouter",
            display_name="OpenRouter",
            capabilities=["credits", "cost_usd"],
            source_type="authoritative_api",
            status="ok",
            cost=CostMetrics(today_usd=0.5, month_usd=2.5, lifetime_usd=10.0),
        ),
    ]

    body = build_popup_body(snapshots)

    assert body.splitlines()[0] == "Claude Code"
    assert "status: ok" in body
    assert "OpenRouter" in body


def test_build_popup_body_handles_no_active_snapshots() -> None:
    assert (
        build_popup_body([])
        == "No providers enabled. Turn on one or more providers in config."
    )


def test_build_popup_columns_groups_snapshots_into_sections() -> None:
    snapshots = [
        AccountSnapshot(
            provider="claude_code",
            display_name="Claude Code",
            capabilities=["subscription_window"],
            source_type="oauth_usage",
            status="ok",
            windows=[WindowMetrics(kind="5h", used_percent=37.0)],
        ),
        AccountSnapshot(
            provider="anthropic_api",
            display_name="Anthropic API",
            capabilities=["cost_usd"],
            source_type="derived_ledger",
            status="incomplete",
            cost=CostMetrics(today_usd=0.0, month_usd=0.0, lifetime_usd=0.0),
        ),
        AccountSnapshot(
            provider="openrouter",
            display_name="OpenRouter",
            capabilities=["credits", "cost_usd"],
            source_type="authoritative_api",
            status="ok",
            credits=CreditsMetrics(used_usd=1.0, total_usd=2.0, balance_usd=1.0),
        ),
    ]

    columns = build_popup_columns(snapshots)

    assert [column.title for column in columns] == ["Subscriptions", "Spend", "Credits"]
    assert columns[0].rows[0].label == "Claude Code · 5h"
    assert columns[1].rows[0].label == "Anthropic API"
    assert columns[2].rows[0].label == "OpenRouter"


def test_format_usd_keeps_small_non_zero_values_visible() -> None:
    assert format_usd(0.0) == "$0.00"
    assert format_usd(0.0003) == "$0.0003"
    assert format_usd(1.25) == "$1.25"


def test_popup_height_tracks_tallest_column() -> None:
    snapshots = [
        AccountSnapshot(
            provider="anthropic_api",
            display_name="Anthropic API",
            capabilities=["cost_usd"],
            source_type="derived_ledger",
            status="ok",
            cost=CostMetrics(today_usd=0.0001, month_usd=0.0001, lifetime_usd=0.0001),
            notes=["one", "two"],
        ),
        AccountSnapshot(
            provider="openai_api",
            display_name="OpenAI API",
            capabilities=["cost_usd"],
            source_type="derived_ledger",
            status="ok",
            cost=CostMetrics(today_usd=0.0003, month_usd=0.0003, lifetime_usd=0.0003),
            notes=["one", "two"],
        ),
        AccountSnapshot(
            provider="openrouter",
            display_name="OpenRouter",
            capabilities=["credits", "cost_usd"],
            source_type="authoritative_api",
            status="ok",
            cost=CostMetrics(today_usd=0.0, month_usd=0.0, lifetime_usd=60.0),
            notes=["one", "two"],
        ),
    ]

    columns = build_popup_columns(snapshots)

    assert popup_height(snapshots, columns) >= 420


def test_primary_metric_summarizes_subscription_windows() -> None:
    claude = AccountSnapshot(
        provider="claude_code",
        display_name="Claude Code",
        capabilities=["subscription_window"],
        source_type="oauth_usage",
        status="ok",
        windows=[
            WindowMetrics(kind="5h", used_percent=37.0),
            WindowMetrics(kind="7d", used_percent=24.0),
        ],
    )
    gpt = AccountSnapshot(
        provider="gpt_subscription",
        display_name="GPT Subscription",
        capabilities=["subscription_window", "credits"],
        source_type="oauth_usage",
        status="ok",
        windows=[
            WindowMetrics(kind="primary window", used_percent=60.0),
            WindowMetrics(kind="secondary window", used_percent=68.0),
        ],
    )

    assert primary_metric(claude) == "5h 37% · 7d 24%"
    assert primary_metric(gpt) == "5h 60% · 7d 68%"


def test_detail_lines_include_readable_notes() -> None:
    snapshot = AccountSnapshot(
        provider="claude_code",
        display_name="Claude Code",
        capabilities=["subscription_window"],
        source_type="oauth_usage",
        status="ok",
        notes=["oauth_usage"],
    )

    lines = detail_lines(snapshot)

    assert lines == ["OAuth usage endpoint"]


def test_detail_rows_include_progress_and_reset_times() -> None:
    snapshot = AccountSnapshot(
        provider="claude_code",
        display_name="Claude Code",
        capabilities=["subscription_window"],
        source_type="oauth_usage",
        status="ok",
        windows=[
            WindowMetrics(
                kind="5h", used_percent=37.0, resets_at="2026-03-07T07:00:00+00:00"
            )
        ],
        notes=["oauth_usage"],
    )

    rows = detail_rows(snapshot)

    assert rows[0].label == "5h"
    assert rows[0].progress == 37.0
    assert rows[0].detail is not None


def test_category_label_distinguishes_subscription_cards() -> None:
    snapshot = AccountSnapshot(
        provider="claude_code",
        display_name="Claude Code",
        capabilities=["subscription_window"],
        source_type="oauth_usage",
        status="ok",
    )

    assert category_label(snapshot) == "subscription"


def test_build_subscription_rows_creates_one_row_per_window() -> None:
    snapshot = AccountSnapshot(
        provider="gpt_subscription",
        display_name="GPT Subscription",
        capabilities=["subscription_window", "credits"],
        source_type="oauth_usage",
        status="ok",
        windows=[
            WindowMetrics(
                kind="5h", used_percent=60.0, resets_at="2026-03-07T07:12:33+00:00"
            ),
            WindowMetrics(
                kind="7d", used_percent=68.0, resets_at="2026-03-12T02:37:32+00:00"
            ),
        ],
    )

    rows = build_subscription_rows([snapshot])

    assert len(rows) == 2
    assert rows[0].label == "GPT Subscription · 5h"
    assert "60%" in rows[0].subtitle
    assert "·" in rows[0].subtitle
    assert rows[1].label == "GPT Subscription · 7d"


def test_format_until_is_human_readable() -> None:
    assert format_until(None) == "reset unknown"


def test_expand_provider_configs_normalizes_single_to_list() -> None:
    from ai_costs.providers.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    settings = PluginSettings(
        claude_code=ProviderSettings(enabled=True, account_id="personal")
    )

    configs = expand_provider_configs(adapter, settings)

    assert len(configs) == 1
    assert configs[0].account_id == "personal"


def test_expand_provider_configs_passes_list_through() -> None:
    from ai_costs.providers.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    settings = PluginSettings(
        claude_code=[
            ProviderSettings(
                enabled=True,
                account_id="personal",
                auth_file="~/.claude/.credentials.json",
            ),
            ProviderSettings(
                enabled=True,
                account_id="fuse",
                auth_file="~/.claude-fuse/.credentials.json",
            ),
        ]
    )

    configs = expand_provider_configs(adapter, settings)

    assert len(configs) == 2
    assert configs[0].account_id == "personal"
    assert configs[1].account_id == "fuse"


def test_collect_snapshots_disambiguates_multi_account_display_names(
    tmp_path: Path,
) -> None:
    """Multi-account providers get account_id appended to display_name."""

    settings = PluginSettings(
        claude_code=[
            ProviderSettings(enabled=False, account_id="personal"),
            ProviderSettings(enabled=False, account_id="fuse"),
        ]
    )
    storage = Storage(tmp_path)

    snapshots = collect_snapshots(settings, storage)

    claude_snapshots = [s for s in snapshots if s.provider == "claude_code"]
    assert len(claude_snapshots) == 2
    assert claude_snapshots[0].display_name == "Claude Code (personal)"
    assert claude_snapshots[0].account_id == "personal"
    assert claude_snapshots[1].display_name == "Claude Code (fuse)"
    assert claude_snapshots[1].account_id == "fuse"
