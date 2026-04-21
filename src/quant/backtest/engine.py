"""Minimal portfolio backtest engine for daily-bar, multi-asset strategies.

Input
-----
* `closes`  — daily close prices, wide DataFrame (dates x symbols, float64)
* `weights` — target weight matrix, same shape. NaN on non-rebalance days,
  numeric target percentages on rebalance days. (See
  `quant.signals.base.SignalStrategy` contract.)

Output
------
A `BacktestResult` dataclass holding the daily equity curve, portfolio
returns, realized trade log, and raw per-asset positions. The tearsheet
functions in `reports.py` consume this object.

Why not vectorbt for this path
------------------------------
vectorbt's `TargetPercent` order type + `cash_sharing=True` works for
multi-asset rebalancing, but the execution-order quirks (see the official
warning on `SizeType.Percent` with `call_seq='auto'`) make it hard to
reproduce Faber's monthly rebalance precisely without pinning every
knob. A flat dot-product of lagged weights x asset returns matches the
published methodology exactly, runs fast, and is trivial to test.

Assumptions & simplifications (V1)
---------------------------------
* Fractional shares allowed (Alpaca supports them; matches live path).
* Execution at the rebalance-day *close* (standard for daily-bar
  backtests — for live we'll move to next-day open or MOC).
* Commission modeled as `fees` x turnover notional on rebalance days.
  Slippage modeled as an additional per-leg spread cost.
* No corporate actions beyond whatever is already baked into the loader
  (yfinance / Alpaca both return adjusted prices — PRD §3.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from quant.types import Bar


@dataclass
class BacktestResult:
    equity: pd.Series  # daily total NAV
    returns: pd.Series  # daily portfolio return
    weights: pd.DataFrame  # applied weights per day (forward-filled)
    trades: pd.DataFrame  # rebalance rows: new weights + turnover
    initial_cash: float
    fees: float
    slippage: float
    metadata: dict[str, object] = field(default_factory=dict)


def run_backtest(
    closes: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    initial_cash: float = 100_000.0,
    fees: float = 0.0005,  # 5 bps commission
    slippage: float = 0.0005,  # 5 bps slippage
) -> BacktestResult:
    """Run a daily multi-asset backtest on a target-weight matrix.

    `weights` rows that are all-NaN mean "hold previous weights". The
    first non-NaN row establishes the initial allocation.
    """
    if closes.empty:
        raise ValueError("closes is empty")
    if not closes.index.is_monotonic_increasing:
        raise ValueError("closes index must be sorted ascending")
    if not closes.columns.equals(weights.columns):
        raise ValueError(
            "closes.columns must equal weights.columns "
            f"(closes={list(closes.columns)}, weights={list(weights.columns)})"
        )

    rebalance_dates = weights.dropna(how="all").index
    if len(rebalance_dates) == 0:
        raise ValueError("weights has no rebalance rows (all NaN)")

    # Forward-fill weights through each day between rebalances.
    applied = weights.ffill().fillna(0.0)

    # Asset returns per day. Missing prices → 0 return that day (holiday/IPO).
    asset_returns = closes.pct_change().fillna(0.0)

    # Effective portfolio exposure uses yesterday's weights (no look-ahead).
    # On the first rebalance day we enter at close — so that day earns no
    # return (the shift of `applied` is NaN there; we coerce to zero).
    effective = applied.shift(1).fillna(0.0)
    gross_return = (effective * asset_returns).sum(axis=1)

    # Turnover = sum of |Δ weight| on rebalance dates (one-sided).
    trade_rows = weights.dropna(how="all")
    prior = applied.shift(1).reindex(trade_rows.index).fillna(0.0)
    delta = (trade_rows - prior).abs()
    turnover = delta.sum(axis=1)
    cost_per_rebalance = turnover * (fees + slippage)

    cost_series = pd.Series(0.0, index=closes.index)
    cost_series.loc[trade_rows.index] = cost_per_rebalance.values

    net_return = gross_return - cost_series
    equity = (1.0 + net_return).cumprod() * initial_cash
    equity.iloc[0] = initial_cash if np.isnan(equity.iloc[0]) else equity.iloc[0]

    trades = trade_rows.copy()
    trades["turnover"] = turnover
    trades["cost"] = cost_per_rebalance

    return BacktestResult(
        equity=equity,
        returns=net_return,
        weights=applied,
        trades=trades,
        initial_cash=initial_cash,
        fees=fees,
        slippage=slippage,
    )


# --- Helpers ------------------------------------------------------------


def closes_from_bars(bars_by_symbol: dict[str, list[Bar]]) -> pd.DataFrame:
    """Assemble a wide close-price DataFrame from per-symbol bar lists.

    Missing dates (one symbol trades on a day another does not) are
    forward-filled within each column so downstream strategies see a
    continuous curve.
    """
    if not bars_by_symbol:
        raise ValueError("bars_by_symbol is empty")

    series_by_symbol: dict[str, pd.Series] = {}
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        ts = [pd.Timestamp(b.ts) for b in bars]
        closes = [float(b.close) for b in bars]
        series_by_symbol[symbol] = pd.Series(closes, index=ts, name=symbol)

    if not series_by_symbol:
        raise ValueError("no bars contained close prices")

    frame = pd.concat(series_by_symbol.values(), axis=1).sort_index()
    return frame.ffill().dropna(how="all")


def align_on_common_dates(closes: pd.DataFrame, *, min_periods: int = 1) -> pd.DataFrame:
    """Drop dates with fewer than `min_periods` observed (non-NaN) symbols.

    Use on assembled close frames to strip pre-IPO windows where a newer
    ETF hasn't started trading yet.
    """
    observed = closes.notna().sum(axis=1)
    return closes.loc[observed >= min_periods]


def clip_to_range(
    closes: pd.DataFrame,
    *,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Inclusive date range filter. Used by the CLI."""
    out = closes
    if start is not None:
        out = out.loc[out.index >= pd.Timestamp(start)]
    if end is not None:
        out = out.loc[out.index <= pd.Timestamp(end)]
    return out
