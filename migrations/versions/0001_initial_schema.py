"""initial schema: bars, orders, fills, positions, pnl_snapshots, signals, backtest_runs

Revision ID: 0001
Revises:
Create Date: 2026-04-20

Notes
-----
* The `bars` table is promoted to a TimescaleDB hypertable partitioned on `ts`.
  If the extension is unavailable (e.g. in a vanilla Postgres dev container),
  hypertable creation is skipped — the regular btree index on (symbol, ts)
  still keeps range scans cheap at V1 volumes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- bars ---
    op.create_table(
        "bars",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("ts", sa.Date(), primary_key=True),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.Numeric(24, 6), nullable=False),
        sa.Column("adjusted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("high >= low", name="bars_high_ge_low"),
        sa.CheckConstraint("open >= low AND open <= high", name="bars_open_in_range"),
        sa.CheckConstraint("close >= low AND close <= high", name="bars_close_in_range"),
    )
    op.create_index("ix_bars_symbol_ts", "bars", ["symbol", "ts"])

    # Try to promote to a TimescaleDB hypertable. Skip gracefully if unavailable.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                PERFORM create_hypertable('bars', 'ts', if_not_exists => TRUE,
                                          migrate_data => TRUE);
            END IF;
        END$$;
        """
    )

    # --- orders ---
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "client_order_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("limit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("time_in_force", sa.String(8), nullable=False, server_default="day"),
        sa.Column("status", sa.String(24), nullable=False, server_default="new"),
        sa.Column("strategy", sa.String(64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_strategy", "orders", ["strategy"])

    # --- fills ---
    op.create_table(
        "fills",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("broker_fill_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commission", sa.Numeric(18, 6), nullable=False, server_default="0"),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])
    op.create_index("ix_fills_symbol", "fills", ["symbol"])
    op.create_index("ix_fills_ts", "fills", ["ts"])

    # --- positions ---
    op.create_table(
        "positions",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("market_value", sa.Numeric(18, 6), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(18, 6), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    )

    # --- pnl_snapshots ---
    op.create_table(
        "pnl_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, unique=True),
        sa.Column("equity", sa.Numeric(18, 6), nullable=False),
        sa.Column("cash", sa.Numeric(18, 6), nullable=False),
        sa.Column("gross_exposure", sa.Numeric(18, 6), nullable=False),
        sa.Column("net_exposure", sa.Numeric(18, 6), nullable=False),
        sa.Column("daily_return", sa.Numeric(12, 8), nullable=True),
        sa.Column("drawdown", sa.Numeric(12, 8), nullable=True),
    )
    op.create_index("ix_pnl_snapshots_ts", "pnl_snapshots", ["ts"])

    # --- signals ---
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("target_weight", sa.Numeric(8, 6), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=True),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.UniqueConstraint("strategy", "symbol", "ts", name="uq_signals_strategy_symbol_ts"),
    )
    op.create_index("ix_signals_strategy", "signals", ["strategy"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])
    op.create_index("ix_signals_ts", "signals", ["ts"])

    # --- backtest_runs ---
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("sharpe", sa.Numeric(8, 4), nullable=True),
        sa.Column("cagr", sa.Numeric(8, 4), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_backtest_runs_strategy", "backtest_runs", ["strategy"])
    op.create_index("ix_backtest_runs_params_hash", "backtest_runs", ["params_hash"])


def downgrade() -> None:
    op.drop_table("backtest_runs")
    op.drop_table("signals")
    op.drop_table("pnl_snapshots")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("bars")
