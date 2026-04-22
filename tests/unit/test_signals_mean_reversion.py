"""Tests for MeanReversionSignal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.signals import MeanReversionSignal
from quant.signals.mean_reversion import _walk_state


def _daily_index(days: int = 60) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-02", periods=days, freq="B")


def _ohlc(
    n: int,
    spec: dict[str, tuple[list[float], list[float], list[float]]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build (closes, highs, lows) frames from per-symbol (close, high, low) lists."""
    idx = _daily_index(n)
    closes = pd.DataFrame({s: c for s, (c, _, _) in spec.items()}, index=idx)
    highs = pd.DataFrame({s: h for s, (_, h, _) in spec.items()}, index=idx)
    lows = pd.DataFrame({s: lo for s, (_, _, lo) in spec.items()}, index=idx)
    return closes, highs, lows


# --- Contract guards --------------------------------------------------


def test_rejects_missing_cash_symbol() -> None:
    n = 30
    c, h, lo = _ohlc(n, {"SPY": ([100.0] * n, [101.0] * n, [99.0] * n)})
    with pytest.raises(ValueError, match="cash symbol"):
        MeanReversionSignal().target_weights(c, h, lo)


def test_rejects_no_risk_symbols() -> None:
    n = 30
    idx = _daily_index(n)
    c = pd.DataFrame({"SGOV": [100.0] * n}, index=idx)
    h = pd.DataFrame({"SGOV": [100.0] * n}, index=idx)
    lo = pd.DataFrame({"SGOV": [100.0] * n}, index=idx)
    with pytest.raises(ValueError, match="risk symbol"):
        MeanReversionSignal().target_weights(c, h, lo)


def test_rejects_misaligned_highs_lows() -> None:
    n = 30
    c, h, _ = _ohlc(n, {"SPY": ([100.0] * n, [101.0] * n, [99.0] * n)})
    c["SGOV"] = 100.0
    h["SGOV"] = 100.0
    lo = pd.DataFrame(
        {"SPY": [99.0] * (n - 1), "SGOV": [100.0] * (n - 1)},
        index=pd.date_range("2024-01-02", periods=n - 1, freq="B"),
    )
    with pytest.raises(ValueError, match="same index"):
        MeanReversionSignal().target_weights(c, h, lo)


