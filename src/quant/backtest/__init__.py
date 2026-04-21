"""Backtest engine (custom, daily-bar portfolio), walk-forward, Deflated Sharpe."""

from quant.backtest.deflated_sharpe import (
    DeflatedSharpeResult,
    annualized_sharpe,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)
from quant.backtest.engine import (
    BacktestResult,
    align_on_common_dates,
    clip_to_range,
    closes_from_bars,
    run_backtest,
)
from quant.backtest.reports import Tearsheet, compute_tearsheet, monthly_returns_pivot
from quant.backtest.trial_log import JsonlTrialLog, TrialLog, TrialRecord
from quant.backtest.walk_forward import (
    StrategyFactory,
    WalkForwardFold,
    WalkForwardResult,
    fixed_params,
    tuned_by_train_sharpe,
    walk_forward,
)

__all__ = [
    "BacktestResult",
    "DeflatedSharpeResult",
    "JsonlTrialLog",
    "StrategyFactory",
    "Tearsheet",
    "TrialLog",
    "TrialRecord",
    "WalkForwardFold",
    "WalkForwardResult",
    "align_on_common_dates",
    "annualized_sharpe",
    "clip_to_range",
    "closes_from_bars",
    "compute_tearsheet",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "fixed_params",
    "monthly_returns_pivot",
    "probabilistic_sharpe_ratio",
    "run_backtest",
    "tuned_by_train_sharpe",
    "walk_forward",
]
