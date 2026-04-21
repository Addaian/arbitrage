"""Faber-style absolute-momentum trend following (PRD §5.1).

Rule, per Mebane Faber's 2007 paper and Antonacci's GEM variant:

1. Once per month, at month end, compute a 10-month SMA of each risk asset.
2. If last month's close > SMA → hold the asset next month.
3. Otherwise → hold cash (SGOV or equivalent).
4. Active assets at rebalance are equal-weighted over the full risk
   universe; any asset that isn't "on" leaves its slice in cash. So if 2
   of 3 risk assets are on, each active one gets 1/3, cash holds 1/3.

Target weights are emitted on the first trading day of each month at
that day's close. All other days are NaN (hold previous allocation).

Produces a weight matrix over **all symbols in the input `closes` frame**,
including the cash symbol. Columns missing from the input are treated
as if never tradable — callers must supply the cash symbol in `closes`
(typically SGOV).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TrendSignal:
    name: str = "trend"
    lookback_months: int = 10
    cash_symbol: str = "SGOV"

    def __post_init__(self) -> None:
        if self.lookback_months <= 0:
            raise ValueError(f"lookback_months must be positive, got {self.lookback_months}")

    def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
        """Compute daily target-weight matrix.

        `closes` must be daily, with a DatetimeIndex and the cash symbol
        included as a column.
        """
        if self.cash_symbol not in closes.columns:
            raise ValueError(
                f"closes is missing the cash symbol {self.cash_symbol!r}; "
                "include it so rebalance remainders can go to cash"
            )

        risk_symbols = [c for c in closes.columns if c != self.cash_symbol]
        if not risk_symbols:
            raise ValueError("need at least one risk symbol alongside the cash symbol")

        monthly_close = closes[risk_symbols].resample("ME").last()
        sma = monthly_close.rolling(
            window=self.lookback_months, min_periods=self.lookback_months
        ).mean()

        # Active on month m's close → hold during month m+1.
        active = (monthly_close > sma).astype(float)
        # Rows inside the warmup window are NaN in `sma` — propagate so
        # early months produce no signal at all (all-cash fallback below).
        active = active.where(~sma.isna())

        # Equal-weight across the full risk universe (not just active).
        # This matches Faber: each sleeve either earns its asset or earns
        # cash in its slot.
        per_slot = 1.0 / len(risk_symbols)
        risk_weights = active * per_slot
        cash_weights = 1.0 - risk_weights.sum(axis=1, min_count=1)

        monthly_weights = risk_weights.copy()
        monthly_weights[self.cash_symbol] = cash_weights

        # Before warmup is complete, go 100% cash.
        warmup_rows = sma.isna().all(axis=1)
        monthly_weights.loc[warmup_rows, risk_symbols] = 0.0
        monthly_weights.loc[warmup_rows, self.cash_symbol] = 1.0

        return _apply_on_first_trading_day_of_month(monthly_weights, closes.index)


# --- Helpers ------------------------------------------------------------


def _apply_on_first_trading_day_of_month(
    monthly_weights: pd.DataFrame,
    daily_index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Produce a daily weights frame: target percentages on the first
    trading day that occurs after each row of `monthly_weights`, NaN
    elsewhere. Indexed on `daily_index`.

    Rationale: signal is computed using data through the last trading
    day of month m. Trade on the first trading day of month m+1 — no
    look-ahead.
    """
    out = pd.DataFrame(np.nan, index=daily_index, columns=monthly_weights.columns, dtype=float)

    for signal_ts, row in monthly_weights.iterrows():
        if row.isna().all():
            continue
        # First trading day strictly after the signal timestamp (month-end).
        future = daily_index[daily_index > signal_ts]
        if len(future) == 0:
            continue
        trade_day = future[0]
        out.loc[trade_day] = row.values

    return out