def test_rejects_missing_high_column() -> None:
    n = 30
    idx = _daily_index(n)
    c = pd.DataFrame({"SPY": [100.0] * n, "SGOV": [100.0] * n}, index=idx)
    h = pd.DataFrame({"SGOV": [100.0] * n}, index=idx)  # missing SPY
    lo = pd.DataFrame({"SPY": [99.0] * n, "SGOV": [100.0] * n}, index=idx)
    with pytest.raises(ValueError, match="missing risk symbols"):
        MeanReversionSignal().target_weights(c, h, lo)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ibs_entry": 0.5, "ibs_exit": 0.3},  # inverted
        {"ibs_entry": 0.0},  # out of range
        {"ibs_exit": 1.0},
        {"rsi_period": 1},
        {"max_positions": 0},
        {"rsi2_entry": 0.0},
        {"rsi2_entry": 100.1},
    ],
)
def test_rejects_bad_constructor_args(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        MeanReversionSignal(**kwargs)  # type: ignore[arg-type]


# --- State machine (pure function) ------------------------------------


def test_walk_state_enters_and_exits() -> None:
    idx = _daily_index(6)
    entry = pd.DataFrame({"A": [False, True, False, False, False, False]}, index=idx)
    exit_ = pd.DataFrame({"A": [False, False, False, True, False, False]}, index=idx)
    state = _walk_state(entry, exit_)
    assert state["A"].tolist() == [False, True, True, False, False, False]


def test_walk_state_exit_before_entry_on_same_day() -> None:
    idx = _daily_index(3)
    entry = pd.DataFrame({"A": [False, True, True]}, index=idx)
    exit_ = pd.DataFrame({"A": [False, False, True]}, index=idx)
    state = _walk_state(entry, exit_)
    # Day 3: was in position (entered day 2). Exit fires first → flat.
    # Then entry fires → re-enter → True.
    assert state["A"].tolist() == [False, True, True]


def test_walk_state_ignores_entry_when_already_in_position() -> None:
    idx = _daily_index(4)
    entry = pd.DataFrame({"A": [True, True, True, True]}, index=idx)
    exit_ = pd.DataFrame({"A": [False, False, False, False]}, index=idx)
    state = _walk_state(entry, exit_)
    assert state["A"].tolist() == [True] * 4


# --- End-to-end weights ------------------------------------------------


def _deterministic_signal_series() -> tuple[list[float], list[float], list[float]]:
    """A deliberate down-then-recover pattern engineered to trigger
    entry (low IBS + low RSI-2) on day ~10 and exit (high IBS) on day ~15.
    """
    n = 30
    close = [100.0] * n
    high = [101.0] * n
    low = [99.0] * n
    # Two big down-closes to drop RSI-2 near 0 on day 9.
    close[8] = 95.0
    low[8] = 94.99  # IBS = 0.0002
    high[8] = 100.0
    close[9] = 90.0
    low[9] = 89.99  # IBS = 0.001
    high[9] = 95.0
    # Recovery: big up-close on day 15 → IBS near 1.
    close[14] = 100.99
    low[14] = 90.0
    high[14] = 101.0  # IBS = (100.99 - 90) / (101 - 90) = 0.999
    return close, high, low


def test_weights_sum_to_one_on_every_emitted_row() -> None:
    close, high, low = _deterministic_signal_series()
    n = len(close)
    c, h, lo = _ohlc(
        n,
        {
            "SPY": (close, high, low),
            "SGOV": ([100.0] * n, [100.0] * n, [100.0] * n),
        },
    )
    w = MeanReversionSignal(max_positions=5).target_weights(c, h, lo)
    emitted = w.dropna(how="all")
    assert not emitted.empty
    totals = emitted.sum(axis=1)
    np.testing.assert_allclose(totals.values, 1.0, atol=1e-10)


def test_weights_emitted_only_on_state_change() -> None:
    n = 30
    # Construct a scenario where the state changes exactly once: entry
    # fires on day 5, exit on day 10. Between them: hold. No emissions.
    close = [100.0] * n
    high = [101.0] * n
    low = [99.0] * n
    # Force low-IBS + low-RSI on day 5; high-IBS on day 10.
    close[4] = 99.01  # IBS near 0
    close[9] = 100.99  # IBS near 1
    c, h, lo = _ohlc(
        n,
        {"SPY": (close, high, low), "SGOV": ([100.0] * n, [100.0] * n, [100.0] * n)},
    )
    w = MeanReversionSignal(
        ibs_entry=0.2, ibs_exit=0.7, rsi2_entry=100.0, max_positions=5
    ).target_weights(c, h, lo)
    emitted = w.dropna(how="all")
    # Emissions: the entry day and the exit day — two rows.
    assert len(emitted) == 2


def test_cash_absorbs_unfilled_slots() -> None:
    n = 20
    close = [100.0] * n
    close[4] = 99.01
    c, h, lo = _ohlc(
        n,
        {
            "SPY": (close, [101.0] * n, [99.0] * n),
            "SGOV": ([100.0] * n, [100.0] * n, [100.0] * n),
        },
    )
    w = MeanReversionSignal(
        ibs_entry=0.2, ibs_exit=0.7, rsi2_entry=100.0, max_positions=5
    ).target_weights(c, h, lo)
    emitted = w.dropna(how="all")
    # After entering SPY (1/5 = 20%), cash carries 4/5 = 80%.
    entry_row = emitted.iloc[0]
    assert entry_row["SPY"] == pytest.approx(0.2)
    assert entry_row["SGOV"] == pytest.approx(0.8)


def test_entry_requires_both_ibs_and_rsi() -> None:
    """IBS low but RSI high → no entry."""
    n = 30
    idx = _daily_index(n)
    # SPY: close hugs the low of the bar for the first 20 days → IBS low.
    # But close also rises steadily → RSI high.
    close = list(np.linspace(100, 130, n))
    high = [c + 2 for c in close]
    low = [c - 0.01 for c in close]  # IBS near 0
    c_df = pd.DataFrame({"SPY": close, "SGOV": [100.0] * n}, index=idx)
    h_df = pd.DataFrame({"SPY": high, "SGOV": [100.0] * n}, index=idx)
    lo_df = pd.DataFrame({"SPY": low, "SGOV": [100.0] * n}, index=idx)
    w = MeanReversionSignal(
        ibs_entry=0.2, ibs_exit=0.7, rsi2_entry=10.0, max_positions=5
    ).target_weights(c_df, h_df, lo_df)
    emitted = w.dropna(how="all")
    # Rising-market RSI stays well above 10 → no entries, no emissions.
    assert emitted.empty
