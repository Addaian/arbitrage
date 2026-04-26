"""Stationary block bootstrap for backtest robustness checks.

The historical Sharpe / max-DD numbers in `docs/research/week13_validation.md`
are point estimates on **one** realized 20-year tape. They tell us nothing
about how robust those numbers are to the specific path the market took.
Two questions this module answers:

1. **Is the historical Sharpe a fluke or robust?** Resample the daily
   return tape with replacement (block-bootstrap to preserve serial
   dependence), run the strategy through each alternate history, and
   look at the distribution of out-of-sample-equivalent metrics.
2. **Where does the realized run sit in that distribution?** If the
   point estimate is at the 99th percentile of the bootstrap distribution,
   that's a red flag that we got lucky.

We use the Politis-Romano (1994) **stationary bootstrap** with
geometric block lengths. Block bootstrap matters for trend / momentum
strategies that depend on autocorrelation; an i.i.d. resample destroys
that structure and would systematically understate trend Sharpe.

This is a robustness test, not a validation of edge — bootstrap can't
tell you the strategies will work in a future regime that doesn't
resemble the past 20 years. No synthetic test can.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.backtest.deflated_sharpe import annualized_sharpe
from quant.backtest.engine import run_backtest


@dataclass
class BootstrapMetric:
    """One bootstrap path's headline metrics."""

    sharpe: float
    max_drawdown: float
    cagr: float
    num_rebalances: int


def stationary_bootstrap_indices(
    n: int,
    *,
    expected_block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return an integer array of length `n` of bootstrap-sampled
    indices into [0, n). Geometric block lengths, mean = expected_block_size.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if expected_block_size <= 0:
        raise ValueError(f"expected_block_size must be positive, got {expected_block_size}")
    p = 1.0 / expected_block_size
    out = np.empty(n, dtype=np.int64)
    restart_draws = rng.uniform(size=n) < p
    out[0] = rng.integers(0, n)
    next_starts = rng.integers(0, n, size=n)
    for i in range(1, n):
        if restart_draws[i]:
            out[i] = next_starts[i]
        else:
            out[i] = (out[i - 1] + 1) % n
    return out


def bootstrap_returns(
    returns: pd.DataFrame,
    *,
    n_paths: int,
    expected_block_size: int = 10,
    seed: int = 42,
) -> Iterator[pd.DataFrame]:
    """Yield `n_paths` bootstrapped return frames. Each yielded frame
    has the same shape and DatetimeIndex as `returns` but is a stationary
    block resample of the rows (preserving cross-sectional correlations).
    """
    if returns.empty:
        raise ValueError("returns is empty")
    rng = np.random.default_rng(seed)
    arr = returns.to_numpy()
    n = len(returns)
    for _ in range(n_paths):
        idx = stationary_bootstrap_indices(n, expected_block_size=expected_block_size, rng=rng)
        yield pd.DataFrame(arr[idx], index=returns.index, columns=returns.columns)


def reconstruct_prices(returns: pd.DataFrame, *, initial: float = 100.0) -> pd.DataFrame:
    """Forward-cumprod returns to a price level. The first row is `initial`."""
    if returns.empty:
        raise ValueError("returns is empty")
    return initial * (1.0 + returns).cumprod()


WeightsFn = Callable[[pd.DataFrame], pd.DataFrame]


def bootstrap_backtest(
    closes: pd.DataFrame,
    weights_fn: WeightsFn,
    *,
    n_paths: int = 200,
    expected_block_size: int = 10,
    seed: int = 42,
    fees: float = 0.0005,
    slippage: float = 0.0005,
) -> pd.DataFrame:
    """For each of `n_paths` bootstrap paths: resample returns, rebuild
    prices, recompute strategy weights, run the backtest, capture metrics.

    Returns a DataFrame with one row per path, columns
    `[sharpe, max_drawdown, cagr, num_rebalances]`.
    """
    returns = closes.pct_change().dropna(how="all")
    rows: list[BootstrapMetric] = []
    for path_returns in bootstrap_returns(
        returns,
        n_paths=n_paths,
        expected_block_size=expected_block_size,
        seed=seed,
    ):
        path_closes = reconstruct_prices(path_returns)
        path_weights = weights_fn(path_closes)
        if path_weights.dropna(how="all").empty:
            rows.append(BootstrapMetric(0.0, 0.0, 0.0, 0))
            continue
        result = run_backtest(path_closes, path_weights, fees=fees, slippage=slippage)
        rows.append(_metrics_from_result(result))
    return pd.DataFrame([m.__dict__ for m in rows])


def _metrics_from_result(result: object) -> BootstrapMetric:
    """Compute the four headline metrics directly from a `BacktestResult`,
    avoiding a `Tearsheet` round-trip (`compute_tearsheet` adds rolling
    Sharpe + monthly pivots we don't need here)."""
    equity = result.equity
    returns = result.returns
    trades = result.trades

    sharpe = float(annualized_sharpe(returns))
    running_max = equity.cummax()
    drawdown = (equity / running_max - 1.0).min()
    max_dd = float(drawdown) if not pd.isna(drawdown) else 0.0
    n_years = max(len(equity) / 252.0, 1.0 / 252.0)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1.0)
    num_rebalances = len(trades)
    return BootstrapMetric(
        sharpe=sharpe,
        max_drawdown=max_dd,
        cagr=cagr,
        num_rebalances=num_rebalances,
    )
