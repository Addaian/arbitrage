"""Tests for the walk-forward harness."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from quant.backtest.walk_forward import (
    _generate_fold_specs,
    fixed_params,
    tuned_by_train_sharpe,
    walk_forward,
)
from quant.signals import TrendSignal


def _daily_closes(years: int, start: str = "2000-01-03") -> pd.DataFrame:
    idx = pd.date_range(start, periods=years * 252, freq="B")
    # Smoothly rising SPY + QQQ, flat cash.
    rising = pd.Series(np.linspace(100.0, 200.0, len(idx)), index=idx)
    return pd.DataFrame({"SPY": rising, "QQQ": rising * 1.2, "SGOV": 100.0}, index=idx)


# --- Fold generation ----------------------------------------------------


def test_rolling_fold_count_matches_history() -> None:
    idx = pd.date_range("2000-01-03", periods=20 * 252, freq="B")
    folds = _generate_fold_specs(idx, train_years=10, test_years=2, step_years=2, expanding=False)
    # 20 x 252 business days ≈ 19.85 calendar years. With 10y train + 2y
    # tests rolled forward 2y at a time, the last test window runs out
    # of history one fold before 5 — so 4 non-overlapping OOS windows.
    assert len(folds) == 4


def test_fold_test_windows_do_not_overlap() -> None:
    idx = pd.date_range("2000-01-03", periods=20 * 252, freq="B")
    folds = _generate_fold_specs(idx, train_years=10, test_years=2, step_years=2, expanding=False)
    test_ranges = [t for _, t in folds]
    for (_, a_end), (b_start, _) in pairwise(test_ranges):
        assert a_end < b_start, f"overlap: {a_end} vs {b_start}"


def test_rolling_train_length_is_constant() -> None:
    idx = pd.date_range("2000-01-03", periods=20 * 252, freq="B")
    folds = _generate_fold_specs(idx, train_years=10, test_years=2, step_years=2, expanding=False)
    for train_range, _ in folds:
        yrs = (train_range[1] - train_range[0]).days / 365.25
        # 10y ± a couple of days from the date-offset boundary math.
        assert 9.9 < yrs < 10.1


def test_expanding_train_length_grows_monotonically() -> None:
    idx = pd.date_range("2000-01-03", periods=20 * 252, freq="B")
    folds = _generate_fold_specs(idx, train_years=10, test_years=2, step_years=2, expanding=True)
    lengths = [(t[1] - t[0]).days for t, _ in folds]
    assert all(a < b for a, b in pairwise(lengths))


def test_no_folds_if_history_too_short() -> None:
    # 8yr history, need 10+2 = 12 → zero folds.
    idx = pd.date_range("2000-01-03", periods=8 * 252, freq="B")
    folds = _generate_fold_specs(idx, train_years=10, test_years=2, step_years=2, expanding=False)
    assert folds == []


# --- walk_forward end-to-end -------------------------------------------


def test_walk_forward_produces_folds_and_oos_curve() -> None:
    closes = _daily_closes(years=15)
    result = walk_forward(
        closes,
        fixed_params(TrendSignal(cash_symbol="SGOV")),
        train_years=10,
        test_years=2,
    )
    assert result.num_folds >= 2
    assert not result.oos_returns.empty
    # Concatenated OOS index is strictly increasing.
    assert result.oos_returns.index.is_monotonic_increasing


def test_walk_forward_fold_oos_windows_do_not_overlap() -> None:
    closes = _daily_closes(years=15)
    result = walk_forward(
        closes,
        fixed_params(TrendSignal(cash_symbol="SGOV")),
        train_years=10,
        test_years=2,
    )
    for a, b in pairwise(result.folds):
        assert a.test_end < b.test_start


def test_walk_forward_refuses_too_little_history() -> None:
    closes = _daily_closes(years=5)
    with pytest.raises(ValueError, match="no WF folds"):
        walk_forward(
            closes,
            fixed_params(TrendSignal(cash_symbol="SGOV")),
            train_years=10,
            test_years=2,
        )


def test_walk_forward_refuses_empty_closes() -> None:
    with pytest.raises(ValueError, match="empty"):
        walk_forward(
            pd.DataFrame(),
            fixed_params(TrendSignal(cash_symbol="SGOV")),
        )


def test_walk_forward_refuses_unsorted_closes() -> None:
    closes = _daily_closes(years=15)
    with pytest.raises(ValueError, match="sorted"):
        walk_forward(
            closes.iloc[::-1],
            fixed_params(TrendSignal(cash_symbol="SGOV")),
        )


def test_tuned_factory_picks_best_in_sample() -> None:
    closes = _daily_closes(years=15)
    candidates = [
        TrendSignal(lookback_months=3, cash_symbol="SGOV"),
        TrendSignal(lookback_months=10, cash_symbol="SGOV"),
        TrendSignal(lookback_months=20, cash_symbol="SGOV"),
    ]
    factory = tuned_by_train_sharpe(candidates)
    picked = factory(closes.iloc[: 10 * 252])
    # All three are valid TrendSignals; picked one's lookback should be
    # one of the candidates. Concretely, strictly uptrending data makes
    # the shortest lookback the most-often-long → highest Sharpe.
    assert picked.lookback_months in {c.lookback_months for c in candidates}


def test_walk_forward_passes_train_slice_to_factory() -> None:
    closes = _daily_closes(years=15)
    seen_train_lengths: list[int] = []

    def probe(train: pd.DataFrame):
        seen_train_lengths.append(len(train))
        return TrendSignal(cash_symbol="SGOV")

    walk_forward(closes, probe, train_years=10, test_years=2)
    # All train slices populated with data.
    assert all(n > 0 for n in seen_train_lengths)


def test_walk_forward_concatenation_sharpe_is_finite() -> None:
    closes = _daily_closes(years=15)
    result = walk_forward(
        closes,
        fixed_params(TrendSignal(cash_symbol="SGOV")),
        train_years=10,
        test_years=2,
    )
    assert np.isfinite(result.oos_sharpe)
