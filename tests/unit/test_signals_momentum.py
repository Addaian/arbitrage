"""Tests for the MomentumSignal strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.signals import MomentumSignal


def _daily_index(years: int = 5) -> pd.DatetimeIndex:
    return pd.date_range("2020-01-02", periods=years * 252, freq="B")


def _mixed_returns_closes(years: int = 5) -> pd.DataFrame:
    """5 risk assets with clearly different momentum profiles + cash."""
    idx = _daily_index(years)
    n = len(idx)
    data = {
        # Strong uptrend → top 1
        "A": np.linspace(100, 300, n),
        # Moderate uptrend → top 2
        "B": np.linspace(100, 200, n),
        # Mild uptrend → top 3
        "C": np.linspace(100, 150, n),
        # Flat → fails absolute-momentum gate
        "D": np.full(n, 100.0),
        # Downtrend → fails absolute-momentum gate
        "E": np.linspace(200, 100, n),
        "SGOV": np.full(n, 100.0),
    }
    return pd.DataFrame(data, index=idx)


# --- Contract ----------------------------------------------------------


def test_rejects_missing_cash_symbol() -> None:
    idx = _daily_index(2)
    closes = pd.DataFrame({"A": np.linspace(100, 150, len(idx))}, index=idx)
    with pytest.raises(ValueError, match="cash symbol"):
        MomentumSignal().target_weights(closes)


def test_rejects_too_few_risk_symbols() -> None:
    idx = _daily_index(2)
    closes = pd.DataFrame({"A": np.linspace(100, 150, len(idx)), "SGOV": 100.0}, index=idx)
    with pytest.raises(ValueError, match="at least 3"):
        MomentumSignal(top_n=3).target_weights(closes)


def test_rejects_nonpositive_lookback() -> None:
    with pytest.raises(ValueError, match="lookback_months"):
        MomentumSignal(lookback_months=0)


def test_rejects_nonpositive_top_n() -> None:
    with pytest.raises(ValueError, match="top_n"):
        MomentumSignal(top_n=0)


# --- Weight shape invariants ------------------------------------------


def test_top_3_weights_after_warmup() -> None:
    closes = _mixed_returns_closes(years=3)
    w = MomentumSignal(lookback_months=6, top_n=3).target_weights(closes)
    rebalances = w.dropna(how="all")
    # With default gate off: top 3 by 6mo return. A > B > C > D (flat) > E (neg).
    last = rebalances.iloc[-1]
    assert last["A"] == pytest.approx(1 / 3)
    assert last["B"] == pytest.approx(1 / 3)
    assert last["C"] == pytest.approx(1 / 3)
    assert last["D"] == pytest.approx(0.0)
    assert last["E"] == pytest.approx(0.0)
    assert last["SGOV"] == pytest.approx(0.0)
    assert last.sum() == pytest.approx(1.0)


def test_weights_sum_to_one_on_every_rebalance() -> None:
    closes = _mixed_returns_closes(years=3)
    w = MomentumSignal(lookback_months=6).target_weights(closes)
    totals = w.dropna(how="all").sum(axis=1)
    np.testing.assert_allclose(totals.values, 1.0, atol=1e-10)


def test_weights_columns_match_closes() -> None:
    closes = _mixed_returns_closes(years=2)
    w = MomentumSignal(lookback_months=6).target_weights(closes)
    assert list(w.columns) == list(closes.columns)
    assert w.index.equals(closes.index)


def test_warmup_rebalance_is_all_cash() -> None:
    closes = _mixed_returns_closes(years=2)
    w = MomentumSignal(lookback_months=6).target_weights(closes)
    first = w.dropna(how="all").iloc[0]
    # First rebalance fires before 6 months of data accumulate → all cash.
    assert first["SGOV"] == pytest.approx(1.0)


def test_all_cash_when_everything_is_flat_or_down_under_filter() -> None:
    """With the optional absolute-momentum filter on, a universe with no
    positive names should rotate to 100% cash.
    """
    idx = _daily_index(3)
    n = len(idx)
    closes = pd.DataFrame(
        {
            "A": np.linspace(200, 100, n),
            "B": np.linspace(150, 100, n),
            "C": np.linspace(110, 100, n),
            "D": np.full(n, 100.0),
            "SGOV": np.full(n, 100.0),
        },
        index=idx,
    )
    w = MomentumSignal(lookback_months=6, top_n=3, abs_momentum_filter=True).target_weights(closes)
    last = w.dropna(how="all").iloc[-1]
    assert last["SGOV"] == pytest.approx(1.0)


def test_filter_off_still_holds_top_n_even_if_all_negative() -> None:
    """Default behavior (filter off, per PRD §5.2): hold top-N by rank,
    even if every name is down — the cash decision is what the combined
    portfolio + regime overlay handles later.
    """
    idx = _daily_index(3)
    n = len(idx)
    closes = pd.DataFrame(
        {
            "A": np.linspace(200, 100, n),
            "B": np.linspace(150, 100, n),
            "C": np.linspace(120, 100, n),
            "D": np.full(n, 100.0),
            "SGOV": np.full(n, 100.0),
        },
        index=idx,
    )
    w = MomentumSignal(lookback_months=6, top_n=3).target_weights(closes)
    last = w.dropna(how="all").iloc[-1]
    # D is flat (0% return, beats all negatives) so ranks first; C, B next.
    assert last["D"] == pytest.approx(1 / 3)
    assert last["C"] == pytest.approx(1 / 3)
    assert last["B"] == pytest.approx(1 / 3)
    assert last["SGOV"] == pytest.approx(0.0)


def test_partial_fill_parks_remainder_in_cash_under_filter() -> None:
    """With filter on: only 2 of 3 pass → 3rd slot goes to cash."""
    idx = _daily_index(3)
    n = len(idx)
    closes = pd.DataFrame(
        {
            "A": np.linspace(100, 200, n),
            "B": np.linspace(100, 180, n),
            "C": np.linspace(200, 100, n),
            "D": np.linspace(150, 100, n),
            "SGOV": np.full(n, 100.0),
        },
        index=idx,
    )
    w = MomentumSignal(lookback_months=6, top_n=3, abs_momentum_filter=True).target_weights(closes)
    last = w.dropna(how="all").iloc[-1]
    assert last["A"] == pytest.approx(1 / 3)
    assert last["B"] == pytest.approx(1 / 3)
    assert last["SGOV"] == pytest.approx(1 / 3)
    assert last.sum() == pytest.approx(1.0)


# --- Rebalance timing -------------------------------------------------


def test_rebalance_frequency_is_monthly() -> None:
    closes = _mixed_returns_closes(years=3)
    w = MomentumSignal(lookback_months=6).target_weights(closes)
    rebalances = w.dropna(how="all")
    months = {(ts.year, ts.month) for ts in rebalances.index}
    assert len(months) == len(rebalances)


def test_rebalance_date_is_first_trading_day_of_month() -> None:
    closes = _mixed_returns_closes(years=2)
    w = MomentumSignal(lookback_months=6).target_weights(closes)
    for ts in w.dropna(how="all").index:
        same_month = closes.index[(closes.index.year == ts.year) & (closes.index.month == ts.month)]
        assert ts == same_month.min()
