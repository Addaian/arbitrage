"""Unit tests for cross-sectional features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.cross_sectional import (
    demean_cross_sectional,
    rank_cross_sectional,
    top_n_mask,
    universe_momentum,
    zscore_cross_sectional,
)


def _wide(n_dates: int = 50, n_syms: int = 5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n_dates, freq="B")
    data = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=(n_dates, n_syms)), axis=0))
    return pd.DataFrame(data, index=idx, columns=[f"S{i}" for i in range(n_syms)])


def test_rank_ascending_order() -> None:
    frame = pd.DataFrame({"A": [1.0, 4.0], "B": [2.0, 3.0], "C": [3.0, 2.0]})
    r = rank_cross_sectional(frame)
    # Row 0: A=1, B=2, C=3 → ranks 1, 2, 3. Row 1: A=4 top, C=2 bottom.
    assert r.loc[0, "A"] == 1.0
    assert r.loc[0, "C"] == 3.0
    assert r.loc[1, "A"] == 3.0
    assert r.loc[1, "C"] == 1.0


def test_rank_pct_in_unit_interval() -> None:
    frame = _wide(30, 5)
    r = rank_cross_sectional(frame, pct=True)
    clean = r.dropna()
    assert (clean >= 0).all().all()
    assert (clean <= 1).all().all()


def test_top_n_mask_selects_n() -> None:
    frame = pd.DataFrame({"A": [1.0], "B": [3.0], "C": [2.0], "D": [4.0], "E": [0.5]})
    mask = top_n_mask(frame, n=2)
    picks = mask.loc[0]
    assert picks["D"] is np.True_ or picks["D"]  # top value
    assert picks["B"] is np.True_ or picks["B"]  # second
    assert int(mask.sum(axis=1).iloc[0]) == 2


def test_top_n_rejects_zero() -> None:
    with pytest.raises(ValueError):
        top_n_mask(pd.DataFrame({"A": [1.0]}), n=0)


def test_zscore_has_zero_mean_per_row() -> None:
    frame = pd.DataFrame(
        {"A": [1.0, 10.0], "B": [2.0, 20.0], "C": [3.0, 30.0]},
    )
    z = zscore_cross_sectional(frame)
    assert z.mean(axis=1).abs().max() == pytest.approx(0.0, abs=1e-12)


def test_zscore_handles_zero_variance_row() -> None:
    frame = pd.DataFrame({"A": [5.0, 5.0], "B": [5.0, 5.0]})
    z = zscore_cross_sectional(frame)
    # Zero std → NaN output (undefined z-score).
    assert z.isna().all().all()


def test_demean_sums_to_zero() -> None:
    frame = pd.DataFrame({"A": [1.0, 10.0], "B": [2.0, 20.0], "C": [3.0, 30.0]})
    d = demean_cross_sectional(frame)
    assert d.sum(axis=1).abs().max() == pytest.approx(0.0, abs=1e-12)


def test_universe_momentum_returns_expected_window() -> None:
    idx = pd.date_range("2026-01-01", periods=10, freq="B")
    prices = pd.DataFrame(
        {
            "A": np.linspace(100, 110, 10),  # +10%
            "B": np.linspace(100, 95, 10),  # -5%
        },
        index=idx,
    )
    # lookback=5: compare t-5 to t. On index 5, A rose 5pts / 104.44 initial.
    mom = universe_momentum(prices, lookback_days=5)
    assert mom.iloc[:5].isna().all().all()  # warmup
    assert mom.iloc[-1]["A"] > 0
    assert mom.iloc[-1]["B"] < 0


def test_universe_momentum_rejects_zero_lookback() -> None:
    with pytest.raises(ValueError):
        universe_momentum(pd.DataFrame({"A": [1.0]}), lookback_days=0)
