"""Short-term mean reversion (PRD §5.3).

Rule:

* **Entry:** at the close of day D, if `IBS(D) < ibs_entry` **AND**
  `RSI-2(D) < rsi2_entry`, open (or hold) a position in that symbol.
* **Exit:** at the close of day D, if `IBS(D) > ibs_exit`, close the
  position.
* Default thresholds per PRD §5.3: `ibs_entry=0.2`, `ibs_exit=0.7`,
  `rsi2_entry=10`. Evaluation order per day: exit first, then entry
  (so a same-day exit-then-reenter is possible on a big intraday
  recovery followed by a down-close — rare but correct).
* **Position size:** equal-weight. Each simultaneous holding is
  `1 / max_positions` of the sleeve; unallocated slots stay in the
  cash symbol. The weights row always sums to 1.0 across all columns.

Unlike `TrendSignal` / `MomentumSignal` (monthly), this strategy
**rebalances daily**. Weight rows are emitted only on days where any
per-symbol state changes — forward-fill between changes gives the
backtest engine a minimal set of rebalance events to cost.

Interface note: the signal needs OHLC — specifically the daily
high/low for IBS — which `TrendSignal` / `MomentumSignal` did not. We
accept the three frames (closes, highs, lows) as positional args to
`target_weights`; callers pass them aligned on the same index +
columns. The combiner (`quant.portfolio.combiner.combine_weights`) only
sees the output weights frame, so this richer input doesn't cascade.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.features.technical import ibs as _ibs
from quant.features.technical import rsi as _rsi


@dataclass
class MeanReversionSignal:
    name: str = "mean_reversion"
    ibs_entry: float = 0.2
    ibs_exit: float = 0.7
    rsi2_entry: float = 10.0
    rsi_period: int = 2
    max_positions: int = 5
    cash_symbol: str = "SGOV"

    def __post_init__(self) -> None:
        if not 0.0 < self.ibs_entry < self.ibs_exit < 1.0:
            raise ValueError(
                f"thresholds must satisfy 0 < ibs_entry ({self.ibs_entry}) "
                f"< ibs_exit ({self.ibs_exit}) < 1"
            )
        if self.rsi_period <= 1:
            raise ValueError(f"rsi_period must be > 1, got {self.rsi_period}")
        if self.max_positions <= 0:
            raise ValueError(f"max_positions must be positive, got {self.max_positions}")
        if not 0.0 < self.rsi2_entry <= 100.0:
            raise ValueError(f"rsi2_entry must be in (0, 100], got {self.rsi2_entry}")

    def target_weights(
        self,
        closes: pd.DataFrame,
        highs: pd.DataFrame,
        lows: pd.DataFrame,
    ) -> pd.DataFrame:
        if self.cash_symbol not in closes.columns:
            raise ValueError(
                f"closes is missing the cash symbol {self.cash_symbol!r}; "
                "include it so rebalance remainders can go to cash"
            )
        risk_symbols = [c for c in closes.columns if c != self.cash_symbol]
        if not risk_symbols:
            raise ValueError("need at least one risk symbol alongside the cash symbol")

        if not closes.index.equals(highs.index) or not closes.index.equals(lows.index):
            raise ValueError("closes, highs, and lows must share the same index")
        missing_high = set(risk_symbols) - set(highs.columns)
        missing_low = set(risk_symbols) - set(lows.columns)
        if missing_high or missing_low:
            raise ValueError(
                f"highs/lows missing risk symbols: "
                f"high={sorted(missing_high)} low={sorted(missing_low)}"
            )

        # Per-symbol indicators.
        ibs_frame = pd.concat(
            {sym: _ibs(highs[sym], lows[sym], closes[sym]) for sym in risk_symbols},
            axis=1,
        )
        rsi_frame = pd.concat(
            {sym: _rsi(closes[sym], window=self.rsi_period) for sym in risk_symbols},
            axis=1,
        )

        entry_mask = (ibs_frame < self.ibs_entry) & (rsi_frame < self.rsi2_entry)
        exit_mask = ibs_frame > self.ibs_exit

        # Stateful walk: for each symbol, toggle a binary "in position" flag.
        in_position = _walk_state(entry_mask, exit_mask)

        # Emit a weight row only when the position vector changes from
        # the previous day. If no signals ever fire, the output frame is
        # entirely NaN and the downstream backtest will reject it — which
        # is the right semantics for "this strategy never traded".
        per_slot = 1.0 / self.max_positions
        weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns, dtype=float)

        prev_positions: dict[str, bool] = dict.fromkeys(risk_symbols, False)

        for ts in closes.index:
            cur = {sym: bool(in_position.loc[ts, sym]) for sym in risk_symbols}
            if cur == prev_positions:
                continue
            active = sum(1 for s in risk_symbols if cur[s])
            row: dict[str, float] = {}
            for sym in risk_symbols:
                row[sym] = per_slot if cur[sym] else 0.0
            row[self.cash_symbol] = 1.0 - active * per_slot
            for col, val in row.items():
                weights.loc[ts, col] = val
            prev_positions = cur

        return weights


# --- Internals ----------------------------------------------------------


def _walk_state(entry_mask: pd.DataFrame, exit_mask: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol binary state: exit first on each day, then entry.

    Pure function for testability. `entry_mask` and `exit_mask` share an
    index and columns (one per risk symbol).
    """
    state = pd.DataFrame(False, index=entry_mask.index, columns=entry_mask.columns)
    current: dict[str, bool] = dict.fromkeys(entry_mask.columns, False)
    entry_values = entry_mask.to_numpy(dtype=bool, na_value=False)
    exit_values = exit_mask.to_numpy(dtype=bool, na_value=False)
    symbols = list(entry_mask.columns)
    state_values = np.zeros_like(entry_values)
    for i in range(entry_mask.shape[0]):
        for j, sym in enumerate(symbols):
            if current[sym] and exit_values[i, j]:
                current[sym] = False
            if not current[sym] and entry_values[i, j]:
                current[sym] = True
            state_values[i, j] = current[sym]
    state.iloc[:, :] = state_values
    return state
