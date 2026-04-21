"""Tests for the backtest engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.backtest import (
    align_on_common_dates,
    clip_to_range,
    closes_from_bars,
    compute_tearsheet,
    monthly_returns_pivot,
    run_backtest,
)
from quant.types import Bar


def _daily_index(n: int = 252) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-02", periods=n, freq="B")


def test_single_asset_all_in_matches_buy_and_hold() -> None:
    idx = _daily_index(252)
    price = pd.Series(np.linspace(100.0, 200.0, len(idx)), index=idx)
    closes = pd.DataFrame({"SPY": price, "SGOV": pd.Series(100.0, index=idx)})
    # Enter 100% SPY on day 1; hold for the rest of the series.
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY", "SGOV"])
    weights.iloc[0] = [1.0, 0.0]

    result = run_backtest(closes, weights, fees=0.0, slippage=0.0)
    # Buy-and-hold return = 200/100 - 1 = 1.0 (100%).
    assert result.equity.iloc[-1] / result.initial_cash == pytest.approx(2.0, rel=1e-6)


def test_all_cash_equity_flat() -> None:
    idx = _daily_index(100)
    price = pd.Series(np.linspace(100.0, 200.0, len(idx)), index=idx)
    closes = pd.DataFrame({"SPY": price, "SGOV": pd.Series(100.0, index=idx)})
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY", "SGOV"])
    weights.iloc[0] = [0.0, 1.0]  # 100% cash, which is flat at 100.0

    result = run_backtest(closes, weights, fees=0.0, slippage=0.0)
    # SGOV is flat → portfolio equity unchanged.
    assert result.equity.iloc[-1] == pytest.approx(result.initial_cash, rel=1e-10)


def test_rebalance_cost_is_charged() -> None:
    idx = _daily_index(100)
    closes = pd.DataFrame({"SPY": pd.Series(100.0, index=idx), "SGOV": pd.Series(100.0, index=idx)})
    # Two rebalances: flip from cash to equity then back. Each rebalance
    # has turnover = 2 (sell one side, buy the other).
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY", "SGOV"])
    weights.iloc[0] = [0.0, 1.0]
    weights.iloc[50] = [1.0, 0.0]

    result = run_backtest(closes, weights, fees=0.001, slippage=0.0)
    assert not result.trades.empty
    # First trade: turnover ~1.0 (go from 0 to 100% cash).
    # Second trade: turnover ~2.0 (swap sides).
    assert result.trades["turnover"].iloc[1] == pytest.approx(2.0)


def test_mismatched_columns_rejected() -> None:
    idx = _daily_index(10)
    closes = pd.DataFrame({"SPY": 100.0, "SGOV": 100.0}, index=idx)
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY", "QQQ"])
    with pytest.raises(ValueError, match="columns"):
        run_backtest(closes, weights)


def test_empty_closes_rejected() -> None:
    closes = pd.DataFrame(columns=["SPY"])
    weights = pd.DataFrame(columns=["SPY"])
    with pytest.raises(ValueError, match="empty"):
        run_backtest(closes, weights)


def test_no_rebalance_rows_rejected() -> None:
    idx = _daily_index(10)
    closes = pd.DataFrame({"SPY": pd.Series(100.0, index=idx)})
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY"])
    with pytest.raises(ValueError, match="no rebalance"):
        run_backtest(closes, weights)


# --- Tearsheet ----------------------------------------------------------


def test_tearsheet_metrics_on_flat_equity() -> None:
    idx = _daily_index(252)
    closes = pd.DataFrame({"SPY": pd.Series(100.0, index=idx), "SGOV": pd.Series(100.0, index=idx)})
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY", "SGOV"])
    weights.iloc[0] = [0.0, 1.0]
    result = run_backtest(closes, weights, fees=0.0, slippage=0.0)
    ts = compute_tearsheet(result)
    assert ts.cagr == pytest.approx(0.0, abs=1e-6)
    assert ts.max_drawdown == pytest.approx(0.0, abs=1e-6)
    assert ts.annual_vol == pytest.approx(0.0, abs=1e-6)


def test_monthly_pivot_has_expected_shape() -> None:
    idx = _daily_index(252 * 2)
    closes = pd.DataFrame(
        {
            "SPY": pd.Series(np.linspace(100, 110, len(idx)), index=idx),
            "SGOV": pd.Series(100.0, index=idx),
        }
    )
    weights = pd.DataFrame(np.nan, index=idx, columns=["SPY", "SGOV"])
    weights.iloc[0] = [1.0, 0.0]
    result = run_backtest(closes, weights, fees=0.0, slippage=0.0)
    pivot = monthly_returns_pivot(result)
    assert pivot.shape[0] >= 2  # at least 2 years
    assert pivot.shape[1] <= 12  # up to 12 months per year


# --- Helpers ------------------------------------------------------------


def _bar(symbol: str, ts: date, price: str) -> Bar:
    return Bar(
        symbol=symbol,
        ts=ts,
        open=Decimal(price),
        high=Decimal(price) + Decimal("1"),
        low=Decimal(price) - Decimal("1"),
        close=Decimal(price),
        volume=Decimal("1000"),
    )


def test_closes_from_bars_aligns_symbols() -> None:
    bars = {
        "SPY": [_bar("SPY", date(2026, 1, 2), "100"), _bar("SPY", date(2026, 1, 3), "101")],
        "QQQ": [_bar("QQQ", date(2026, 1, 2), "200"), _bar("QQQ", date(2026, 1, 3), "202")],
    }
    frame = closes_from_bars(bars)
    assert list(frame.columns) == ["SPY", "QQQ"]
    assert frame.loc[pd.Timestamp(date(2026, 1, 2)), "SPY"] == 100.0
    assert frame.loc[pd.Timestamp(date(2026, 1, 3)), "QQQ"] == 202.0


def test_closes_from_bars_forward_fills_holes() -> None:
    # SPY trades both days, QQQ only the second. Forward-fill to avoid NaN.
    bars = {
        "SPY": [_bar("SPY", date(2026, 1, 2), "100"), _bar("SPY", date(2026, 1, 3), "101")],
        "QQQ": [_bar("QQQ", date(2026, 1, 3), "200")],
    }
    frame = closes_from_bars(bars)
    # First row: SPY=100, QQQ=NaN (forward-fill doesn't invent data).
    assert np.isnan(frame.iloc[0]["QQQ"])
    assert frame.iloc[1]["QQQ"] == 200.0


def test_align_on_common_dates_drops_sparse_rows() -> None:
    idx = pd.date_range("2026-01-02", periods=5, freq="B")
    frame = pd.DataFrame(
        {"SPY": [100, 101, 102, 103, 104], "QQQ": [np.nan, np.nan, 200, 201, 202]},
        index=idx,
    )
    out = align_on_common_dates(frame, min_periods=2)
    assert len(out) == 3  # rows 0 and 1 dropped


def test_clip_to_range() -> None:
    idx = pd.date_range("2026-01-02", periods=10, freq="B")
    frame = pd.DataFrame({"SPY": np.arange(10.0)}, index=idx)
    clipped = clip_to_range(frame, start=date(2026, 1, 5), end=date(2026, 1, 10))
    assert clipped.index[0] >= pd.Timestamp("2026-01-05")
    assert clipped.index[-1] <= pd.Timestamp("2026-01-10")
