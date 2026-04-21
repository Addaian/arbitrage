"""Minimal CRUD repositories. One class per table.

Repos are the only layer that talks to SQLAlchemy — everything else passes
domain types (`quant.types`). Callers supply a session so transactions can
compose across multiple repos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant.storage.models import (
    BacktestRunORM,
    BarORM,
    FillORM,
    OrderORM,
    PnlSnapshotORM,
    PositionORM,
    SignalORM,
)
from quant.types import (
    Account,
    Bar,
    Fill,
    Order,
    OrderResult,
    OrderStatus,
    Position,
    Signal,
)

# --- Bars ---------------------------------------------------------------


class BarRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_many(self, bars: list[Bar]) -> int:
        if not bars:
            return 0
        values = [
            {
                "symbol": b.symbol,
                "ts": b.ts,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "adjusted": b.adjusted,
            }
            for b in bars
        ]
        stmt = pg_insert(BarORM).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "adjusted": stmt.excluded.adjusted,
            },
        )
        await self.session.execute(stmt)
        return len(values)

    async def get_range(self, symbol: str, start: date, end: date) -> list[Bar]:
        stmt = (
            select(BarORM)
            .where(BarORM.symbol == symbol, BarORM.ts >= start, BarORM.ts <= end)
            .order_by(BarORM.ts)
        )
        result = await self.session.execute(stmt)
        return [
            Bar(
                symbol=row.symbol,
                ts=row.ts,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                adjusted=row.adjusted,
            )
            for row in result.scalars()
        ]


# --- Orders & fills -----------------------------------------------------


class OrderRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_new(self, order: Order) -> int:
        row = OrderORM(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side.value,
            qty=order.qty,
            type=order.type.value,
            limit_price=order.limit_price,
            time_in_force=order.time_in_force.value,
            status=OrderStatus.NEW.value,
            strategy=order.strategy,
            submitted_at=order.submitted_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def record_result(self, result: OrderResult) -> None:
        stmt = select(OrderORM).where(OrderORM.client_order_id == result.order_id)
        row = (await self.session.execute(stmt)).scalar_one()
        row.status = result.status.value
        row.broker_order_id = result.broker_order_id
        row.rejection_reason = result.reason
        row.updated_at = datetime.now(UTC)


class FillRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, fill: Fill, *, order_pk: int) -> None:
        row = FillORM(
            order_id=order_pk,
            broker_fill_id=fill.broker_fill_id,
            symbol=fill.symbol,
            side=fill.side.value,
            qty=fill.qty,
            price=fill.price,
            ts=fill.ts,
            commission=fill.commission,
        )
        self.session.add(row)


# --- Positions ----------------------------------------------------------


class PositionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_many(self, positions: list[Position]) -> None:
        if not positions:
            return
        values = [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "as_of": p.as_of,
            }
            for p in positions
        ]
        stmt = pg_insert(PositionORM).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={
                "qty": stmt.excluded.qty,
                "avg_entry_price": stmt.excluded.avg_entry_price,
                "market_value": stmt.excluded.market_value,
                "unrealized_pnl": stmt.excluded.unrealized_pnl,
                "as_of": stmt.excluded.as_of,
            },
        )
        await self.session.execute(stmt)

    async def replace_all(self, positions: list[Position]) -> None:
        """Delete-all-and-insert. Used at reconciliation when broker state is
        the source of truth and any ghost rows must disappear.
        """
        await self.session.execute(delete(PositionORM))
        await self.upsert_many(positions)


# --- P&L ----------------------------------------------------------------


class PnlRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_from_account(self, account: Account) -> None:
        row = PnlSnapshotORM(
            ts=account.as_of,
            equity=account.equity,
            cash=account.cash,
            gross_exposure=account.portfolio_value,
            net_exposure=account.portfolio_value - account.cash,
        )
        self.session.add(row)


# --- Signals ------------------------------------------------------------


class SignalRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_many(self, signals: list[Signal]) -> None:
        if not signals:
            return
        rows = [
            SignalORM(
                strategy=s.strategy,
                symbol=s.symbol,
                ts=s.ts,
                direction=s.direction.value,
                target_weight=s.target_weight,
                confidence=s.confidence,
                meta=dict(s.metadata),
            )
            for s in signals
        ]
        self.session.add_all(rows)


# --- Backtest runs (for DSR trial count) --------------------------------


class BacktestRunRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        strategy: str,
        params_hash: str,
        params: dict[str, object],
        start_date: date,
        end_date: date,
        sharpe: float | None,
        cagr: float | None,
        max_drawdown: float | None,
    ) -> int:
        row = BacktestRunORM(
            strategy=strategy,
            params_hash=params_hash,
            params=params,
            start_date=start_date,
            end_date=end_date,
            sharpe=sharpe,
            cagr=cagr,
            max_drawdown=max_drawdown,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def count_trials(self, strategy: str) -> int:
        stmt = select(BacktestRunORM).where(BacktestRunORM.strategy == strategy)
        result = await self.session.execute(stmt)
        return len(list(result.scalars()))
