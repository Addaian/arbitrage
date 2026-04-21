"""Tests for the TrendSignal strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.signals import TrendSignal


def _daily_index(years: int = 5) -> pd.DatetimeIndex:
    return pd.date_range("2020-01-02", periods=years * 252, freq="B")


def _uptrending_closes(years: int = 5) -> pd.DataFrame:
    idx = _daily_index(years)
    # Strictly rising — trend signal should be "long everything"
    rising = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    flat_cash = pd.Series(100.0, index=idx)
    return pd.DataFrame({"SPY": rising, "QQQ": rising * 1.1, "SGOV": flat_cash})


def _downtrending_closes(years: int = 5) -> pd.DataFrame:
    idx = _daily_index(years)
    falling = pd.Series(np.linspace(200, 100, len(idx)), index=idx)
    flat_cash = pd.Series(100.0, index=idx)
    return pd.DataFrame({"SPY": falling, "QQQ": falling, "SGOV": flat_cash})


# --- Basic contract -----------------------------------------------------


def test_rejects_missing_cash_symbol() -> None:
    idx = _daily_index(3)
    closes = pd.DataFrame({"SPY": np.linspace(100, 150, len(idx))}, index=idx)
    with pytest.raises(ValueError, match="cash symbol"):
        TrendSignal().target_weights(closes)


def test_rejects_nonpositive_lookback() -> None:
    with pytest.raises(ValueError):
        TrendSignal(lookback_months=0)


def test_rejects_no_risk_symbols() -> None:
    idx = _daily_index(3)
    closes = pd.DataFrame({"SGOV": np.full(len(idx), 100.0)}, index=idx)
    with pytest.raises(ValueError, match="risk symbol"):
        TrendSignal().target_weights(closes)


# --- Weight-shape invariants -------------------------------------------


def test_weights_sum_to_one_on_rebalance_days() -> None:
    closes = _uptrending_closes(years=5)
    w = TrendSignal(lookback_months=10).target_weights(closes)
    rebalances = w.dropna(how="all")
    assert not rebalances.empty
    totals = rebalances.sum(axis=1)
    np.testing.assert_allclose(totals.values, 1.0, atol=1e-12)


def test_weights_have_same_shape_as_closes() -> None:
    closes = _uptrending_closes(years=3)
    w = TrendSignal().target_weights(closes)
    assert list(w.columns) == list(closes.columns)
    assert w.index.equals(closes.index)


def test_warmup_rebalance_is_all_cash() -> None:
    closes = _uptrending_closes(years=3)
    w = TrendSignal(lookback_months=10).target_weights(closes)
    rebalances = w.dropna(how="all")
    # The earliest rebalance row should appear on the first trading day
    # *after* 10 monthly closes have accumulated — prior to that the
    # signal is undefined, so the strategy is 100% cash.
    first = rebalances.iloc[0]
    assert first["SGOV"] == 1.0
    assert first["SPY"] == 0.0
    assert first["QQQ"] == 0.0


def test_all_long_when_uptrending() -> None:
    closes = _uptrending_closes(years=5)
    w = TrendSignal(lookback_months=10).target_weights(closes)
    # After warmup (roughly 10 months in), every asset is above its SMA,
    # so weights should split equally between risk assets and SGOV stays 0.
    after_warmup = w.dropna(how="all").iloc[-1]
    assert after_warmup["SPY"] == pytest.approx(0.5)
    assert after_warmup["QQQ"] == pytest.approx(0.5)
    assert after_warmup["SGOV"] == pytest.approx(0.0)


def test_all_cash_when_downtrending() -> None:
    closes = _downtrending_closes(years=5)
    w = TrendSignal(lookback_months=10).target_weights(closes)
    after_warmup = w.dropna(how="all").iloc[-1]
    assert after_warmup["SGOV"] == pytest.approx(1.0)
    assert after_warmup["SPY"] == pytest.approx(0.0)
    assert after_warmup["QQQ"] == pytest.approx(0.0)


def test_mixed_signals_split_correctly() -> None:
    idx = _daily_index(5)
    rising = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    falling = pd.Series(np.linspace(200, 100, len(idx)), index=idx)
    flat = pd.Series(100.0, index=idx)
    closes = pd.DataFrame({"SPY": rising, "EFA": falling, "SGOV": flat})
    w = TrendSignal(lookback_months=10).target_weights(closes)
    last = w.dropna(how="all").iloc[-1]
    # SPY gets 1/2 (active), EFA gets 0, remaining 1/2 stays in SGOV.
    assert last["SPY"] == pytest.approx(0.5)
    assert last["EFA"] == pytest.approx(0.0)
    assert last["SGOV"] == pytest.approx(0.5)


# --- Rebalance timing ---------------------------------------------------


def test_rebalance_frequency_is_monthly() -> None:
    closes = _uptrending_closes(years=5)
    w = TrendSignal(lookback_months=10).target_weights(closes)
    rebalances = w.dropna(how="all")
    months = {(ts.year, ts.month) for ts in rebalances.index}
    # Exactly one rebalance per represented (year, month).
    assert len(months) == len(rebalances)


def test_rebalance_date_is_first_trading_day_of_month() -> None:
    closes = _uptrending_closes(years=2)
    w = TrendSignal(lookback_months=10).target_weights(closes)
    rebalances = w.dropna(how="all")
    for ts in rebalances.index:
        earlier = closes.index[(closes.index.year == ts.year) & (closes.index.month == ts.month)]
        assert ts == earlier.min(), f"rebalance at {ts} is not the first trading day of its month"
