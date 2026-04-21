"""Shared strategy contract.

All signal strategies emit a **target-weight matrix**: a DataFrame indexed
by date, columns = tradable symbols. Values are target percentages of the
strategy sleeve's NAV. The backtest engine and live portfolio combiner
both speak this type.

Convention:
- Rows with all NaN → no rebalance order that day (hold previous weights).
- Rows with numeric values → rebalance to those target percentages.
- Per-row sum may be <= 1.0 (remainder is cash); per-row sum > 1.0 is
  leverage, allowed only by explicitly-leveraged strategies (none in V1).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class SignalStrategy(Protocol):
    """A strategy that maps price history to target weights."""

    name: str

    def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
        """`closes`: daily close prices (dates x symbols). Returns the
        weight matrix per the module docstring.
        """
        ...
