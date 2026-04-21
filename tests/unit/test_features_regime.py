"""Unit tests for regime features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.regime import (
    compute_regime_features,
    term_structure_ratio,
    vix_log_level,
    vix_percentile,
)


def test_vix_log_level_matches_np_log() -> None:
    vix = pd.Series([10.0, 20.0, 40.0])
    out = vix_log_level(vix)
    np.testing.assert_allclose(out.values, np.log([10.0, 20.0, 40.0]))


def test_vix_log_level_zero_becomes_nan() -> None:
    vix = pd.Series([10.0, 0.0, 20.0])
    out = vix_log_level(vix)
    assert np.isnan(out.iloc[1])


def test_vix_percentile_shape_and_range() -> None:
    rng = np.random.default_rng(0)
    vix = pd.Series(rng.uniform(10, 40, size=500))
    pct = vix_percentile(vix, window=252)
    clean = pct.dropna()
    assert (clean >= 0).all()
    assert (clean <= 1).all()
    assert len(clean) == len(vix) - 251  # warmup = window-1


def test_vix_percentile_last_value_is_rank() -> None:
    # Monotonically rising series: current value is always the max of its
    # window → percentile is 1.0 after warmup.
    vix = pd.Series(np.arange(1.0, 101.0))
    pct = vix_percentile(vix, window=30)
    # Once warmed up, each value is the new max → pct = 1.0 (top of window).
    assert pct.dropna().iloc[-1] == pytest.approx(1.0)


def test_vix_percentile_rejects_zero_window() -> None:
    with pytest.raises(ValueError):
        vix_percentile(pd.Series([1.0]), window=0)


def test_term_structure_ratio_above_one_signals_stress() -> None:
    short = pd.Series([30.0])  # VIX9D elevated
    long = pd.Series([20.0])  # VIX lower
    r = term_structure_ratio(short, long)
    assert r.iloc[0] > 1.0


def test_term_structure_ratio_contango() -> None:
    short = pd.Series([15.0])
    long = pd.Series([20.0])
    r = term_structure_ratio(short, long)
    assert r.iloc[0] < 1.0


def test_term_structure_zero_long_becomes_nan() -> None:
    short = pd.Series([20.0])
    long = pd.Series([0.0])
    r = term_structure_ratio(short, long)
    assert np.isnan(r.iloc[0])


def test_compute_regime_features_without_short_vix() -> None:
    vix = pd.Series([20.0] * 300, index=pd.date_range("2026-01-01", periods=300))
    out = compute_regime_features(vix)
    assert set(out.columns) == {"vix", "vix_log", "vix_pct"}


def test_compute_regime_features_with_short_vix() -> None:
    idx = pd.date_range("2026-01-01", periods=300)
    vix = pd.Series(np.full(300, 20.0), index=idx)
    vix9d = pd.Series(np.full(300, 22.0), index=idx)
    out = compute_regime_features(vix, vix_short=vix9d)
    assert "vix_ts_ratio" in out.columns
    assert out["vix_ts_ratio"].iloc[-1] == pytest.approx(22.0 / 20.0)
