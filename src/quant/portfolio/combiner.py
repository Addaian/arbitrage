"""Combine per-strategy sleeve weights into a portfolio-level target.

Each strategy returns a **sleeve** target-weight matrix — weights sum
to 1.0 on rebalance days, NaN on hold days. The portfolio combiner
scales each sleeve by its `allocation` (what fraction of total NAV
that strategy controls) and sums across strategies.

Contract:

* Input: `{strategy_name: weights_frame}` and `{strategy_name: allocation}`.
  Allocations for enabled strategies must sum to 1.0; any leftover is
  held in the reserved `cash_symbol` passed by the caller.
* Output: a portfolio weights frame (dates x all symbols), with rows
  that are **all-NaN** only when *every* strategy abstains that day —
  otherwise abstaining strategies contribute zero on a rebalance row.

We forward-fill per-strategy sleeves before combining: a strategy that
rebalances monthly still holds its last target on weeks 2-4 of the
month, which is what the portfolio level needs to reason about.
"""

from __future__ import annotations

import math

import pandas as pd


def combine_weights(
    strategy_weights: dict[str, pd.DataFrame],
    allocations: dict[str, float],
    *,
    tolerance: float = 1e-6,
) -> pd.DataFrame:
    """Scale each strategy's sleeve by its allocation and sum them.

    Raises if (a) allocations don't sum to ~1.0, (b) there are strategies
    without an allocation (or vice versa), (c) any sleeve frame is empty.
    """
    if not strategy_weights:
        raise ValueError("strategy_weights is empty")
    if set(strategy_weights) != set(allocations):
        raise ValueError(
            f"strategy_weights keys {sorted(strategy_weights)} must equal "
            f"allocations keys {sorted(allocations)}"
        )
    total = sum(allocations.values())
    if not math.isclose(total, 1.0, abs_tol=tolerance):
        raise ValueError(f"allocations must sum to ~1.0 (got {total:.6f})")

    # Union of columns + union of dates across all strategies.
    all_columns: list[str] = []
    seen: set[str] = set()
    for frame in strategy_weights.values():
        for col in frame.columns:
            if col not in seen:
                seen.add(col)
                all_columns.append(col)
    all_index: pd.DatetimeIndex = pd.DatetimeIndex([])
    for frame in strategy_weights.values():
        if frame.empty:
            raise ValueError("one of the strategy weight frames is empty")
        all_index = all_index.union(frame.index)

    combined = pd.DataFrame(0.0, index=all_index, columns=all_columns, dtype=float)
    # Track per-date whether ANY strategy had a rebalance (non-NaN row).
    # If none did, we'll return NaN for that date so the portfolio
    # inherits the "no rebalance" semantics.
    rebalance_hits = pd.Series(False, index=all_index)

    for name, frame in strategy_weights.items():
        alloc = allocations[name]
        reindexed = frame.reindex(index=all_index, columns=all_columns)
        # Per-date: was there a signal today on this strategy?
        row_mask = reindexed.notna().any(axis=1)
        rebalance_hits = rebalance_hits | row_mask
        filled = reindexed.ffill().fillna(0.0)
        combined = combined + filled * alloc

    # Rows where NO strategy had a signal ever-prior: NaN (no rebalance yet).
    # Once the first rebalance fires, carry-forward semantics kick in — so
    # the only truly "unknown" rows are the pre-first-signal prefix.
    cumulative_hit = rebalance_hits.cummax()
    combined.loc[~cumulative_hit] = float("nan")

    # Rebalance rows: we keep them numeric (no NaN). Non-rebalance rows
    # after first hit are still numeric carry-forward — this is how the
    # backtest engine treats "hold previous weights" after ffill.
    # For signalling "no new rebalance today" downstream, we expose a
    # separate boolean series via `rebalance_dates` below.
    return combined


def rebalance_dates(strategy_weights: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Union of the rebalance dates across all strategies.

    Useful when the caller wants to know "on which days did *anything*
    rebalance" — the backtest engine can then charge costs only on
    those dates.
    """
    indices: list[pd.DatetimeIndex] = []
    for frame in strategy_weights.values():
        rebalance = frame.dropna(how="all").index
        if not rebalance.empty:
            indices.append(pd.DatetimeIndex(rebalance))
    if not indices:
        return pd.DatetimeIndex([])
    union = indices[0]
    for idx in indices[1:]:
        union = union.union(idx)
    return pd.DatetimeIndex(sorted(set(union)))
