"""Cross-sectional features across a universe of symbols.

Input shape: a wide DataFrame indexed by date, columns = symbol. Each row
is one observation across the whole universe at time `t`. Output has the
same shape; the value at [t, sym] is `sym`'s feature computed using only
data at time `t` (no look-ahead).

Used by the cross-sectional momentum strategy (PRD §5.2) to rank the
10-ETF universe by 6-month return and hold the top 3.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rank_cross_sectional(frame: pd.DataFrame, *, pct: bool = False) -> pd.DataFrame:
    """Per-row rank. Highest value gets the highest rank.

    `pct=True` returns ranks normalized to [0, 1] — useful as an input
    feature. `pct=False` returns integer ranks starting at 1 — useful for
    top-N selection.
    """
    return frame.rank(axis=1, ascending=True, method="average", pct=pct)


def top_n_mask(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    """Boolean mask marking the top-N symbols per row. Ties broken by rank
    average — a tie on the boundary may let >n symbols through, which we
    accept over the more surprising alternative of picking alphabetically.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    ranks = frame.rank(axis=1, ascending=False, method="min")
    return ranks <= n


def zscore_cross_sectional(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-row z-score: (x - row_mean) / row_std. NaN rows (fewer than 2
    observations) propagate.
    """
    mean = frame.mean(axis=1)
    std = frame.std(axis=1, ddof=1)
    std = std.replace(0.0, np.nan)
    return frame.sub(mean, axis=0).div(std, axis=0)


def demean_cross_sectional(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-row mean subtraction. Preserves variance; often a lighter-weight
    alternative to z-scoring when scale differences across dates are small.
    """
    return frame.sub(frame.mean(axis=1), axis=0)


def universe_momentum(
    prices: pd.DataFrame,
    *,
    lookback_days: int,
    skip_days: int = 0,
) -> pd.DataFrame:
    """Trailing total-return momentum per symbol.

    `lookback_days` is the horizon; `skip_days` drops the most recent N days
    (useful for equity momentum where the last week tends to reverse — not
    needed for ETFs but exposed as a param anyway).

    Example: 6-month momentum with `lookback_days=126`.
    """
    if lookback_days <= 0:
        raise ValueError(f"lookback_days must be positive, got {lookback_days}")
    end = prices.shift(skip_days) if skip_days else prices
    start = prices.shift(skip_days + lookback_days)
    return end / start - 1.0
