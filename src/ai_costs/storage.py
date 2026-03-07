"""SQLite persistence for snapshots and derived cost ledgers."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_costs.models import AccountSnapshot, CostMetrics, SourceType, UsageLedgerEntry


class Storage:
    """SQLite-backed storage for snapshots and usage ledger entries."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runtime_dir / "usage.sqlite"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_snapshots (
                  provider TEXT NOT NULL,
                  account_id TEXT NOT NULL,
                  source_type TEXT NOT NULL,
                  capability_mask TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY (provider, account_id)
                );

                CREATE TABLE IF NOT EXISTS usage_ledger (
                  id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  account_id TEXT NOT NULL,
                  model TEXT,
                  ts INTEGER NOT NULL,
                  input_tokens INTEGER,
                  cache_read_tokens INTEGER,
                  cache_write_tokens INTEGER,
                  output_tokens INTEGER,
                  cost_usd REAL,
                  source_type TEXT NOT NULL,
                  raw_json TEXT
                );
                """
            )

    def get_snapshot(
        self, provider: str, account_id: str = "default"
    ) -> AccountSnapshot | None:
        """Load the most recent snapshot for a provider account."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM provider_snapshots
                WHERE provider = ? AND account_id = ?
                """,
                (provider, account_id),
            ).fetchone()
        if not row:
            return None
        return AccountSnapshot.model_validate_json(row["payload_json"])

    def upsert_snapshot(self, snapshot: AccountSnapshot) -> None:
        """Insert or replace a normalized provider snapshot."""

        updated_at = int(
            datetime.fromisoformat(snapshot.updated_at.replace("Z", "+00:00"))
            .astimezone(UTC)
            .timestamp()
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_snapshots (
                  provider,
                  account_id,
                  source_type,
                  capability_mask,
                  payload_json,
                  updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, account_id) DO UPDATE SET
                  source_type = excluded.source_type,
                  capability_mask = excluded.capability_mask,
                  payload_json = excluded.payload_json,
                  updated_at = excluded.updated_at
                """,
                (
                    snapshot.provider,
                    snapshot.account_id,
                    snapshot.source_type,
                    json.dumps(snapshot.capabilities),
                    snapshot.model_dump_json(),
                    updated_at,
                ),
            )

    def insert_ledger_entry(self, entry: UsageLedgerEntry) -> None:
        """Append a new usage ledger entry."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO usage_ledger (
                  id,
                  provider,
                  account_id,
                  model,
                  ts,
                  input_tokens,
                  cache_read_tokens,
                  cache_write_tokens,
                  output_tokens,
                  cost_usd,
                  source_type,
                  raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.provider,
                    entry.account_id,
                    entry.model,
                    entry.ts,
                    entry.input_tokens,
                    entry.cache_read_tokens,
                    entry.cache_write_tokens,
                    entry.output_tokens,
                    entry.cost_usd,
                    entry.source_type,
                    json.dumps(entry.raw_json) if entry.raw_json is not None else None,
                ),
            )

    def aggregate_cost(
        self,
        provider: str,
        account_id: str = "default",
    ) -> CostMetrics:
        """Return today/month/lifetime USD totals from the usage ledger."""

        now = datetime.now(UTC)
        start_of_day = int(
            now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        start_of_month = int(
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        )

        def sum_since(ts: int | None) -> float:
            query = """
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM usage_ledger
                WHERE provider = ? AND account_id = ?
            """
            params: list[Any] = [provider, account_id]
            if ts is not None:
                query += " AND ts >= ?"
                params.append(ts)
            with self._connect() as connection:
                row = connection.execute(query, params).fetchone()
            return float(row[0] or 0.0)

        return CostMetrics(
            today_usd=round(sum_since(start_of_day), 4),
            month_usd=round(sum_since(start_of_month), 4),
            lifetime_usd=round(sum_since(None), 4),
        )

    def aggregate_total_cost(self) -> CostMetrics:
        """Return aggregate totals across all providers."""

        now = datetime.now(UTC)
        start_of_day = int(
            now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        start_of_month = int(
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        )

        def sum_since(ts: int | None) -> float:
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger"
            params: list[Any] = []
            if ts is not None:
                query += " WHERE ts >= ?"
                params.append(ts)
            with self._connect() as connection:
                row = connection.execute(query, params).fetchone()
            return float(row[0] or 0.0)

        return CostMetrics(
            today_usd=round(sum_since(start_of_day), 4),
            month_usd=round(sum_since(start_of_month), 4),
            lifetime_usd=round(sum_since(None), 4),
        )

    def record_authoritative_delta(
        self,
        provider: str,
        account_id: str,
        new_total: float | None,
        source_type: SourceType,
        raw_json: dict[str, Any] | None = None,
    ) -> None:
        """Append a usage delta when an authoritative lifetime total increases."""

        if new_total is None:
            return
        previous = self.get_snapshot(provider, account_id)
        previous_total = (
            previous.cost.lifetime_usd if previous and previous.cost else None
        )
        if previous_total is None or new_total <= previous_total:
            return
        self.insert_ledger_entry(
            UsageLedgerEntry(
                provider=provider,
                account_id=account_id,
                cost_usd=round(new_total - previous_total, 4),
                source_type=source_type,
                raw_json=raw_json,
            )
        )
