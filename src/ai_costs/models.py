"""Domain models for normalized provider snapshots and ledger entries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

Capability = Literal["cost_usd", "credits", "subscription_window", "quota_only"]
SourceType = Literal[
    "authoritative_api",
    "oauth_usage",
    "derived_ledger",
    "imported_logs",
    "quota_only",
]
Status = Literal["ok", "stale", "error", "unconfigured", "incomplete", "disabled"]


class CostMetrics(BaseModel):
    """Normalized USD cost fields."""

    today_usd: float | None = None
    month_usd: float | None = None
    lifetime_usd: float | None = None


class CreditsMetrics(BaseModel):
    """Normalized credit fields expressed in USD-equivalent units."""

    used_usd: float | None = None
    total_usd: float | None = None
    balance_usd: float | None = None


class WindowMetrics(BaseModel):
    """A reset-based quota or subscription window."""

    kind: str
    used_percent: float | None = None
    resets_at: str | None = None


class AccountSnapshot(BaseModel):
    """Current normalized state for one provider account."""

    provider: str
    account_id: str = "default"
    display_name: str
    capabilities: list[Capability]
    source_type: SourceType
    status: Status
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    cost: CostMetrics | None = None
    credits: CreditsMetrics | None = None
    windows: list[WindowMetrics] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class UsageLedgerEntry(BaseModel):
    """A persisted usage or cost event."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    provider: str
    account_id: str = "default"
    model: str | None = None
    ts: int = Field(default_factory=lambda: int(utc_now().timestamp()))
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    source_type: SourceType
    raw_json: dict[str, Any] | None = None


class PopupRow(BaseModel):
    """One compact provider row inside a popup section."""

    label: str
    detail: str | None = None
    subtitle: str | None = None
    progress: float | None = None
    tone: str | None = None


class PopupColumn(BaseModel):
    """One popup card rendered by the Sinew plugin host."""

    title: str | None = None
    value: str | None = None
    meta: str | None = None
    body: str = ""
    rows: list[PopupRow] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    tone: str | None = None


class PluginPopup(BaseModel):
    """Popup body for Sinew plugin output."""

    type: str = "panel"
    title: str
    body: str = ""
    columns: list[PopupColumn] = Field(default_factory=list)
    height: int = 320
    width: int | None = None


class PluginOutput(BaseModel):
    """Sinew plugin response payload."""

    label: str
    icon: str = ""
    color: str | None = None
    popup: PluginPopup
    state: dict[str, Any]


def utc_now() -> datetime:
    """Return the current UTC datetime."""

    return datetime.now(UTC)
