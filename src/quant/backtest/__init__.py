"""Backtest engine (custom, daily-bar portfolio), walk-forward, Deflated Sharpe."""

from quant.backtest.engine import (
    BacktestResult,
    align_on_common_dates,
    clip_to_range,
    closes_from_bars,
    run_backtest,
)
from quant.backtest.reports import Tearsheet, compute_tearsheet, monthly_returns_pivot

__all__ = [
    "BacktestResult",
    "Tearsheet",
    "align_on_common_dates",
    "clip_to_range",
    "closes_from_bars",
    "compute_tearsheet",
    "monthly_returns_pivot",
    "run_backtest",
]
