"""Integration: 3 consecutive daily cycles against PaperBroker + Postgres.

Meets the Wave 8 acceptance criterion: "Running the runner in paper
mode 3 days in a row shows coherent state in Postgres (no duplicate
orders, no ghost positions)."

Skips cleanly when Postgres isn't reachable; use `make up` to bring up
the local docker-compose Postgres first.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import func, select

from quant.execution.order_manager import OrderManager
from quant.execution.paper_broker import PaperBroker
from quant.live.runner import LiveRunner
from quant.signals.trend import TrendSignal
from quant.storage.db import dispose_engine, get_sessionmaker, session_scope
from quant.storage.models import (
    FillORM,
    OrderORM,
    PnlSnapshotORM,
    PositionORM,
    SignalORM,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def alembic_cfg() -> AlembicConfig:
    return AlembicConfig(str(Path("alembic.ini").resolve()))


def _uptrend_closes() -> pd.DataFrame:
    idx = pd.date_range("2020-01-02", periods=3 * 252, freq="B")
    rising = pd.Series(np.linspace(100.0, 200.0, len(idx)), index=idx)
    return pd.DataFrame(
        {"SPY": rising, "EFA": rising * 1.1, "IEF": rising * 0.9, "SHY": 100.0},
        index=idx,
    )


def test_three_day_paper_cycle_coherent(alembic_cfg: AlembicConfig) -> None:
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")

    async def _exercise() -> None:
        broker = PaperBroker(starting_cash=Decimal("100000"), slippage_bps=Decimal("0"))
        runner = LiveRunner(
            broker=broker,
            order_manager=OrderManager(broker, poll_timeout=0.0),
            signal=TrendSignal(lookback_months=10, cash_symbol="SHY"),
            closes_provider=_uptrend_closes,
            session_factory=get_sessionmaker(),
        )

        last_row = _uptrend_closes().iloc[-1]
        mark_prices = {sym: Decimal(str(float(last_row[sym]))) for sym in last_row.index}

        cycle_ts = [
            datetime(2026, 4, 17, 20, 0, tzinfo=UTC),
            datetime(2026, 4, 18, 20, 0, tzinfo=UTC),
            datetime(2026, 4, 19, 20, 0, tzinfo=UTC),
        ]

        for ts in cycle_ts:
            result = await runner.run_daily_cycle(as_of=ts)
            # Simulate the next-bar open filling queued orders.
            broker.advance_to(mark_prices, now=ts)
            # After the first cycle, subsequent cycles should have zero
            # or only sub-share drift — indicating we're at target.
            if ts != cycle_ts[0]:
                assert all(abs(d.delta) < Decimal("1.0") for d in result.drift)

        # Verify DB coherence.
        async with session_scope() as session:
            # No duplicate client_order_ids.
            dup = await session.execute(
                select(OrderORM.client_order_id, func.count())
                .group_by(OrderORM.client_order_id)
                .having(func.count() > 1)
            )
            assert list(dup) == []

            # Fills each map to an order.
            orphaned = await session.execute(
                select(FillORM)
                .outerjoin(OrderORM, FillORM.order_id == OrderORM.id)
                .where(OrderORM.id.is_(None))
            )
            assert orphaned.first() is None

            # Positions table matches broker state exactly (no ghosts).
            db_positions = (
                (await session.execute(select(PositionORM).order_by(PositionORM.symbol)))
                .scalars()
                .all()
            )
            broker_positions = sorted(broker.get_positions(), key=lambda p: p.symbol)
            assert [p.symbol for p in db_positions] == [p.symbol for p in broker_positions]
            for db_p, b_p in zip(db_positions, broker_positions, strict=True):
                assert db_p.qty == b_p.qty

            # PnL rows: one per cycle.
            pnl_count = (
                await session.execute(select(func.count()).select_from(PnlSnapshotORM))
            ).scalar_one()
            assert pnl_count == len(cycle_ts)

            # Signals: we write 4 rows (SPY/EFA/IEF/SHY) per cycle → 12.
            sig_count = (
                await session.execute(select(func.count()).select_from(SignalORM))
            ).scalar_one()
            assert sig_count == 4 * len(cycle_ts)

        await dispose_engine()

    asyncio.run(_exercise())

    alembic_command.downgrade(alembic_cfg, "base")
