"""Walk-forward validation harness (PRD §5.5, implementation plan Week 6).

A `SignalStrategy` is evaluated on a series of out-of-sample windows.
Each fold gives the strategy an in-sample slice (for parameter
selection, if it cares) and then grades it on the contiguous
out-of-sample slice that follows. Returns, equity, and Sharpe are
reported per fold and concatenated across folds.

Two window schemes:

* **rolling** (default) — fixed train length (e.g. 10y), rolled forward
  by `step_years` each fold. Catches regime drift: a strategy tuned on
  2005-2015 must still work on 2015-2017.
* **expanding** — train window grows, all prior data used. Useful when
  longer history is strictly more information.

The WF runner delegates the actual backtest to
`quant.backtest.engine.run_backtest`, so cost / slippage assumptions
stay identical to single-run backtests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.backtest.deflated_sharpe import annualized_sharpe
from quant.backtest.engine import BacktestResult, run_backtest
from quant.signals.base import SignalStrategy

# Strategy factory: receives the in-sample (training) closes and returns
# a ready-to-use SignalStrategy. For fixed-param strategies (like
# Faber's 10-month SMA) the train window is ignored.
StrategyFactory = Callable[[pd.DataFrame], SignalStrategy]


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    strategy_name: str
    oos_sharpe: float
    oos_cagr: float
    oos_max_drawdown: float
    result: BacktestResult


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[WalkForwardFold]
    oos_returns: pd.Series  # concatenated OOS daily returns across folds
    oos_sharpe: float  # annualized, computed on the concatenation
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def num_folds(self) -> int:
        return len(self.folds)

    @property
    def fold_sharpes(self) -> list[float]:
        return [f.oos_sharpe for f in self.folds]


def walk_forward(
    closes: pd.DataFrame,
    strategy_factory: StrategyFactory,
    *,
    train_years: int = 10,
    test_years: int = 2,
    step_years: int | None = None,
    expanding: bool = False,
    initial_cash: float = 100_000.0,
    fees: float = 0.0005,
    slippage: float = 0.0005,
) -> WalkForwardResult:
    """Run walk-forward validation.

    `step_years` defaults to `test_years`, which makes OOS windows
    contiguous and non-overlapping — the canonical setup. Use a smaller
    step for stress tests of transition sensitivity.
    """
    if closes.empty:
        raise ValueError("closes is empty")
    if not closes.index.is_monotonic_increasing:
        raise ValueError("closes index must be sorted ascending")
    if train_years <= 0 or test_years <= 0:
        raise ValueError("train_years and test_years must be positive")

    step = step_years if step_years is not None else test_years
    if step <= 0:
        raise ValueError("step_years must be positive")

    folds_spec = _generate_fold_specs(
        closes.index,
        train_years=train_years,
        test_years=test_years,
        step_years=step,
        expanding=expanding,
    )
    if not folds_spec:
        raise ValueError(
            f"no WF folds fit in the available history "
            f"({closes.index[0].date()} → {closes.index[-1].date()}) "
            f"with train={train_years}y, test={test_years}y"
        )

    folds: list[WalkForwardFold] = []
    oos_return_frames: list[pd.Series] = []

    for i, (train_slice, test_slice) in enumerate(folds_spec):
        train_closes = closes.loc[train_slice[0] : train_slice[1]]
        # Strategy needs enough history to warm up any rolling features
        # (SMA, EMA, ...) — so feed train+test together, then grade only
        # on the OOS segment. This is standard WF practice.
        strat = strategy_factory(train_closes)
        # Concat by union of the two indices, preserving order.
        combined = closes.loc[train_slice[0] : test_slice[1]]
        weights = strat.target_weights(combined)
        result = run_backtest(
            combined,
            weights,
            initial_cash=initial_cash,
            fees=fees,
            slippage=slippage,
        )
        oos_returns = result.returns.loc[test_slice[0] : test_slice[1]]
        oos_equity = result.equity.loc[test_slice[0] : test_slice[1]]

        oos_sharpe = annualized_sharpe(oos_returns)
        oos_cagr = _cagr(oos_equity) if len(oos_equity) >= 2 else 0.0
        oos_max_dd = _max_drawdown(oos_equity) if len(oos_equity) >= 2 else 0.0

        folds.append(
            WalkForwardFold(
                fold_index=i,
                train_start=train_slice[0],
                train_end=train_slice[1],
                test_start=test_slice[0],
                test_end=test_slice[1],
                strategy_name=getattr(strat, "name", type(strat).__name__),
                oos_sharpe=oos_sharpe,
                oos_cagr=oos_cagr,
                oos_max_drawdown=oos_max_dd,
                result=result,
            )
        )
        oos_return_frames.append(oos_returns)

    concat_returns = pd.concat(oos_return_frames).sort_index()
    # OOS slices are contiguous by construction, so duplicates shouldn't
    # exist — but guard against overlap specified via custom step.
    concat_returns = concat_returns.loc[~concat_returns.index.duplicated(keep="first")]
    concat_sharpe = annualized_sharpe(concat_returns)

    return WalkForwardResult(
        folds=folds,
        oos_returns=concat_returns,
        oos_sharpe=concat_sharpe,
        metadata={
            "train_years": train_years,
            "test_years": test_years,
            "step_years": step,
            "expanding": expanding,
        },
    )


# --- Fold generation ----------------------------------------------------


def _generate_fold_specs(
    index: pd.DatetimeIndex,
    *,
    train_years: int,
    test_years: int,
    step_years: int,
    expanding: bool,
) -> list[tuple[tuple[pd.Timestamp, pd.Timestamp], tuple[pd.Timestamp, pd.Timestamp]]]:
    """Returns [(train_range, test_range), ...] as inclusive timestamp pairs."""
    start = index[0]
    end = index[-1]
    folds: list[tuple[tuple[pd.Timestamp, pd.Timestamp], tuple[pd.Timestamp, pd.Timestamp]]] = []

    train_end = start + pd.DateOffset(years=train_years)
    while True:
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.DateOffset(years=test_years) - pd.Timedelta(days=1)
        if test_end > end:
            break
        train_start = start if expanding else train_end - pd.DateOffset(years=train_years)
        folds.append(((train_start, train_end), (test_start, test_end)))
        train_end = train_end + pd.DateOffset(years=step_years)

    return folds


# --- Metric helpers (duplicated from reports.py to avoid circular import) -----


def _cagr(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    if total <= -1.0:
        return -1.0
    return float((1.0 + total) ** (1.0 / years) - 1.0)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min())


# --- Strategy factory helpers -------------------------------------------


def fixed_params(strategy: SignalStrategy) -> StrategyFactory:
    """Factory that ignores train data and returns the same strategy.

    Use for published-rule strategies (Faber's 10-month SMA) where
    params are the hypothesis and train data shouldn't alter them.
    """

    def _factory(_train_closes: pd.DataFrame) -> SignalStrategy:
        return strategy

    return _factory


def tuned_by_train_sharpe(
    candidates: list[SignalStrategy],
) -> StrategyFactory:
    """Factory that picks the candidate strategy with the highest
    in-sample Sharpe on the train window. Used to simulate a parameter
    search — the kind the DSR is designed to deflate.

    Every candidate is run against the train window on a fresh backtest
    and the best-Sharpe one is returned. Trial count should reflect
    `len(candidates)` when computing DSR on the OOS result.
    """

    def _factory(train_closes: pd.DataFrame) -> SignalStrategy:
        best: SignalStrategy | None = None
        best_sharpe = -np.inf
        for cand in candidates:
            weights = cand.target_weights(train_closes)
            if weights.dropna(how="all").empty:
                continue
            result = run_backtest(train_closes, weights, fees=0.0, slippage=0.0)
            s = annualized_sharpe(result.returns)
            if s > best_sharpe:
                best_sharpe = s
                best = cand
        if best is None:
            # Fallback to the first candidate — should never hit in WF
            # use because the train window is long.
            best = candidates[0]
        return best

    return _factory
