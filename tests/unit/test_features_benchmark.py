"""Performance guard for feature engineering.

Week 4 acceptance criterion: full feature engineering on 10 ETFs x 20 years
of daily bars (~50k rows) takes <10s end-to-end.

Synthetic data is generated inline so the test needs no network or cache.
Runs under the `slow` marker — `.venv/bin/pytest -m slow`.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from quant.features import (
    compute_regime_features,
    compute_technical_features,
    rank_cross_sectional,
    universe_momentum,
)

pytestmark = pytest.mark.slow


def _synthetic_ohlcv(n_days: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0005, 0.012, size=n_days)
    close = 100.0 * np.exp(np.cumsum(shocks))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.005, size=n_days)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.005, size=n_days)))
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, size=n_days).astype(float),
        },
        index=pd.date_range("2006-01-01", periods=n_days, freq="B"),
    )


def test_feature_engineering_10_etfs_20yr_under_10s() -> None:
    n_days = 20 * 252  # 20 years of business days ≈ 5,040
    symbols = [f"SYM{i}" for i in range(10)]
    per_symbol = {s: _synthetic_ohlcv(n_days, seed=i) for i, s in enumerate(symbols)}

    t0 = time.perf_counter()

    # Per-symbol technical features.
    tech_features = {sym: compute_technical_features(df) for sym, df in per_symbol.items()}
    assert len(tech_features) == 10

    # Build a wide close-price frame for cross-sectional work.
    closes = pd.DataFrame({sym: df["close"] for sym, df in per_symbol.items()})
    rank_cross_sectional(closes)
    universe_momentum(closes, lookback_days=126)

    # Regime features on one synthetic VIX series.
    vix = _synthetic_ohlcv(n_days, seed=99)["close"].rename("vix")
    compute_regime_features(vix)

    elapsed = time.perf_counter() - t0
    assert elapsed < 10.0, f"feature pipeline took {elapsed:.2f}s, budget 10s"
