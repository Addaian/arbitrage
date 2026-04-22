"""Tests for the portfolio combiner."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.portfolio import combine_weights, rebalance_dates


def _frame(index: pd.DatetimeIndex, values: dict[str, list[float | None]]) -> pd.DataFrame:
    return pd.DataFrame({k: pd.array(v, dtype="float64") for k, v in values.items()}, index=index)


# --- Guards ------------------------------------------------------------


def test_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        combine_weights({}, {})


def test_rejects_mismatched_keys() -> None:
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    f = _frame(idx, {"SPY": [1.0, float("nan"), float("nan")]})
    with pytest.raises(ValueError, match="must equal"):
        combine_weights({"trend": f}, {"trend": 0.5, "mom": 0.5})


def test_rejects_allocations_not_summing_to_one() -> None:
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    f = _frame(idx, {"SPY": [1.0, float("nan"), float("nan")]})
    with pytest.raises(ValueError, match="sum to"):
        combine_weights({"trend": f}, {"trend": 0.5})


def test_rejects_empty_sleeve_frame() -> None:
    f = pd.DataFrame(columns=["SPY"])
    with pytest.raises(ValueError, match="empty"):
        combine_weights({"trend": f}, {"trend": 1.0})


# --- Single-strategy identity -----------------------------------------


def test_single_strategy_identity() -> None:
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    f = _frame(
        idx, {"SPY": [1.0, float("nan"), float("nan")], "CASH": [0.0, float("nan"), float("nan")]}
    )
    combined = combine_weights({"trend": f}, {"trend": 1.0})
    # Post-ffill, all three rows should read SPY=1, CASH=0.
    assert combined["SPY"].tolist() == [1.0, 1.0, 1.0]
    assert combined["CASH"].tolist() == [0.0, 0.0, 0.0]


# --- Two-strategy weighted sum ----------------------------------------


def test_two_strategy_sum() -> None:
    idx = pd.date_range("2024-01-02", periods=2, freq="B")
    trend = _frame(idx, {"SPY": [1.0, float("nan")], "CASH": [0.0, float("nan")]})
    mom = _frame(idx, {"QQQ": [1.0, float("nan")], "CASH": [0.0, float("nan")]})
    combined = combine_weights({"trend": trend, "momentum": mom}, {"trend": 0.7, "momentum": 0.3})
    # Union of columns: SPY, CASH, QQQ — combiner preserves seen-order.
    assert set(combined.columns) == {"SPY", "CASH", "QQQ"}
    first = combined.iloc[0]
    assert first["SPY"] == pytest.approx(0.7)
    assert first["QQQ"] == pytest.approx(0.3)
    assert first["CASH"] == pytest.approx(0.0)
    # Sum to 1.0 on rebalance days.
    assert combined.sum(axis=1).iloc[0] == pytest.approx(1.0)


def test_strategies_with_disjoint_universes_combine_cleanly() -> None:
    idx = pd.date_range("2024-01-02", periods=2, freq="B")
    trend = _frame(idx, {"SPY": [1.0, float("nan")]})
    mom = _frame(idx, {"IEF": [1.0, float("nan")]})
    combined = combine_weights({"trend": trend, "momentum": mom}, {"trend": 0.6, "momentum": 0.4})
    first = combined.iloc[0]
    assert first["SPY"] == pytest.approx(0.6)
    assert first["IEF"] == pytest.approx(0.4)


# --- NaN / pre-signal behavior ----------------------------------------


def test_prefix_rows_before_any_rebalance_are_nan() -> None:
    idx = pd.date_range("2024-01-02", periods=5, freq="B")
    # Rebalance only on row 2 (index 2) → rows 0, 1 are pre-signal.
    f = _frame(
        idx,
        {
            "SPY": [float("nan"), float("nan"), 1.0, float("nan"), float("nan")],
            "CASH": [float("nan"), float("nan"), 0.0, float("nan"), float("nan")],
        },
    )
    combined = combine_weights({"trend": f}, {"trend": 1.0})
    # First two rows all-NaN — no signal has fired yet.
    assert combined.iloc[0].isna().all()
    assert combined.iloc[1].isna().all()
    # From row 2 onwards, the sleeve has been established.
    assert combined.iloc[2]["SPY"] == pytest.approx(1.0)
    assert combined.iloc[4]["SPY"] == pytest.approx(1.0)  # forward-filled


def test_rebalance_dates_is_union() -> None:
    idx = pd.date_range("2024-01-02", periods=5, freq="B")
    trend = _frame(
        idx,
        {
            "SPY": [1.0, float("nan"), float("nan"), 0.5, float("nan")],
            "CASH": [0.0, float("nan"), float("nan"), 0.5, float("nan")],
        },
    )
    mom = _frame(
        idx,
        {
            "QQQ": [float("nan"), 1.0, float("nan"), float("nan"), float("nan")],
            "CASH": [float("nan"), 0.0, float("nan"), float("nan"), float("nan")],
        },
    )
    dates = rebalance_dates({"trend": trend, "momentum": mom})
    assert len(dates) == 3
    assert list(dates) == sorted([idx[0], idx[1], idx[3]])


def test_rebalance_dates_empty_for_no_signals() -> None:
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    f = pd.DataFrame(np.nan, index=idx, columns=["SPY"], dtype=float)
    assert len(rebalance_dates({"trend": f})) == 0
