"""Unit tests for technical indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.technical import (
    atr,
    compute_technical_features,
    ema,
    ewma_vol,
    ibs,
    log_returns,
    returns,
    rolling_vol,
    rsi,
    sma,
)


def _ohlcv(n: int = 100, seed: int = 0) -> pd.DataFrame:
    """Generate synthetic OHLCV data with realistic shape."""
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0005, 0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(shocks))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.005, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.005, size=n)))
    open_ = close * (1.0 + rng.normal(0.0, 0.003, size=n))
    open_ = np.clip(open_, low, high)
    volume = rng.integers(1_000_000, 5_000_000, size=n).astype(float)
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# --- Basic math ---------------------------------------------------------


def test_returns_formula() -> None:
    close = pd.Series([100.0, 110.0, 99.0])
    r = returns(close)
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.10)
    assert r.iloc[2] == pytest.approx(-0.10)


def test_log_returns_reconstruct_prices() -> None:
    close = pd.Series([100.0, 110.0, 121.0, 110.0])
    lr = log_returns(close)
    reconstructed = np.exp(lr.fillna(0).cumsum()) * close.iloc[0]
    pd.testing.assert_series_equal(reconstructed, close, check_names=False)


# --- Moving averages ----------------------------------------------------


def test_sma_warmup_is_nan() -> None:
    s = pd.Series(range(10), dtype=float)
    m = sma(s, window=3)
    assert m.isna().sum() == 2  # first 2 points warm up
    assert m.iloc[2] == pytest.approx(1.0)  # mean of 0, 1, 2


def test_sma_rejects_bad_window() -> None:
    with pytest.raises(ValueError, match="positive"):
        sma(pd.Series([1.0]), window=0)


def test_ema_converges_toward_series() -> None:
    s = pd.Series([100.0] * 50)
    e = ema(s, span=10)
    # After enough warmup, EMA of a constant series equals that constant.
    assert e.iloc[-1] == pytest.approx(100.0)


# --- RSI ----------------------------------------------------------------


def test_rsi_saturates_at_100_on_pure_uptrend() -> None:
    close = pd.Series(np.linspace(100, 200, 50))
    r = rsi(close, window=14)
    # Once warmed up, RSI on a strictly increasing series pins at 100.
    assert r.dropna().iloc[-1] == pytest.approx(100.0, abs=1e-6)


def test_rsi_saturates_at_0_on_pure_downtrend() -> None:
    close = pd.Series(np.linspace(200, 100, 50))
    r = rsi(close, window=14)
    assert r.dropna().iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_warmup_window() -> None:
    close = pd.Series([100.0, 101.0, 100.5])
    r = rsi(close, window=14)
    assert r.isna().all()  # all NaN within warmup


def test_rsi_rejects_small_window() -> None:
    with pytest.raises(ValueError):
        rsi(pd.Series([1.0, 2.0]), window=1)


# --- IBS ----------------------------------------------------------------


def test_ibs_at_close_extremes() -> None:
    high = pd.Series([110.0, 110.0, 110.0])
    low = pd.Series([100.0, 100.0, 100.0])
    close = pd.Series([100.0, 105.0, 110.0])
    result = ibs(high, low, close)
    assert result.iloc[0] == pytest.approx(0.0)
    assert result.iloc[1] == pytest.approx(0.5)
    assert result.iloc[2] == pytest.approx(1.0)


def test_ibs_flat_bar_is_nan() -> None:
    high = pd.Series([100.0])
    low = pd.Series([100.0])
    close = pd.Series([100.0])
    assert ibs(high, low, close).isna().iloc[0]


# --- ATR + volatility ---------------------------------------------------


def test_atr_positive_on_sane_data() -> None:
    df = _ohlcv(80)
    a = atr(df["high"], df["low"], df["close"])
    clean = a.dropna()
    assert (clean > 0).all()
    assert len(clean) > 0


def test_rolling_vol_annualization_factor() -> None:
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.0, 0.01, size=500))
    v_daily = rolling_vol(rets, window=60, annualize=False)
    v_annual = rolling_vol(rets, window=60, annualize=True, periods_per_year=252)
    # Annualization = sqrt(252) ~ 15.87
    ratio = (v_annual / v_daily).dropna()
    assert ratio.iloc[-1] == pytest.approx(np.sqrt(252), rel=1e-6)


def test_ewma_vol_rejects_bad_lambda() -> None:
    with pytest.raises(ValueError):
        ewma_vol(pd.Series([0.01, 0.02]), lam=0.0)
    with pytest.raises(ValueError):
        ewma_vol(pd.Series([0.01, 0.02]), lam=1.0)


# --- Aggregator ---------------------------------------------------------


def test_compute_technical_features_has_expected_columns() -> None:
    df = _ohlcv(300)
    feats = compute_technical_features(df)
    expected = {
        "open",
        "high",
        "low",
        "close",
        "volume",  # input
        "ret",
        "log_ret",
        "close_sma_20",
        "close_sma_50",
        "close_sma_200",
        "close_ema_12",
        "close_ema_26",
        "rsi_2",
        "rsi_14",
        "ibs",
        "atr_14",
        "vol_21",
    }
    assert expected.issubset(feats.columns)


def test_compute_technical_features_requires_columns() -> None:
    df = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="missing required columns"):
        compute_technical_features(df)


def test_compute_technical_features_preserves_index() -> None:
    df = _ohlcv(100)
    feats = compute_technical_features(df)
    pd.testing.assert_index_equal(feats.index, df.index)
