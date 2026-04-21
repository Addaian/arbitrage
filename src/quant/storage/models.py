"""SQLAlchemy ORM models — the persisted state of the system.

Tables mirror the domain types in `quant.types` but with integer PKs for
efficient indexing and explicit foreign keys where useful.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BarORM(Base):
    """Daily OHLCV bars. Indexed on (symbol, ts) for fast range scans.

    Will be promoted to a TimescaleDB hypertable by the first migration so
    range queries over multi-year windows stay cheap.
    """

    __tablename__ = "bars"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("high >= low", name="bars_high_ge_low"),
        CheckConstraint("open >= low AND open <= high", name="bars_open_in_range"),
        CheckConstraint("close >= low AND close <= high", name="bars_close_in_range"),
        Index("ix_bars_symbol_ts", "symbol", "ts"),
    )


class OrderORM(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True, default=uuid4
    )
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="day")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new", index=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class FillORM(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_fill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=0)

    __table_args__ = (UniqueConstraint("broker_fill_id", name="uq_fills_broker_fill_id"),)


class PositionORM(Base):
    """Latest known position per symbol. Upserted each reconciliation."""

    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PnlSnapshotORM(Base):
    __tablename__ = "pnl_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, unique=True, index=True
    )
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    gross_exposure: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    net_exposure: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    daily_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    drawdown: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)


class SignalORM(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    ts: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    target_weight: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    meta: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("strategy", "symbol", "ts", name="uq_signals_strategy_symbol_ts"),
    )


class BacktestRunORM(Base):
    """One row per backtest execution — powers the DSR trial count (PRD §5.6)."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    sharpe: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    cagr: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
