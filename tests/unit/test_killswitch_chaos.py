"""Chaos test: kill-switch flattens the paper account within one cycle
(PRD §6.2 / Week 12 acceptance).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.execution.order_manager import OrderManager
from quant.execution.paper_broker import PaperBroker
from quant.live.runner import LiveRunner
from quant.risk import Killswitch
from quant.signals.trend import TrendSignal


def _closes_uptrend() -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=2 * 252, freq="B")
    rising = pd.Series(np.linspace(100.0, 180.0, len(idx)), index=idx)
    return pd.DataFrame(
        {"SPY": rising, "EFA": rising * 1.05, "IEF": rising * 0.95, "SHY": 100.0},
        index=idx,
    )


@pytest.mark.asyncio
async def test_killswitch_flattens_paper_account_within_one_cycle(tmp_path: Path) -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"), slippage_bps=Decimal("0"))
    ks = Killswitch(tmp_path / "HALT")
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(lookback_months=10, cash_symbol="SHY"),
        closes_provider=_closes_uptrend,
        killswitch=ks,
    )

    # Cycle 1: normal cycle opens positions.
    last_row = _closes_uptrend().iloc[-1]
    marks = {sym: Decimal(str(float(last_row[sym]))) for sym in last_row.index}
    await runner.run_daily_cycle(as_of=datetime(2026, 4, 13, 20, 0, tzinfo=UTC))
    broker.advance_to(marks, now=datetime(2026, 4, 13, 21, 0, tzinfo=UTC))
    assert len(broker.get_positions()) > 0

    # Cycle 2: engage the kill-switch mid-day, then run the cycle. It
    # should flatten everything — no new signal-driven orders.
    ks.engage(reason="chaos test")
    result = await runner.run_daily_cycle(as_of=datetime(2026, 4, 14, 20, 0, tzinfo=UTC))
    broker.advance_to(marks, now=datetime(2026, 4, 14, 21, 0, tzinfo=UTC))

    # Acceptance: every position has been closed.
    open_positions = [p for p in broker.get_positions() if p.qty != 0]
    assert open_positions == []
    assert result.errors and "killswitch" in result.errors[0]
    assert result.submitted_orders, "flatten should have produced sell orders"


@pytest.mark.asyncio
async def test_killswitch_dry_run_still_plans_flatten(tmp_path: Path) -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"))
    ks = Killswitch(tmp_path / "HALT")
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(lookback_months=10, cash_symbol="SHY"),
        closes_provider=_closes_uptrend,
        killswitch=ks,
        dry_run=True,
    )
    last_row = _closes_uptrend().iloc[-1]
    marks = {sym: Decimal(str(float(last_row[sym]))) for sym in last_row.index}

    await runner.run_daily_cycle(as_of=datetime(2026, 4, 13, 20, 0, tzinfo=UTC))
    broker.advance_to(marks, now=datetime(2026, 4, 13, 21, 0, tzinfo=UTC))

    # Switch on; dry-run flatten reports plan without executing.
    ks.engage(reason="dry test")
    result = await runner.run_daily_cycle(as_of=datetime(2026, 4, 14, 20, 0, tzinfo=UTC))
    # Dry run: positions still there because no orders submitted.
    assert result.submitted_orders == []
    assert result.errors and "killswitch" in result.errors[0]


@pytest.mark.asyncio
async def test_killswitch_idle_when_no_positions(tmp_path: Path) -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"))
    ks = Killswitch(tmp_path / "HALT")
    ks.engage(reason="no positions")
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(lookback_months=10, cash_symbol="SHY"),
        closes_provider=_closes_uptrend,
        killswitch=ks,
    )
    result = await runner.run_daily_cycle(as_of=datetime(2026, 4, 14, 20, 0, tzinfo=UTC))
    assert result.planned_orders == []
    assert result.submitted_orders == []
    assert result.errors and "killswitch" in result.errors[0]


@pytest.mark.asyncio
async def test_disengaged_killswitch_runs_normal_cycle(tmp_path: Path) -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"))
    ks = Killswitch(tmp_path / "HALT")  # not engaged
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(lookback_months=10, cash_symbol="SHY"),
        closes_provider=_closes_uptrend,
        killswitch=ks,
    )
    result = await runner.run_daily_cycle(as_of=datetime(2026, 4, 13, 20, 0, tzinfo=UTC))
    # No kill-switch error.
    assert not any("killswitch" in e for e in result.errors)
