"""Tests for EWMAVolForecaster + vol_target_multiplier."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.models import EWMAVolForecaster, forecast_vol_series
from quant.portfolio import vol_target_multiplier

# --- EWMAVolForecaster ------------------------------------------------


def test_rejects_bad_lambda() -> None:
    with pytest.raises(ValueError, match="lam"):
        EWMAVolForecaster(lam=0.0)
    with pytest.raises(ValueError, match="lam"):
        EWMAVolForecaster(lam=1.0)


def test_rejects_bad_periods_per_year() -> None:
    with pytest.raises(ValueError, match="periods_per_year"):
        EWMAVolForecaster(periods_per_year=0)


def test_current_vol_zero_before_updates() -> None:
    fc = EWMAVolForecaster()
    assert fc.current_vol() == 0.0
    assert fc.n_updates == 0


def test_single_update_vol_matches_abs_return() -> None:
    fc = EWMAVolForecaster(lam=0.94)
    fc.update(0.02)
    # First observation: variance = r^2 → vol = |r| * sqrt(252).
    expected = 0.02 * (252**0.5)
    assert fc.current_vol() == pytest.approx(expected)


def test_update_reacts_faster_with_lower_lambda() -> None:
    slow = EWMAVolForecaster(lam=0.99)
    fast = EWMAVolForecaster(lam=0.80)
    for r in [0.005] * 50:
        slow.update(r)
        fast.update(r)
    # Both stable at 0.005^2 annualized (≈ 0.005 * sqrt(252) ≈ 0.0794).
    baseline_slow = slow.current_vol()
    baseline_fast = fast.current_vol()
    assert baseline_slow == pytest.approx(baseline_fast, abs=1e-6)

    # Now inject a single big return. Fast should jump more than slow.
    slow.update(0.05)
    fast.update(0.05)
    assert fast.current_vol() - baseline_fast > slow.current_vol() - baseline_slow


def test_reset_clears_state() -> None:
    fc = EWMAVolForecaster()
    fc.update(0.03)
    fc.reset()
    assert fc.current_vol() == 0.0
    assert fc.n_updates == 0


def test_forecast_vol_series_annualizes() -> None:
    returns = pd.Series([0.01, 0.01, 0.01, 0.01, 0.01])
    vol = forecast_vol_series(returns, lam=0.94)
    # Final value: steady-state vol ≈ 0.01 * sqrt(252) ≈ 0.1587.
    assert vol.iloc[-1] == pytest.approx(0.01 * (252**0.5), rel=1e-6)


def test_forecast_vol_series_tracks_recent_shock() -> None:
    rng = np.random.default_rng(0)
    quiet = list(rng.normal(0, 0.005, 60))
    spike = list(rng.normal(0, 0.05, 20))
    returns = pd.Series(quiet + spike)
    vol = forecast_vol_series(returns, lam=0.94)
    # Quiet-period vol should be lower than post-spike vol.
    assert vol.iloc[50] < vol.iloc[-1]


# --- vol_target_multiplier --------------------------------------------


def test_vol_target_multiplier_basic() -> None:
    forecast = pd.Series([0.10, 0.20, 0.05])
    mult = vol_target_multiplier(forecast, target_vol=0.10, max_gross_exposure=1.0)
    # 0.10/0.10=1.0, 0.10/0.20=0.5, 0.10/0.05=2.0 → clipped to 1.0.
    np.testing.assert_allclose(mult.to_numpy(), [1.0, 0.5, 1.0])


def test_vol_target_multiplier_allows_leverage_when_cap_high() -> None:
    forecast = pd.Series([0.05, 0.10])
    mult = vol_target_multiplier(forecast, target_vol=0.10, max_gross_exposure=2.0)
    np.testing.assert_allclose(mult.to_numpy(), [2.0, 1.0])


def test_vol_target_multiplier_nan_on_zero_or_nan_forecast() -> None:
    forecast = pd.Series([0.10, 0.0, float("nan"), 0.05])
    mult = vol_target_multiplier(forecast, target_vol=0.10)
    assert mult.iloc[0] == pytest.approx(1.0)
    assert mult.iloc[3] == pytest.approx(1.0)  # 0.10/0.05=2.0, clipped at 1.0
    assert np.isnan(mult.iloc[1])
    assert np.isnan(mult.iloc[2])


def test_vol_target_multiplier_rejects_bad_target() -> None:
    with pytest.raises(ValueError, match="target_vol"):
        vol_target_multiplier(pd.Series([0.1]), target_vol=0.0)
    with pytest.raises(ValueError, match="target_vol"):
        vol_target_multiplier(pd.Series([0.1]), target_vol=10.0)


def test_vol_target_multiplier_rejects_bad_cap() -> None:
    with pytest.raises(ValueError, match="max_gross_exposure"):
        vol_target_multiplier(pd.Series([0.1]), target_vol=0.10, max_gross_exposure=0.0)


def test_vol_target_multiplier_empty() -> None:
    out = vol_target_multiplier(pd.Series([], dtype=float), target_vol=0.10)
    assert out.empty


# --- Composition: regime x vol-target -> apply_regime_overlay ---------


def test_composed_multipliers_multiplied_elementwise() -> None:
    """The two overlays compose by element-wise multiplication of
    their multiplier series. Caller multiplies, passes the product to
    `apply_regime_overlay`. This is the pattern the Wave 16 backtest
    uses — asserting it here locks in the contract.
    """
    regime = pd.Series([1.0, 0.5, 1.0, 0.25], index=pd.RangeIndex(4))
    voltgt = pd.Series([0.8, 1.0, 0.5, 1.0], index=pd.RangeIndex(4))
    composed = regime * voltgt
    np.testing.assert_allclose(composed.to_numpy(), [0.8, 0.5, 0.5, 0.25])
    # Both factors in [0, 1] → product stays in [0, 1].
    assert (composed >= 0).all()
    assert (composed <= 1).all()
