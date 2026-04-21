"""Tests for LiveRunner.run_daily_cycle — against PaperBroker, no DB."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.execution.order_manager import OrderManager
from quant.execution.paper_broker import PaperBroker
from quant.live.runner import (
    CycleResult,
    LiveRunner,
    _compute_drift,
    _plan_orders,
)
from quant.signals.trend import TrendSignal
from quant.types import OrderSide, Position


def _uptrend_closes() -> pd.DataFrame:
    """3 years of clean uptrend — all signals long by month ~10."""
    idx = pd.date_range("2020-01-02", periods=3 * 252, freq="B")
    rising = pd.Series(np.linspace(100.0, 200.0, len(idx)), index=idx)
    return pd.DataFrame(
        {"SPY": rising, "EFA": rising * 1.1, "IEF": rising * 0.9, "SHY": 100.0},
        index=idx,
    )


def _mk_runner(*, dry_run: bool = False, broker: PaperBroker | None = None) -> LiveRunner:
    broker = broker or PaperBroker(starting_cash=Decimal("100000"), slippage_bps=Decimal("0"))
    signal = TrendSignal(lookback_months=10, cash_symbol="SHY")
    return LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=signal,
        closes_provider=_uptrend_closes,
        dry_run=dry_run,
    )


# --- Planner (pure function) ------------------------------------------


def test_plan_orders_emits_buy_when_target_above_current() -> None:
    planned = _plan_orders(
        target_weights={"SPY": Decimal("0.5")},
        latest_prices={"SPY": Decimal("100")},
        current_positions={},
        equity=Decimal("10000"),
    )
    assert len(planned) == 1
    p = planned[0]
    assert p.symbol == "SPY"
    assert p.side == OrderSide.BUY
    # equity=10000, weight=0.5, price=100 → target qty = 50.
    assert p.qty == Decimal("50.000000")


def test_plan_orders_emits_sell_when_holding_above_target() -> None:
    now = datetime.now(UTC)
    positions = {
        "SPY": Position(
            symbol="SPY",
            qty=Decimal("80"),
            avg_entry_price=Decimal("95"),
            market_value=Decimal("8000"),
            unrealized_pnl=Decimal("0"),
            as_of=now,
        )
    }
    planned = _plan_orders(
        target_weights={"SPY": Decimal("0.3")},
        latest_prices={"SPY": Decimal("100")},
        current_positions=positions,
        equity=Decimal("10000"),
    )
    assert planned[0].side == OrderSide.SELL
    assert planned[0].qty == Decimal("50.000000")  # 80 - 30


def test_plan_orders_skips_subshare_drift() -> None:
    now = datetime.now(UTC)
    positions = {
        "SPY": Position(
            symbol="SPY",
            qty=Decimal("29.9995"),
            avg_entry_price=Decimal("100"),
            market_value=Decimal("3000"),
            unrealized_pnl=Decimal("0"),
            as_of=now,
        )
    }
    planned = _plan_orders(
        target_weights={"SPY": Decimal("0.3")},
        latest_prices={"SPY": Decimal("100")},
        current_positions=positions,
        equity=Decimal("10000"),
    )
    assert planned == []  # delta 0.0005 < epsilon


def test_plan_orders_drops_symbols_without_price() -> None:
    planned = _plan_orders(
        target_weights={"SPY": Decimal("0.5"), "XXX": Decimal("0.5")},
        latest_prices={"SPY": Decimal("100")},  # XXX missing
        current_positions={},
        equity=Decimal("10000"),
    )
    assert [p.symbol for p in planned] == ["SPY"]


def test_plan_orders_handles_zero_price() -> None:
    planned = _plan_orders(
        target_weights={"SPY": Decimal("0.5")},
        latest_prices={"SPY": Decimal("0")},
        current_positions={},
        equity=Decimal("10000"),
    )
    assert planned == []


def test_compute_drift_flags_mismatch() -> None:
    now = datetime.now(UTC)
    positions = [
        Position(
            symbol="SPY",
            qty=Decimal("40"),
            avg_entry_price=Decimal("100"),
            market_value=Decimal("4000"),
            unrealized_pnl=Decimal("0"),
            as_of=now,
        ),
    ]
    drift = _compute_drift(
        target_weights={"SPY": Decimal("0.5")},
        latest_prices={"SPY": Decimal("100")},
        equity=Decimal("10000"),
        actual_positions=positions,
    )
    assert len(drift) == 1
    assert drift[0].delta == Decimal("-10.000000")


# --- End-to-end cycle -------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_emits_plan_without_submitting() -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"))
    runner = _mk_runner(dry_run=True, broker=broker)
    result = await runner.run_daily_cycle()
    assert isinstance(result, CycleResult)
    assert result.dry_run is True
    # Plan is populated but nothing submitted.
    assert len(result.planned_orders) > 0
    assert result.submitted_orders == []
    # Broker hasn't seen any orders.
    assert broker.get_positions() == []


@pytest.mark.asyncio
async def test_live_cycle_submits_and_updates_positions() -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"), slippage_bps=Decimal("0"))
    runner = _mk_runner(broker=broker)
    result = await runner.run_daily_cycle()
    assert len(result.submitted_orders) == len(result.planned_orders)
    # Orders are accepted by PaperBroker but haven't been filled
    # (no advance_to → no next-bar open). So final_positions reflects
    # pre-cycle state (empty).
    assert result.final_positions == []


@pytest.mark.asyncio
async def test_second_cycle_sees_no_drift_after_fills() -> None:
    """Cycle 1 submits orders, then fills happen at next-bar open, then
    Cycle 2 runs: it should see positions matching targets (drift empty
    or sub-epsilon) because Cycle 1 already moved us into position.
    """
    broker = PaperBroker(starting_cash=Decimal("100000"), slippage_bps=Decimal("0"))
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(lookback_months=10, cash_symbol="SHY"),
        closes_provider=_uptrend_closes,
    )

    await runner.run_daily_cycle()
    assert broker.get_positions() == []  # no fills yet — next-bar hasn't run

    # Simulate the next-bar open filling all queued orders.
    last_row = _uptrend_closes().iloc[-1]
    broker.advance_to({sym: Decimal(str(float(last_row[sym]))) for sym in last_row.index})
    assert len(broker.get_positions()) > 0

    result2 = await runner.run_daily_cycle()
    # After one round-trip, current positions match targets — nothing
    # bigger than one share away.
    assert all(abs(d.delta) < Decimal("1.0") for d in result2.drift)


@pytest.mark.asyncio
async def test_empty_closes_raises() -> None:
    broker = PaperBroker(starting_cash=Decimal("100000"))
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(cash_symbol="SHY"),
        closes_provider=pd.DataFrame,
        dry_run=True,
    )
    with pytest.raises(ValueError, match="empty"):
        await runner.run_daily_cycle()


@pytest.mark.asyncio
async def test_notifier_receives_start_and_complete() -> None:
    calls: list[str] = []

    class _RecordingNotifier:
        def cycle_start(self, strategy: str, now: datetime) -> None:
            calls.append(f"start:{strategy}")

        def cycle_complete(self, strategy: str, result: CycleResult) -> None:
            calls.append(f"complete:{strategy}")

        def cycle_error(self, strategy: str, message: str) -> None:  # pragma: no cover
            calls.append(f"error:{strategy}")

    broker = PaperBroker(starting_cash=Decimal("100000"))
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(cash_symbol="SHY"),
        closes_provider=_uptrend_closes,
        notifier=_RecordingNotifier(),  # type: ignore[arg-type]
        dry_run=True,
    )
    await runner.run_daily_cycle()
    assert calls == ["start:trend", "complete:trend"]


@pytest.mark.asyncio
async def test_notifier_receives_error_on_failure() -> None:
    calls: list[str] = []

    class _RecordingNotifier:
        def cycle_start(self, strategy: str, now: datetime) -> None:
            pass

        def cycle_complete(self, strategy: str, result: CycleResult) -> None:  # pragma: no cover
            pass

        def cycle_error(self, strategy: str, message: str) -> None:
            calls.append(message)

    broker = PaperBroker(starting_cash=Decimal("100000"))
    runner = LiveRunner(
        broker=broker,
        order_manager=OrderManager(broker, poll_timeout=0.0),
        signal=TrendSignal(cash_symbol="SHY"),
        closes_provider=pd.DataFrame,
        notifier=_RecordingNotifier(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        await runner.run_daily_cycle()
    assert len(calls) == 1 and "empty" in calls[0]
