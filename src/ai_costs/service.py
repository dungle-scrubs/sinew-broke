"""Fetch provider snapshots and build Sinew plugin output."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from ai_costs.models import (
    AccountSnapshot,
    Capability,
    PluginOutput,
    PluginPopup,
    PopupColumn,
    PopupRow,
    SourceType,
)
from ai_costs.providers import (
    AnthropicAPIAdapter,
    ClaudeCodeAdapter,
    GLMAdapter,
    GPTSubscriptionAdapter,
    MiniMaxAdapter,
    OpenAIAPIAdapter,
    OpenRouterAdapter,
)
from ai_costs.providers.base import ProviderAdapter, ProviderError
from ai_costs.settings import PluginSettings
from ai_costs.storage import Storage
from ai_costs.utils import age_minutes, now_iso, parse_timestamp

FIXED_ORDER: tuple[ProviderAdapter, ...] = (
    ClaudeCodeAdapter(),
    AnthropicAPIAdapter(),
    OpenAIAPIAdapter(),
    GPTSubscriptionAdapter(),
    GLMAdapter(),
    MiniMaxAdapter(),
    OpenRouterAdapter(),
)

DEFAULT_TYPES: dict[str, tuple[list[Capability], SourceType]] = {
    "claude_code": (["subscription_window"], "oauth_usage"),
    "anthropic_api": (["cost_usd"], "derived_ledger"),
    "openai_api": (["cost_usd"], "derived_ledger"),
    "gpt_subscription": (["subscription_window", "credits"], "oauth_usage"),
    "glm": (["quota_only"], "quota_only"),
    "minimax": (["quota_only"], "quota_only"),
    "openrouter": (["credits", "cost_usd"], "authoritative_api"),
}


def collect_snapshots(
    settings: PluginSettings, storage: Storage
) -> list[AccountSnapshot]:
    """Fetch all provider snapshots in RFC-defined order."""

    snapshots: list[AccountSnapshot] = []
    for adapter in FIXED_ORDER:
        previous = storage.get_snapshot(adapter.spec.provider)
        try:
            snapshot = adapter.fetch(settings, storage)
        except ProviderError as error:
            snapshot = stale_or_error(adapter, previous, error.code, str(error))
        except Exception as error:  # noqa: BLE001
            snapshot = stale_or_error(adapter, previous, "AIC003", str(error))
        storage.upsert_snapshot(snapshot)
        snapshots.append(snapshot)
    return snapshots


def stale_or_error(
    adapter: ProviderAdapter,
    previous: AccountSnapshot | None,
    code: str,
    message: str,
) -> AccountSnapshot:
    """Degrade to STALE when there is a last-known-good snapshot."""

    if previous is not None and previous.status in {"ok", "stale", "incomplete"}:
        previous.status = "stale"
        previous.notes = [*previous.notes, f"{code}: {message}"]
        previous.updated_at = now_iso()
        previous.error_code = code
        return previous

    capabilities, source_type = DEFAULT_TYPES.get(
        adapter.spec.provider,
        (["subscription_window"], "oauth_usage"),
    )
    status = "unconfigured" if code == "AIC001" else "error"
    return AccountSnapshot(
        provider=adapter.spec.provider,
        display_name=adapter.spec.display_name,
        capabilities=capabilities,
        source_type=source_type,
        status=status,
        updated_at=now_iso(),
        notes=[f"{code}: {message}"],
        error_code=code,
    )


def build_output(
    snapshots: Iterable[AccountSnapshot], storage: Storage
) -> PluginOutput:
    """Build the bar label, popup body, and state payload for Sinew."""

    ordered = list(snapshots)
    active = [snapshot for snapshot in ordered if snapshot.status != "disabled"]
    label = build_label(ordered, storage)
    color = pick_color(ordered)
    columns = build_popup_columns(active)
    height = popup_height(active, columns)
    return PluginOutput(
        label=label,
        color=color,
        popup=PluginPopup(
            title=f"AI Costs · {len(active)} active",
            body=build_popup_body(active),
            columns=columns,
            height=height,
        ),
        state={
            "provider_count": len(ordered),
            "active_count": len(active),
            "healthy_count": sum(snapshot.status == "ok" for snapshot in ordered),
            "warning_count": quota_warning_count(ordered),
        },
    )


def format_usd(value: float | None) -> str:
    """Format USD values without hiding tiny non-zero usage."""

    if value is None:
        return "—"
    if value == 0:
        return "$0.00"
    if abs(value) < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def cost_totals_from_snapshots(snapshots: list[AccountSnapshot]) -> tuple[float, float]:
    """Aggregate current today/month totals from provider snapshots."""

    today = 0.0
    month = 0.0
    for snapshot in snapshots:
        if "cost_usd" not in snapshot.capabilities:
            continue
        if snapshot.status in {"disabled", "unconfigured", "error"}:
            continue
        if snapshot.cost is None:
            continue
        today += snapshot.cost.today_usd or 0.0
        month += snapshot.cost.month_usd or 0.0
    return (round(today, 4), round(month, 4))


def build_label(snapshots: list[AccountSnapshot], storage: Storage) -> str:
    """Build the compact top-level bar summary."""

    today_total, month_total = cost_totals_from_snapshots(snapshots)
    has_cost_provider = any(
        "cost_usd" in snapshot.capabilities
        and snapshot.status not in {"disabled", "unconfigured", "error"}
        for snapshot in snapshots
    )
    if has_cost_provider:
        return f"AI {format_usd(today_total)} · MTD {format_usd(month_total)}"

    healthy = sum(snapshot.status == "ok" for snapshot in snapshots)
    warnings = quota_warning_count(snapshots)
    return f"AI {healthy} ok · {warnings} quota low"


def pick_color(snapshots: list[AccountSnapshot]) -> str:
    """Pick a bar color based on health severity."""

    if any(snapshot.status == "error" for snapshot in snapshots):
        return "#f38ba8"
    if any(snapshot.status == "stale" for snapshot in snapshots):
        return "#f9e2af"
    if quota_warning_count(snapshots) > 0:
        return "#f9e2af"
    return "#a6e3a1"


def quota_warning_count(snapshots: list[AccountSnapshot]) -> int:
    """Count providers with high usage windows."""

    return sum(
        any((window.used_percent or 0.0) >= 80.0 for window in snapshot.windows)
        for snapshot in snapshots
    )


def build_popup_body(snapshots: list[AccountSnapshot]) -> str:
    """Render a deterministic plain-text popup body fallback."""

    if not snapshots:
        return "No providers enabled. Turn on one or more providers in config."

    return "\n\n".join(build_snapshot_block(snapshot) for snapshot in snapshots)


def build_popup_columns(snapshots: list[AccountSnapshot]) -> list[PopupColumn]:
    """Render active providers as grouped section columns."""

    if not snapshots:
        return []

    groups = [
        (
            "Subscriptions",
            [s for s in snapshots if category_label(s) == "subscription"],
        ),
        ("Spend", [s for s in snapshots if category_label(s) == "spend"]),
        ("Credits", [s for s in snapshots if category_label(s) == "credits"]),
        ("Quota", [s for s in snapshots if category_label(s) == "quota"]),
    ]

    columns = [build_section_column(title, group) for title, group in groups if group]
    return columns[:4]


def format_age(iso_timestamp: str) -> str:
    """Render a short human-readable age string."""

    minutes = age_minutes(iso_timestamp)
    if minutes <= 0:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours, rem = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {rem}m ago" if rem else f"{hours}h ago"
    days, rem_hours = divmod(hours, 24)
    return f"{days}d {rem_hours}h ago" if rem_hours else f"{days}d ago"


def format_until(value: str | None) -> str:
    """Render time remaining until a reset timestamp."""

    if not value:
        return "reset unknown"
    now = datetime.now(UTC)
    target = parse_timestamp(value)
    delta = target - now
    if delta.total_seconds() <= 0:
        return "resetting now"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"resets in {minutes}m"
    hours, rem = divmod(minutes, 60)
    if hours < 24:
        return f"resets in {hours}h {rem}m" if rem else f"resets in {hours}h"
    days, rem_hours = divmod(hours, 24)
    return f"resets in {days}d {rem_hours}h" if rem_hours else f"resets in {days}d"


def format_reset_time(value: str | None) -> str:
    """Render reset times in a readable local-ish format."""

    if not value:
        return "unknown"
    dt = parse_timestamp(value).astimezone()
    now = datetime.now(UTC).astimezone()
    day_label = dt.strftime("%a")
    if dt.date() == now.date():
        day_label = "today"
    elif dt.date() == now.date() + timedelta(days=1):
        day_label = "tomorrow"
    return f"{day_label} {dt.strftime('%-I:%M %p')}"


def format_note(note: str) -> str:
    """Shorten raw provider notes into readable support text."""

    if note == "oauth_usage":
        return "OAuth usage endpoint"
    if note == "derived_ledger":
        return "Derived from local request ledger"
    if note == "authoritative admin costs API":
        return "OpenAI organization costs API"
    if note == "authoritative credits endpoint":
        return "OpenRouter credits endpoint"
    if "429 Too Many Requests" in note:
        return "Rate limited — retry later"
    if "401" in note or "unauthorized" in note.lower():
        return "Authentication expired — re-auth may be required"
    if note.startswith("AIC001:"):
        return note.removeprefix("AIC001: ").strip()
    if note.startswith("AIC002:"):
        return note.removeprefix("AIC002: ").strip()
    if note.startswith("AIC003:"):
        return note.removeprefix("AIC003: ").split("\n", 1)[0].strip()
    return note


def build_section_column(title: str, snapshots: list[AccountSnapshot]) -> PopupColumn:
    """Convert one category into a single section column."""

    rows = (
        build_subscription_rows(snapshots)
        if title == "Subscriptions"
        else [build_snapshot_row(snapshot) for snapshot in snapshots]
    )
    return PopupColumn(
        title=title,
        value=section_value(title, snapshots),
        meta=section_meta(title, snapshots),
        rows=rows,
        lines=section_notes(snapshots),
        tone=section_tone(snapshots),
    )


def build_snapshot_row(snapshot: AccountSnapshot) -> PopupRow:
    """Convert one provider snapshot into a structured row."""

    return PopupRow(
        label=snapshot.display_name,
        detail=primary_metric(snapshot),
        subtitle=row_subtitle(snapshot),
        progress=row_progress(snapshot),
        tone=snapshot_tone(snapshot),
    )


def build_subscription_rows(snapshots: list[AccountSnapshot]) -> list[PopupRow]:
    """Expand subscription providers into one row per reset window."""

    rows: list[PopupRow] = []
    for snapshot in snapshots:
        for window in snapshot.windows[:3]:
            percent = window.used_percent or 0.0
            rows.append(
                PopupRow(
                    label=f"{snapshot.display_name} · {subscription_window_label(window.kind)}",
                    detail=f"{percent:.0f}%",
                    subtitle=format_until(window.resets_at),
                    progress=window.used_percent,
                    tone=snapshot_tone(snapshot),
                )
            )
    return rows


def build_snapshot_block(snapshot: AccountSnapshot) -> str:
    """Render one provider as a plain-text fallback block."""

    metric = primary_metric(snapshot)
    age = age_minutes(snapshot.updated_at)
    lines = [
        snapshot.display_name,
        f"status: {snapshot.status}",
        f"source: {snapshot.source_type}",
        f"metric: {metric}",
        f"updated: {age}m ago",
    ]
    lines.extend(f"note: {note}" for note in snapshot.notes[:2])
    return "\n".join(lines)


def section_value(title: str, snapshots: list[AccountSnapshot]) -> str:
    """Render the headline value for a section column."""

    if title == "Spend":
        today = sum(
            snapshot.cost.today_usd or 0.0 for snapshot in snapshots if snapshot.cost
        )
        month = sum(
            snapshot.cost.month_usd or 0.0 for snapshot in snapshots if snapshot.cost
        )
        return f"{format_usd(month)} month · {format_usd(today)} today"
    if title == "Credits":
        snapshot = snapshots[0]
        if snapshot.credits and snapshot.credits.balance_usd is not None:
            return f"{format_usd(snapshot.credits.balance_usd)} balance"
    if title == "Subscriptions":
        next_reset = min(
            (
                window.resets_at
                for snapshot in snapshots
                for window in snapshot.windows
                if window.resets_at
            ),
            default=None,
        )
        return format_until(next_reset) if next_reset else "limits and resets"
    return f"{len(snapshots)} sources"


def section_meta(title: str, snapshots: list[AccountSnapshot]) -> str:
    """Render compact metadata for a section column."""

    freshest = max(snapshots, key=lambda snapshot: parse_timestamp(snapshot.updated_at))
    parts = [f"updated {format_age(freshest.updated_at)}"]
    cached = sum(snapshot.status == "stale" for snapshot in snapshots)
    errors = sum(snapshot.status == "error" for snapshot in snapshots)
    if cached:
        parts.append(f"{cached} cached")
    if errors:
        parts.append(f"{errors} error")
    return " · ".join(parts)


def section_notes(snapshots: list[AccountSnapshot]) -> list[str]:
    """Collect short section-level notes for warnings and errors."""

    notes: list[str] = []
    for snapshot in snapshots:
        if snapshot.status == "ok":
            continue
        for note in snapshot.notes[:1]:
            pretty = format_note(note)
            entry = f"{snapshot.display_name}: {pretty}"
            if entry not in notes:
                notes.append(entry)
    return notes[:1]


def section_tone(snapshots: list[AccountSnapshot]) -> str:
    """Derive a section accent tone from contained snapshots."""

    if any(snapshot.status == "error" for snapshot in snapshots):
        return "error"
    if any(snapshot.status in {"stale", "incomplete"} for snapshot in snapshots):
        return "warning"
    if any(
        snapshot.provider == "openrouter" and snapshot_tone(snapshot) == "warning"
        for snapshot in snapshots
    ):
        return "warning"
    return "info"


def category_label(snapshot: AccountSnapshot) -> str:
    """Return a high-level category label for the popup card."""

    if "subscription_window" in snapshot.capabilities:
        return "subscription"
    if (
        "credits" in snapshot.capabilities
        and snapshot.source_type == "authoritative_api"
    ):
        return "credits"
    if "quota_only" in snapshot.capabilities:
        return "quota"
    return "spend"


def card_meta(snapshot: AccountSnapshot) -> str:
    """Render a compact metadata line for one card."""

    age = format_age(snapshot.updated_at)
    category = category_label(snapshot)
    if snapshot.status == "ok":
        return f"{category} · {age}"
    return f"{category} · {snapshot.status} · {age}"


def row_progress(snapshot: AccountSnapshot) -> float | None:
    """Pick the most useful progress percentage for one provider row."""

    if snapshot.windows:
        return snapshot.windows[0].used_percent
    if (
        snapshot.provider == "openrouter"
        and snapshot.credits
        and snapshot.credits.used_usd is not None
        and snapshot.credits.total_usd not in (None, 0)
    ):
        return (snapshot.credits.used_usd / snapshot.credits.total_usd) * 100.0
    return None


def row_subtitle(snapshot: AccountSnapshot) -> str | None:
    """Render the most important supporting detail for a provider row."""

    if snapshot.provider == "claude_code" and snapshot.windows:
        return format_reset_time(snapshot.windows[0].resets_at)
    if snapshot.provider == "gpt_subscription" and snapshot.windows:
        return format_reset_time(snapshot.windows[0].resets_at)
    if snapshot.provider == "openrouter":
        return (
            "overdraw allowed"
            if snapshot.credits and (snapshot.credits.balance_usd or 0.0) < 0
            else None
        )
    return None


def detail_rows(snapshot: AccountSnapshot) -> list[PopupRow]:
    """Build structured metric rows for one popup card."""

    rows: list[PopupRow] = []
    if snapshot.windows:
        for window in snapshot.windows[:3]:
            rows.append(
                PopupRow(
                    label=window.kind,
                    detail=format_reset_time(window.resets_at),
                    progress=window.used_percent,
                    tone=snapshot_tone(snapshot),
                )
            )
    if (
        snapshot.credits
        and snapshot.source_type == "oauth_usage"
        and snapshot.credits.balance_usd is not None
    ):
        rows.append(
            PopupRow(
                label="credits",
                detail=format_usd(snapshot.credits.balance_usd),
                progress=None,
                tone=snapshot_tone(snapshot),
            )
        )
    if (
        snapshot.provider == "openrouter"
        and snapshot.credits
        and snapshot.credits.used_usd is not None
        and snapshot.credits.total_usd not in (None, 0)
    ):
        percent = (snapshot.credits.used_usd / snapshot.credits.total_usd) * 100.0
        rows.append(
            PopupRow(
                label="usage",
                detail=f"{percent:.0f}%",
                progress=percent,
                tone=snapshot_tone(snapshot),
            )
        )
    return rows


def detail_lines(snapshot: AccountSnapshot) -> list[str]:
    """Build short readable note lines for one popup card."""

    lines: list[str] = []
    for note in snapshot.notes[:2]:
        pretty = format_note(note)
        if pretty not in lines:
            lines.append(pretty)
    return lines


def snapshot_tone(snapshot: AccountSnapshot) -> str:
    """Map provider status to a popup card accent tone."""

    if (
        snapshot.provider == "openrouter"
        and snapshot.credits
        and (snapshot.credits.balance_usd or 0.0) < 0
    ):
        return "warning"
    if snapshot.status == "ok":
        return "success"
    if snapshot.status in {"stale", "incomplete"}:
        return "warning"
    if snapshot.status in {"error", "unconfigured"}:
        return "error"
    return "info"


def popup_height(snapshots: list[AccountSnapshot], columns: list[PopupColumn]) -> int:
    """Choose a popup height large enough for a single-row card layout."""

    if not snapshots:
        return 260

    def card_line_count(column: PopupColumn) -> int:
        return 1 + int(bool(column.value)) + int(bool(column.meta)) + len(column.lines)

    max_lines = max((card_line_count(column) for column in columns), default=8)
    return max(420, 140 + (max_lines * 26))


def subscription_window_label(kind: str) -> str:
    """Normalize subscription window labels for UI display."""

    if kind == "primary window":
        return "5h"
    if kind == "secondary window":
        return "7d"
    return kind


def primary_metric(snapshot: AccountSnapshot) -> str:
    """Return the provider-specific headline metric."""

    if (
        snapshot.credits
        and snapshot.credits.balance_usd is not None
        and snapshot.source_type == "authoritative_api"
    ):
        balance = format_usd(snapshot.credits.balance_usd)
        used = format_usd(snapshot.credits.used_usd)
        total = format_usd(snapshot.credits.total_usd)
        return f"balance {balance} · used {used} / {total}"
    if snapshot.provider == "claude_code" and len(snapshot.windows) >= 2:
        primary, secondary = snapshot.windows[:2]
        return (
            f"{subscription_window_label(primary.kind)} {primary.used_percent or 0:.0f}% · "
            f"{subscription_window_label(secondary.kind)} {secondary.used_percent or 0:.0f}%"
        )
    if snapshot.provider == "gpt_subscription" and len(snapshot.windows) >= 2:
        primary, secondary = snapshot.windows[:2]
        return (
            f"{subscription_window_label(primary.kind)} {primary.used_percent or 0:.0f}% · "
            f"{subscription_window_label(secondary.kind)} {secondary.used_percent or 0:.0f}%"
        )
    if snapshot.cost and snapshot.cost.month_usd is not None:
        return (
            f"today {format_usd(snapshot.cost.today_usd)} · "
            f"month {format_usd(snapshot.cost.month_usd)}"
        )
    if snapshot.credits and snapshot.credits.balance_usd is not None:
        balance = format_usd(snapshot.credits.balance_usd)
        used = format_usd(snapshot.credits.used_usd)
        total = format_usd(snapshot.credits.total_usd)
        return f"balance {balance} · used {used} / {total}"
    if snapshot.windows:
        window = snapshot.windows[0]
        if window.used_percent is None:
            return window.kind
        return f"{window.kind} {window.used_percent:.0f}%"
    return "no data"
