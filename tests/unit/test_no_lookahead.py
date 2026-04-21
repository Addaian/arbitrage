"""Look-ahead-bias guard.

Per PRD principle 4 ("test the risk layer") and the Week 4 acceptance
criterion. The test: for any feature function, shifting the input series
by k and computing the feature should produce output identical to
shifting the original output by k. If the function peeks ahead, the
shifted-input output will differ at row t from the shifted output.

This catches a wide family of mistakes (accidental `.shift(-1)`, using
`tomorrow` inside a `.rolling()` apply, misaligned joins, etc.).

For cross-sectional + regime functions the same invariant holds along the
date axis.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quant.features import cross_sectional as xs
from quant.features import regime as rg
from quant.features import technical as tech

# --- Test fixtures ------------------------------------------------------


def _close(n: int = 400, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0005, 0.012, size=n)
    return pd.Series(
        100.0 * np.exp(np.cumsum(shocks)),
        index=pd.date_range("2026-01-01", periods=n, freq="B"),
    )


def _ohlcv(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, size=n)))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.005, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.005, size=n)))
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2026-01-01", periods=n, freq="B"),
    )


def _wide_prices(n_dates: int = 400, n_syms: int = 5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=(n_dates, n_syms)), axis=0))
    return pd.DataFrame(
        data,
        index=pd.date_range("2026-01-01", periods=n_dates, freq="B"),
        columns=[f"S{i}" for i in range(n_syms)],
    )


# --- Invariant ----------------------------------------------------------


def _assert_shift_equivariant(
    fn: Callable[..., pd.Series | pd.DataFrame],
    data: Any,
    *,
    shift: int = 20,
    atol: float = 1e-10,
) -> None:
    """Feature of shifted(data) must equal shifted(feature of data)."""
    original = fn(data)
    shifted_data = data.shift(shift)
    shifted_output = fn(shifted_data)
    expected = original.shift(shift)

    # Compare on rows where both exist. NaN masks must match; numeric values
    # must be close.
    nan_mask_lhs = shifted_output.isna()
    nan_mask_rhs = expected.isna()

    if isinstance(expected, pd.Series):
        assert (nan_mask_lhs == nan_mask_rhs).all(), "NaN masks diverge"
        both_present = ~(nan_mask_lhs | nan_mask_rhs)
        np.testing.assert_allclose(
            shifted_output[both_present].values,
            expected[both_present].values,
            atol=atol,
        )
    else:
        assert (nan_mask_lhs == nan_mask_rhs).all().all(), "NaN masks diverge"
        both_present = ~(nan_mask_lhs | nan_mask_rhs)
        np.testing.assert_allclose(
            shifted_output.where(both_present).dropna(how="all").values,
            expected.where(both_present).dropna(how="all").values,
            atol=atol,
        )


# --- Parameterized scan across every feature ---------------------------

SINGLE_SERIES_FEATURES: list[tuple[str, Callable[[pd.Series], pd.Series]]] = [
    ("returns", tech.returns),
    ("log_returns", tech.log_returns),
    ("sma_20", lambda s: tech.sma(s, 20)),
    ("ema_12", lambda s: tech.ema(s, 12)),
    ("rsi_14", lambda s: tech.rsi(s, 14)),
    ("rolling_vol_21", lambda s: tech.rolling_vol(s, 21)),
    ("ewma_vol", lambda s: tech.ewma_vol(s.pct_change())),
    ("vix_log", rg.vix_log_level),
    ("vix_pct_252", lambda s: rg.vix_percentile(s, 252)),
]


@pytest.mark.parametrize(
    ("name", "fn"), SINGLE_SERIES_FEATURES, ids=[n for n, _ in SINGLE_SERIES_FEATURES]
)
def test_single_series_feature_has_no_lookahead(
    name: str, fn: Callable[[pd.Series], pd.Series]
) -> None:
    _ = name
    _assert_shift_equivariant(fn, _close(400))


def test_ibs_no_lookahead() -> None:
    df = _ohlcv(200)

    def _ibs(data: pd.DataFrame) -> pd.Series:
        return tech.ibs(data["high"], data["low"], data["close"])

    _assert_shift_equivariant(_ibs, df)


def test_atr_no_lookahead() -> None:
    df = _ohlcv(200)

    def _atr(data: pd.DataFrame) -> pd.Series:
        return tech.atr(data["high"], data["low"], data["close"], 14)

    _assert_shift_equivariant(_atr, df)


def test_compute_technical_features_no_lookahead() -> None:
    df = _ohlcv(300)
    _assert_shift_equivariant(tech.compute_technical_features, df)


def test_rank_cross_sectional_no_lookahead() -> None:
    # Cross-sectional rank is row-local so it's trivially non-peeking, but we
    # still verify to catch any future regression.
    frame = _wide_prices(300, 5)
    _assert_shift_equivariant(xs.rank_cross_sectional, frame)


def test_universe_momentum_no_lookahead() -> None:
    frame = _wide_prices(400, 5)
    _assert_shift_equivariant(
        lambda d: xs.universe_momentum(d, lookback_days=126),
        frame,
    )


def test_regime_bundle_no_lookahead() -> None:
    vix = _close(400).rename("vix")
    _assert_shift_equivariant(
        lambda s: rg.compute_regime_features(s).dropna(axis=1, how="all"),
        vix,
    )
