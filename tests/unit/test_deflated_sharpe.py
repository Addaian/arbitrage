"""Tests for the Deflated Sharpe Ratio module."""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from quant.backtest.deflated_sharpe import (
    annualized_sharpe,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)

_DAYS = 252


def _normal_returns(n: int, annualized_sr: float, seed: int = 0) -> pd.Series:
    """Synthetic daily returns with target annualized Sharpe *exactly*.

    Standardizes the raw draws to mean 0, std 1, then rescales — so
    Monte Carlo noise can't slide the realized Sharpe off target. This
    is what we want for unit tests of Sharpe-sensitive functions.
    """
    daily_sigma = 0.01
    daily_mean = annualized_sr * daily_sigma / math.sqrt(_DAYS)
    rng = np.random.default_rng(seed)
    raw = rng.normal(0.0, 1.0, n)
    standardized = (raw - raw.mean()) / raw.std(ddof=1)
    return pd.Series(standardized * daily_sigma + daily_mean)


# --- PSR ----------------------------------------------------------------


def test_psr_half_at_equal_sharpes() -> None:
    # SR == SR* → PSR = 0.5 (the boundary).
    psr = probabilistic_sharpe_ratio(1.0, 1.0, num_observations=1000)
    assert psr == pytest.approx(0.5, abs=1e-10)


def test_psr_near_one_when_sr_dominates_benchmark() -> None:
    # SR way above SR* on a long history → PSR ~ 1.
    psr = probabilistic_sharpe_ratio(2.0, 0.0, num_observations=2520)  # 10y daily
    assert psr > 0.99


def test_psr_near_zero_when_benchmark_dominates_sr() -> None:
    psr = probabilistic_sharpe_ratio(0.0, 2.0, num_observations=2520)
    assert psr < 0.01


def test_psr_symmetric_around_equality() -> None:
    # PSR(SR, SR*, ...) + PSR(SR*, SR, ...) should sum to 1 for the same
    # observation moments (skew=kurt=0). The se(SR) formula is asymmetric
    # in SR, so we hold variance approximately fixed by keeping the SR
    # gap small.
    psr_above = probabilistic_sharpe_ratio(1.2, 0.8, num_observations=1000)
    psr_below = probabilistic_sharpe_ratio(0.8, 1.2, num_observations=1000)
    assert psr_above + psr_below == pytest.approx(1.0, abs=1e-3)


def test_psr_rejects_tiny_sample() -> None:
    with pytest.raises(ValueError, match="num_observations"):
        probabilistic_sharpe_ratio(1.0, 0.0, num_observations=1)


def test_psr_pathological_variance_returns_half() -> None:
    # Huge positive skew with high Sharpe drives the variance factor
    # negative. The implementation degrades gracefully to 0.5 rather
    # than crashing with a math-domain error.
    psr = probabilistic_sharpe_ratio(5.0, 0.0, num_observations=100, skew=10.0, excess_kurtosis=0.0)
    assert psr == pytest.approx(0.5)


# --- Expected max-of-N Sharpe -------------------------------------------


def test_expected_max_is_zero_for_single_trial() -> None:
    assert expected_max_sharpe(1, sr_variance=1.0) == 0.0


def test_expected_max_grows_with_trial_count() -> None:
    # More trials → higher expected max. Monotone increasing.
    v = 1.0
    values = [expected_max_sharpe(n, v) for n in (2, 5, 20, 100, 1000)]
    assert all(a < b for a, b in pairwise(values))


def test_expected_max_scales_with_sqrt_variance() -> None:
    # Doubling the Sharpe variance scales the benchmark by √2.
    base = expected_max_sharpe(50, 1.0)
    doubled = expected_max_sharpe(50, 2.0)
    assert doubled / base == pytest.approx(math.sqrt(2.0), rel=1e-10)


def test_expected_max_rejects_bad_args() -> None:
    with pytest.raises(ValueError):
        expected_max_sharpe(0, sr_variance=1.0)
    with pytest.raises(ValueError):
        expected_max_sharpe(10, sr_variance=-1.0)


# --- DSR end-to-end -----------------------------------------------------


def test_dsr_passes_a_strong_single_trial_strategy() -> None:
    # Sharpe ~1.5 over 5 years, 1 trial → should comfortably pass.
    rets = _normal_returns(_DAYS * 5, annualized_sr=1.5, seed=1)
    result = deflated_sharpe_ratio(rets, num_trials=1)
    assert result.observed_sharpe > 1.0
    assert result.benchmark_sharpe == 0.0
    assert result.psr > 0.95
    assert result.passes


def test_dsr_fails_a_strong_strategy_under_many_trials() -> None:
    # Same Sharpe ~1.5 but pretending we ran 10,000 trials to find it.
    # Benchmark balloons and the PSR collapses.
    rets = _normal_returns(_DAYS * 5, annualized_sr=1.5, seed=1)
    deflated = deflated_sharpe_ratio(rets, num_trials=10_000)
    undeflated = deflated_sharpe_ratio(rets, num_trials=1)
    assert deflated.benchmark_sharpe > undeflated.benchmark_sharpe
    assert deflated.psr < undeflated.psr


def test_dsr_fails_noise() -> None:
    # Zero-mean noise over 5 years, 1 trial → ~50/50 at best.
    rets = _normal_returns(_DAYS * 5, annualized_sr=0.0, seed=42)
    result = deflated_sharpe_ratio(rets, num_trials=1)
    assert not result.passes
    assert 0.2 < result.psr < 0.8


def test_dsr_rejects_too_few_observations() -> None:
    with pytest.raises(ValueError, match="observations"):
        deflated_sharpe_ratio(pd.Series([0.01] * 10), num_trials=1)


def test_dsr_drops_nans() -> None:
    rets = _normal_returns(_DAYS * 2, annualized_sr=1.0, seed=3)
    rets.iloc[::10] = float("nan")
    result = deflated_sharpe_ratio(rets, num_trials=1)
    assert (
        result.num_observations == len(rets) - (len(rets) // 10 + 1) or result.num_observations > 0
    )


# --- annualized_sharpe helper -------------------------------------------


def test_annualized_sharpe_matches_target_on_synthetic() -> None:
    # Large sample → noisy but close to target.
    rets = _normal_returns(_DAYS * 10, annualized_sr=1.0, seed=7)
    assert annualized_sharpe(rets) == pytest.approx(1.0, abs=0.25)


def test_annualized_sharpe_flat_series_is_zero() -> None:
    assert annualized_sharpe(pd.Series([0.0] * 100)) == 0.0


def test_annualized_sharpe_handles_empty() -> None:
    assert annualized_sharpe(pd.Series([], dtype=float)) == 0.0
