"""Cross-sectional momentum (PRD §5.2).

Rule:

1. Once per month, at month end, compute each asset's total return over
   the last `lookback_months` months of monthly closes.
2. Rank assets. Hold the top `top_n`, equal-weighted.
3. `abs_momentum_filter=True` (default false) optionally drops any
   top-N name with non-positive lookback return and parks its slot in
   `cash_symbol` — the Antonacci variant. Off by default to match the
   PRD's plain "rank + hold top N" spec; research in Week 13 can
   revisit whether adding the gate improves DSR.
4. Emit target weights on the first trading day of the following month.

The universe **must** include the cash symbol; the returned weights
matrix is reindexed on `closes.columns`, so any column missing from
`closes` won't appear.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MomentumSignal:
    name: str = "momentum"
    lookback_months: int = 6
    top_n: int = 3
    cash_symbol: str = "SGOV"
    abs_momentum_filter: bool = False

    def __post_init__(self) -> None:
        if self.lookback_months <= 0:
            raise ValueError(f"lookback_months must be positive, got {self.lookback_months}")
        if self.top_n <= 0:
            raise ValueError(f"top_n must be positive, got {self.top_n}")

    def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
        if self.cash_symbol not in closes.columns:
            raise ValueError(
                f"closes is missing the cash symbol {self.cash_symbol!r}; "
                "include it so rebalance remainders can go to cash"
            )

        risk_symbols = [c for c in closes.columns if c != self.cash_symbol]
        if len(risk_symbols) < self.top_n:
            raise ValueError(
                f"need at least {self.top_n} risk symbols alongside the cash symbol, "
                f"got {len(risk_symbols)}"
            )

        monthly_close = closes[risk_symbols].resample("ME").last()
        momentum = monthly_close.pct_change(periods=self.lookback_months, fill_method=None)

        monthly_weights = pd.DataFrame(
            0.0, index=monthly_close.index, columns=closes.columns, dtype=float
        )

        for ts, row in momentum.iterrows():
            if row.isna().all():
                monthly_weights.loc[ts, self.cash_symbol] = 1.0
                continue
            ranked = row.dropna().sort_values(ascending=False)
            if self.abs_momentum_filter:
                selected = ranked[ranked > 0].head(self.top_n)
            else:
                selected = ranked.head(self.top_n)
            if selected.empty:
                monthly_weights.loc[ts, self.cash_symbol] = 1.0
                continue
            per_slot = 1.0 / self.top_n
            for sym in selected.index:
                monthly_weights.loc[ts, sym] = per_slot
            filled = len(selected)
            if filled < self.top_n:
                # Only reachable when abs_momentum_filter trims below top_n.
                monthly_weights.loc[ts, self.cash_symbol] = (self.top_n - filled) * per_slot

        return _apply_on_first_trading_day_of_month(monthly_weights, closes.index)


# --- Helpers ------------------------------------------------------------


def _apply_on_first_trading_day_of_month(
    monthly_weights: pd.DataFrame,
    daily_index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Project monthly rebalance rows onto the daily index — target
    percentages on the first trading day strictly after each signal
    timestamp (month-end), NaN elsewhere.
    """
    out = pd.DataFrame(np.nan, index=daily_index, columns=monthly_weights.columns, dtype=float)

    for signal_ts, row in monthly_weights.iterrows():
        future = daily_index[daily_index > signal_ts]
        if len(future) == 0:
            continue
        trade_day = future[0]
        out.loc[trade_day] = row.values

    return out
