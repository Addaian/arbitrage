"""Tearsheet metrics for a `BacktestResult`.

Scope: the classic set — CAGR, Sharpe, Sortino, Calmar, max drawdown,
monthly hit rate, exposure, turnover. Plus a monthly-returns pivot for
the heatmap in the CLI output.

Everything is computed from the daily return/equity series; no look-
ahead, no external data. The risk-free rate is zero unless passed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from quant.backtest.engine import BacktestResult

_TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Tearsheet:
    start: pd.Timestamp
    end: pd.Timestamp
    days: int
    years: float
    final_equity: float
    total_return: float
    cagr: float
    annual_vol: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    max_drawdown_duration_days: int
    best_month: float
    worst_month: float
    monthly_hit_rate: float
    num_rebalances: int
    avg_turnover: float
    total_cost: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compute_tearsheet(result: BacktestResult, *, risk_free: float = 0.0) -> Tearsheet:
    returns = result.returns
    equity = result.equity
    if returns.empty:
        raise ValueError("backtest returned no data")

    start = returns.index[0]
    end = returns.index[-1]
    days = int((end - start).days)
    years = max(days / 365.25, 1e-9)

    total_return = float(equity.iloc[-1] / result.initial_cash - 1.0)
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1.0 else -1.0

    excess = returns - (risk_free / _TRADING_DAYS_PER_YEAR)
    annual_vol = float(returns.std(ddof=1) * np.sqrt(_TRADING_DAYS_PER_YEAR))
    sharpe = (
        float(excess.mean() / returns.std(ddof=1) * np.sqrt(_TRADING_DAYS_PER_YEAR))
        if returns.std(ddof=1) > 0
        else 0.0
    )

    downside = returns.clip(upper=0.0)
    downside_std = float(downside.std(ddof=1))
    sortino = (
        float(excess.mean() / downside_std * np.sqrt(_TRADING_DAYS_PER_YEAR))
        if downside_std > 0
        else 0.0
    )

    max_dd, max_dd_duration = _max_drawdown(equity)
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    monthly_hit_rate = float((monthly > 0).mean()) if not monthly.empty else 0.0
    best_month = float(monthly.max()) if not monthly.empty else 0.0
    worst_month = float(monthly.min()) if not monthly.empty else 0.0

    return Tearsheet(
        start=start,
        end=end,
        days=days,
        years=round(years, 3),
        final_equity=float(equity.iloc[-1]),
        total_return=total_return,
        cagr=float(cagr),
        annual_vol=annual_vol,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown=float(max_dd),
        max_drawdown_duration_days=int(max_dd_duration),
        best_month=best_month,
        worst_month=worst_month,
        monthly_hit_rate=monthly_hit_rate,
        num_rebalances=len(result.trades),
        avg_turnover=float(result.trades["turnover"].mean()) if not result.trades.empty else 0.0,
        total_cost=float(result.trades["cost"].sum()) if not result.trades.empty else 0.0,
    )


def monthly_returns_pivot(result: BacktestResult) -> pd.DataFrame:
    """Returns a years x months matrix of monthly returns — the canonical
    heatmap shape. Missing months are NaN.
    """
    monthly = (1.0 + result.returns).resample("ME").prod() - 1.0
    df = pd.DataFrame({"r": monthly})
    df["year"] = df.index.year
    df["month"] = df.index.month
    return df.pivot(index="year", columns="month", values="r")


# --- Internals ----------------------------------------------------------


def _max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """Return (maximum drawdown as negative fraction, duration in days)."""
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    trough = dd.idxmin()
    max_dd = float(dd.min())

    # Duration: from peak before trough to next new high (or series end).
    pre_trough = equity.loc[:trough]
    peak = pre_trough.idxmax()
    post_trough = equity.loc[trough:]
    recovered = post_trough[post_trough >= equity.loc[peak]]
    end = recovered.index[0] if not recovered.empty else equity.index[-1]
    duration = (end - peak).days
    return max_dd, duration
