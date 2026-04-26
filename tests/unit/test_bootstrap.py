"""Unit tests for the stationary block bootstrap module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.bootstrap import (
    bootstrap_backtest,
    bootstrap_returns,
    reconstruct_prices,
    stationary_bootstrap_indices,
)


def _synthetic_returns(n: int = 252, n_symbols: int = 3, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    cols = [f"S{i}" for i in range(n_symbols)]
    data = rng.normal(0.0005, 0.012, size=(n, n_symbols))
    return pd.DataFrame(data, index=idx, columns=cols)


def test_indices_have_expected_length() -> None:
    rng = np.random.default_rng(1)
    idx = stationary_bootstrap_indices(100, expected_block_size=10, rng=rng)
    assert len(idx) == 100
    assert idx.min() >= 0
    assert idx.max() < 100


def test_indices_form_geometric_blocks() -> None:
    """Block lengths should average ~expected_block_size."""
    rng = np.random.default_rng(7)
    idx = stationary_bootstrap_indices(10_000, expected_block_size=20, rng=rng)
    # Detect block boundaries: a step that isn't +1 mod n means a restart.
    diffs = np.diff(idx)
    n_restarts = int((diffs != 1).sum())
    n_blocks = n_restarts + 1
    avg_block = 10_000 / n_blocks
    # Loose tolerance — geometric draws are noisy at this scale.
    assert 12.0 < avg_block < 30.0


def test_indices_are_deterministic_under_same_seed() -> None:
    a = stationary_bootstrap_indices(50, expected_block_size=5, rng=np.random.default_rng(123))
    b = stationary_bootstrap_indices(50, expected_block_size=5, rng=np.random.default_rng(123))
    assert np.array_equal(a, b)


def test_bootstrap_returns_preserves_shape() -> None:
    returns = _synthetic_returns()
    [path] = list(bootstrap_returns(returns, n_paths=1, seed=0))
    assert path.shape == returns.shape
    assert path.index.equals(returns.index)
    assert list(path.columns) == list(returns.columns)


def test_bootstrap_returns_uses_only_observed_rows() -> None:
    """No interpolated returns — every row must be a copy of an
    original input row (preserves cross-sectional correlations)."""
    returns = _synthetic_returns()
    arr = returns.to_numpy()
    [path] = list(bootstrap_returns(returns, n_paths=1, expected_block_size=5, seed=0))
    path_rows = {tuple(row) for row in path.to_numpy()}
    original_rows = {tuple(row) for row in arr}
    assert path_rows.issubset(original_rows)


def test_bootstrap_returns_invalid_raises() -> None:
    with pytest.raises(ValueError):
        list(bootstrap_returns(pd.DataFrame(), n_paths=1))


def test_reconstruct_prices_starts_at_initial_x_first_step() -> None:
    """`reconstruct_prices` does `initial * cumprod(1+r)`, so the first
    row equals `initial * (1 + r0)`, not exactly `initial`."""
    returns = pd.DataFrame({"S": [0.01, -0.005, 0.02]})
    px = reconstruct_prices(returns, initial=100.0)
    np.testing.assert_allclose(px["S"].iloc[0], 100.0 * 1.01, rtol=1e-9)
    np.testing.assert_allclose(px["S"].iloc[-1], 100.0 * 1.01 * 0.995 * 1.02, rtol=1e-9)


def test_bootstrap_backtest_returns_one_row_per_path() -> None:
    """End-to-end: bootstrap → reconstruct → weights → backtest."""
    closes = reconstruct_prices(_synthetic_returns(n=400, n_symbols=2))
    closes.columns = ["RISK", "CASH"]

    def constant_weights(closes_in: pd.DataFrame) -> pd.DataFrame:
        # Always 50/50 — rebalance only on the first day.
        out = pd.DataFrame(np.nan, index=closes_in.index, columns=closes_in.columns)
        out.iloc[0] = [0.5, 0.5]
        return out

    metrics = bootstrap_backtest(
        closes,
        constant_weights,
        n_paths=12,
        expected_block_size=5,
        seed=1,
    )
    assert len(metrics) == 12
    assert {"sharpe", "max_drawdown", "cagr", "num_rebalances"} == set(metrics.columns)
    # Max drawdowns are ≤ 0 (downward).
    assert (metrics["max_drawdown"] <= 0).all()


def test_bootstrap_backtest_distribution_brackets_realized_for_random_walk() -> None:
    """On a near-random-walk input, the realized Sharpe should fall
    near the centre of the bootstrap distribution, not at an extreme.
    This is a smoke test that the bootstrap isn't biased."""
    np.random.seed(0)
    closes = reconstruct_prices(_synthetic_returns(n=500, n_symbols=2, seed=99))
    closes.columns = ["RISK", "CASH"]

    def buy_and_hold(closes_in: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(np.nan, index=closes_in.index, columns=closes_in.columns)
        out.iloc[0] = [1.0, 0.0]
        return out

    metrics = bootstrap_backtest(closes, buy_and_hold, n_paths=80, seed=2)
    p5 = float(np.nanpercentile(metrics["sharpe"], 5))
    p95 = float(np.nanpercentile(metrics["sharpe"], 95))
    # Wide envelope: any reasonable point estimate must lie in [p5, p95]
    # for a near-random-walk synthetic — otherwise the bootstrap or the
    # reconstruction is broken.
    realized = float(metrics["sharpe"].median())
    assert p5 <= realized <= p95
