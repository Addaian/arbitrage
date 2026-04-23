"""Week-13 research sprint: honest evaluation of all 3 strategies.

Produces the numbers that drive the go/no-go decision in
`docs/research/week13_validation.md`. Covers:

1. **Full-history backtest** per strategy at Alpaca-realistic costs
   (0 bp commission, 3 bp slippage).
2. **Walk-forward + DSR** per strategy (10y train / 2y test, 5 folds).
3. **Stress-period Sharpes** across the four named windows:
   2008 GFC, 2020 COVID crash, 2022 bond/equity double-down, April 2025.
4. **Vol-regime-conditioned Sharpes** (low / mid / high SPY vol).
5. **Combined-portfolio** Sharpe under allocations from
   `config/strategies.yaml` (0.40 / 0.30 / 0.15 normalized).

Read-only: loads from the Parquet cache, runs backtests, prints tables.
No DB writes, no broker calls. Run time ~15s for the full sweep.

Usage:
    uv run python scripts/research_sprint.py
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quant.backtest import compute_tearsheet, run_backtest
from quant.backtest.deflated_sharpe import annualized_sharpe, deflated_sharpe_ratio
from quant.backtest.walk_forward import fixed_params, walk_forward
from quant.data import CacheKey, ParquetBarCache
from quant.portfolio import combine_weights
from quant.signals import MeanReversionSignal, MomentumSignal, TrendSignal

CONSOLE = Console()
CACHE_ROOT = Path("data/parquet")
COSTS = {"fees": 0.0, "slippage": 0.0003}  # Alpaca ETF cost profile

# PRD §5 universes.
TREND_SYMBOLS = ["SPY", "EFA", "IEF", "SHY"]
MOMENTUM_SYMBOLS = [
    "SPY",
    "QQQ",
    "EFA",
    "EEM",
    "GLD",
    "IEF",
    "TLT",
    "VNQ",
    "DBC",
    "XLE",
    "SHY",
]
MEAN_REV_SYMBOLS = MOMENTUM_SYMBOLS
CASH = "SHY"

STRESS_WINDOWS = {
    "2008 GFC": (date(2008, 9, 1), date(2009, 3, 31)),
    "2020 COVID": (date(2020, 2, 15), date(2020, 4, 30)),
    "2022 bonds+equity": (date(2022, 1, 1), date(2022, 12, 31)),
    "April 2025": (date(2025, 4, 1), date(2025, 4, 30)),
}


# --- Data plumbing ------------------------------------------------------


def _load_widest(symbol: str, cache: ParquetBarCache) -> pd.DataFrame:
    parquets = sorted((CACHE_ROOT / symbol).glob("*.parquet"))
    widest = min(parquets, key=lambda p: p.stem.split("_")[0])
    start_s, end_s = widest.stem.split("_")
    bars = cache.get(
        CacheKey(symbol=symbol, start=date.fromisoformat(start_s), end=date.fromisoformat(end_s))
    )
    if bars is None:
        raise RuntimeError(f"cache miss on {symbol}")
    idx = [pd.Timestamp(b.ts) for b in bars]
    return pd.DataFrame(
        {
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
        },
        index=idx,
    )


def _load_ohlc(symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cache = ParquetBarCache(CACHE_ROOT)
    per_symbol = {s: _load_widest(s, cache) for s in symbols}
    common = None
    for df in per_symbol.values():
        common = df.index if common is None else common.intersection(df.index)
    assert common is not None
    closes = pd.DataFrame({s: per_symbol[s]["close"].loc[common] for s in symbols})
    highs = pd.DataFrame({s: per_symbol[s]["high"].loc[common] for s in symbols})
    lows = pd.DataFrame({s: per_symbol[s]["low"].loc[common] for s in symbols})
    return closes.sort_index(), highs.sort_index(), lows.sort_index()


# --- Per-strategy runner -----------------------------------------------


@dataclass(frozen=True)
class StrategyEvalRow:
    name: str
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    num_rebalances: int
    oos_sharpe: float
    dsr_psr: float
    dsr_benchmark: float
    stress_sharpes: dict[str, float]
    regime_sharpes: dict[str, float]
    returns_series_name: str  # for correlation tables


def _eval_trend(
    closes_all: pd.DataFrame,
) -> tuple[pd.Series, StrategyEvalRow, pd.DataFrame]:
    closes = closes_all[TREND_SYMBOLS]
    signal = TrendSignal(lookback_months=10, cash_symbol=CASH)
    weights = signal.target_weights(closes)
    result = run_backtest(closes, weights, **COSTS)
    ts = compute_tearsheet(result)

    wf = walk_forward(closes, fixed_params(signal), train_years=10, test_years=2, **COSTS)
    dsr = deflated_sharpe_ratio(wf.oos_returns, num_trials=wf.num_folds)

    stress = _stress_sharpes(result.returns, closes_all)
    regime = _regime_sharpes(result.returns, closes_all)

    row = StrategyEvalRow(
        name="trend",
        cagr=ts.cagr,
        sharpe=ts.sharpe,
        sortino=ts.sortino,
        max_drawdown=ts.max_drawdown,
        num_rebalances=ts.num_rebalances,
        oos_sharpe=wf.oos_sharpe,
        dsr_psr=dsr.psr,
        dsr_benchmark=dsr.benchmark_sharpe,
        stress_sharpes=stress,
        regime_sharpes=regime,
        returns_series_name="trend",
    )
    return result.returns, row, weights


def _eval_momentum(
    closes_all: pd.DataFrame,
) -> tuple[pd.Series, StrategyEvalRow, pd.DataFrame]:
    closes = closes_all[MOMENTUM_SYMBOLS]
    signal = MomentumSignal(lookback_months=6, top_n=3, cash_symbol=CASH)
    weights = signal.target_weights(closes)
    result = run_backtest(closes, weights, **COSTS)
    ts = compute_tearsheet(result)

    wf = walk_forward(closes, fixed_params(signal), train_years=10, test_years=2, **COSTS)
    dsr = deflated_sharpe_ratio(wf.oos_returns, num_trials=wf.num_folds)

    stress = _stress_sharpes(result.returns, closes_all)
    regime = _regime_sharpes(result.returns, closes_all)

    row = StrategyEvalRow(
        name="momentum",
        cagr=ts.cagr,
        sharpe=ts.sharpe,
        sortino=ts.sortino,
        max_drawdown=ts.max_drawdown,
        num_rebalances=ts.num_rebalances,
        oos_sharpe=wf.oos_sharpe,
        dsr_psr=dsr.psr,
        dsr_benchmark=dsr.benchmark_sharpe,
        stress_sharpes=stress,
        regime_sharpes=regime,
        returns_series_name="momentum",
    )
    return result.returns, row, weights


def _eval_mean_rev(
    closes_all: pd.DataFrame, highs_all: pd.DataFrame, lows_all: pd.DataFrame
) -> tuple[pd.Series, StrategyEvalRow, pd.DataFrame]:
    closes = closes_all[MEAN_REV_SYMBOLS]
    highs = highs_all[MEAN_REV_SYMBOLS]
    lows = lows_all[MEAN_REV_SYMBOLS]
    signal = MeanReversionSignal(cash_symbol=CASH)
    weights = signal.target_weights(closes, highs, lows)
    result = run_backtest(closes, weights, **COSTS)
    ts = compute_tearsheet(result)

    # Adapter so walk-forward (close-only factory) can drive OHLC.
    class _Adapter:
        name = "mean_reversion"

        def target_weights(self, sub_closes: pd.DataFrame) -> pd.DataFrame:
            h = highs.reindex(index=sub_closes.index, columns=sub_closes.columns)
            lo = lows.reindex(index=sub_closes.index, columns=sub_closes.columns)
            return signal.target_weights(sub_closes, h, lo)

    wf = walk_forward(closes, lambda _train: _Adapter(), train_years=10, test_years=2, **COSTS)
    dsr = deflated_sharpe_ratio(wf.oos_returns, num_trials=wf.num_folds)

    stress = _stress_sharpes(result.returns, closes_all)
    regime = _regime_sharpes(result.returns, closes_all)

    row = StrategyEvalRow(
        name="mean_reversion",
        cagr=ts.cagr,
        sharpe=ts.sharpe,
        sortino=ts.sortino,
        max_drawdown=ts.max_drawdown,
        num_rebalances=ts.num_rebalances,
        oos_sharpe=wf.oos_sharpe,
        dsr_psr=dsr.psr,
        dsr_benchmark=dsr.benchmark_sharpe,
        stress_sharpes=stress,
        regime_sharpes=regime,
        returns_series_name="mean_reversion",
    )
    return result.returns, row, weights


# --- Helpers -----------------------------------------------------------


def _stress_sharpes(returns: pd.Series, closes: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, (start, end) in STRESS_WINDOWS.items():
        mask = (returns.index >= pd.Timestamp(start)) & (returns.index <= pd.Timestamp(end))
        window = returns.loc[mask]
        if len(window) < 20:
            out[name] = float("nan")
            continue
        out[name] = annualized_sharpe(window)
    return out


def _regime_sharpes(returns: pd.Series, closes: pd.DataFrame) -> dict[str, float]:
    """Split returns by SPY rolling-60d vol tercile. Lower = calm regime."""
    spy_ret = closes["SPY"].pct_change()
    vol = spy_ret.rolling(60).std() * (252**0.5)
    vol = vol.reindex(returns.index)
    valid = vol.dropna()
    if valid.empty:
        return {"low": float("nan"), "mid": float("nan"), "high": float("nan")}
    low_q = valid.quantile(1 / 3)
    high_q = valid.quantile(2 / 3)
    out = {}
    for label, mask in [
        ("low", vol <= low_q),
        ("mid", (vol > low_q) & (vol <= high_q)),
        ("high", vol > high_q),
    ]:
        window = returns.loc[mask.fillna(False)]
        out[label] = annualized_sharpe(window) if len(window) > 30 else float("nan")
    return out


def _correlation_matrix(r_trend: pd.Series, r_mom: pd.Series, r_mr: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"trend": r_trend, "momentum": r_mom, "mean_reversion": r_mr}).dropna()
    return df.corr()


def _combined_eval(
    r_trend: pd.Series,
    r_mom: pd.Series,
    r_mr: pd.Series,
    closes: pd.DataFrame,
    trend_w: pd.DataFrame,
    mom_w: pd.DataFrame,
    mr_w: pd.DataFrame,
) -> dict[str, float]:
    raw = {"trend": 0.40, "momentum": 0.30, "mean_reversion": 0.15}
    total = sum(raw.values())
    alloc = {k: v / total for k, v in raw.items()}
    combined_w = combine_weights(
        {"trend": trend_w, "momentum": mom_w, "mean_reversion": mr_w}, alloc
    )
    combined_closes = closes[list(combined_w.columns)]
    result = run_backtest(combined_closes, combined_w, **COSTS)
    ts = compute_tearsheet(result)
    return {
        "sharpe": ts.sharpe,
        "cagr": ts.cagr,
        "max_drawdown": ts.max_drawdown,
        "num_rebalances": ts.num_rebalances,
        "sortino": ts.sortino,
    }


def _print_headline(rows: list[StrategyEvalRow]) -> None:
    tbl = Table(title="per-strategy headline (Alpaca ETF cost profile: 0 bp + 3 bp slip)")
    for col in ("strategy", "CAGR", "Sharpe", "Sortino", "maxDD", "OOS Sharpe", "DSR PSR", "rebal"):
        tbl.add_column(col, justify="right")
    for row in rows:
        tbl.add_row(
            row.name,
            f"{row.cagr * 100:+.2f}%",
            f"{row.sharpe:.3f}",
            f"{row.sortino:.3f}",
            f"{row.max_drawdown * 100:.2f}%",
            f"{row.oos_sharpe:+.3f}",
            f"{row.dsr_psr:.3f}",
            f"{row.num_rebalances}",
        )
    CONSOLE.print(tbl)


def _print_stress(rows: list[StrategyEvalRow]) -> None:
    tbl = Table(title="stress-window Sharpes (annualized)")
    tbl.add_column("strategy")
    for win in STRESS_WINDOWS:
        tbl.add_column(win, justify="right")
    for row in rows:
        cells = [row.name]
        for win in STRESS_WINDOWS:
            v = row.stress_sharpes[win]
            cells.append("NA" if np.isnan(v) else f"{v:+.2f}")
        tbl.add_row(*cells)
    CONSOLE.print(tbl)


def _print_regime(rows: list[StrategyEvalRow]) -> None:
    tbl = Table(title="vol-regime Sharpes (SPY 60d vol terciles)")
    tbl.add_column("strategy")
    for regime in ("low", "mid", "high"):
        tbl.add_column(regime, justify="right")
    for row in rows:
        cells = [row.name]
        for regime in ("low", "mid", "high"):
            v = row.regime_sharpes[regime]
            cells.append("NA" if np.isnan(v) else f"{v:+.2f}")
        tbl.add_row(*cells)
    CONSOLE.print(tbl)


def _print_correlation(corr: pd.DataFrame) -> None:
    tbl = Table(title="daily-return correlations (all-period)")
    tbl.add_column("")
    for col in corr.columns:
        tbl.add_column(col, justify="right")
    for idx in corr.index:
        tbl.add_row(idx, *(f"{corr.loc[idx, c]:+.3f}" for c in corr.columns))
    CONSOLE.print(tbl)


def _print_combined(summary: dict[str, float]) -> None:
    tbl = Table(title="combined 3-strategy portfolio (allocs 0.47/0.35/0.18 normalized)")
    tbl.add_column("metric")
    tbl.add_column("value", justify="right")
    tbl.add_row("Sharpe", f"{summary['sharpe']:.3f}")
    tbl.add_row("CAGR", f"{summary['cagr'] * 100:+.2f}%")
    tbl.add_row("max drawdown", f"{summary['max_drawdown'] * 100:.2f}%")
    tbl.add_row("Sortino", f"{summary['sortino']:.3f}")
    tbl.add_row("rebalances", f"{summary['num_rebalances']}")
    CONSOLE.print(tbl)


# --- Entry point -------------------------------------------------------


app = typer.Typer(add_completion=False, help="Week-13 research sprint.")


@app.command()
def run(
    output: str | None = typer.Option(
        None, "--output", help="Write JSON summary to this path (for docs)."
    ),
) -> None:
    universe = sorted(set(TREND_SYMBOLS + MOMENTUM_SYMBOLS + MEAN_REV_SYMBOLS))
    closes, highs, lows = _load_ohlc(universe)
    CONSOLE.print(
        f"[bold]data:[/bold] {closes.index[0].date()} -> {closes.index[-1].date()}  "
        f"({len(closes)} bars, {len(universe)} symbols)"
    )

    r_trend, trend_row, trend_w = _eval_trend(closes)
    r_mom, mom_row, mom_w = _eval_momentum(closes)
    r_mr, mr_row, mr_w = _eval_mean_rev(closes, highs, lows)
    rows = [trend_row, mom_row, mr_row]

    _print_headline(rows)
    _print_stress(rows)
    _print_regime(rows)

    corr = _correlation_matrix(r_trend, r_mom, r_mr)
    _print_correlation(corr)

    combined = _combined_eval(r_trend, r_mom, r_mr, closes, trend_w, mom_w, mr_w)
    _print_combined(combined)

    if output is not None:
        summary = {
            "data_range": [str(closes.index[0].date()), str(closes.index[-1].date())],
            "num_bars": len(closes),
            "strategies": [asdict(r) for r in rows],
            "correlation_matrix": corr.round(3).to_dict(),
            "combined_portfolio": combined,
        }
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(summary, indent=2, default=float))
        CONSOLE.print(f"[dim]wrote summary → {output}[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
