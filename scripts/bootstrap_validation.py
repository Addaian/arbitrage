"""Stationary block-bootstrap robustness check.

Resamples the historical daily-return tape with replacement (preserving
serial dependence via Politis-Romano blocks) and runs each surviving
strategy through the alternate history. Reports the distribution of
Sharpe / max-DD / CAGR alongside the realized point estimate.

Usage:
    uv run python scripts/bootstrap_validation.py --strategy trend
    uv run python scripts/bootstrap_validation.py --strategy combined --n-paths 500

Exit codes:
    0  realized Sharpe within the [`--gate-low`, +inf) band of the
       bootstrap distribution.
    1  realized Sharpe below `--gate-low` or below the 5th percentile of
       the bootstrap distribution. Either case suggests the historical
       Sharpe was a fluke.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quant.backtest import (
    align_on_common_dates,
    annualized_sharpe,
    bootstrap_backtest,
    closes_from_bars,
    run_backtest,
)
from quant.config import get_settings
from quant.data import CacheKey, ParquetBarCache
from quant.portfolio import combine_weights
from quant.signals import MeanReversionSignal, MomentumSignal, TrendSignal

CASH = "SGOV"
TREND_RISK = ["SPY", "EFA", "IEF"]
MOMENTUM_RISK = ["SPY", "QQQ", "EFA", "EEM", "GLD", "IEF", "TLT", "VNQ", "DBC", "XLE"]
MEAN_REV_RISK = MOMENTUM_RISK
COSTS = {"fees": 0.0005, "slippage": 0.0005}

app = typer.Typer(add_completion=False, help="Bootstrap robustness check.")
console = Console()


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _load_ohlc(
    universe: list[str], cache_root: Path
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cache = ParquetBarCache(cache_root)
    closes_b: dict[str, list] = {}
    highs_b: dict[str, list] = {}
    lows_b: dict[str, list] = {}
    for sym in universe:
        symbol_dir = cache_root / sym
        parquets = sorted(symbol_dir.glob("*.parquet")) if symbol_dir.exists() else []
        if not parquets:
            raise typer.BadParameter(
                f"no cached bars for {sym}; run `scripts/backfill.py {' '.join(universe)}` first"
            )
        latest = parquets[-1]
        start_s, end_s = latest.stem.split("_")
        bars = cache.get(CacheKey(symbol=sym, start=_parse_date(start_s), end=_parse_date(end_s)))
        if bars is None:
            raise typer.BadParameter(f"cache miss for {sym}")
        closes_b[sym] = bars
        highs_b[sym] = bars
        lows_b[sym] = bars
    closes = closes_from_bars(closes_b)
    closes = align_on_common_dates(closes, min_periods=len(universe))
    # Build highs/lows on each symbol's full timestamp index, then reindex
    # onto the closes index so all three frames share the same dates.
    highs = pd.concat(
        {
            sym: pd.Series(
                [float(b.high) for b in bars],
                index=[pd.Timestamp(b.ts) for b in bars],
                name=sym,
            )
            for sym, bars in highs_b.items()
        },
        axis=1,
    ).reindex(closes.index)
    lows = pd.concat(
        {
            sym: pd.Series(
                [float(b.low) for b in bars],
                index=[pd.Timestamp(b.ts) for b in bars],
                name=sym,
            )
            for sym, bars in lows_b.items()
        },
        axis=1,
    ).reindex(closes.index)
    return closes, highs, lows


def _trend_weights_fn(closes: pd.DataFrame) -> pd.DataFrame:
    sig = TrendSignal(lookback_months=10, cash_symbol=CASH)
    return sig.target_weights(closes)


def _momentum_weights_fn(closes: pd.DataFrame) -> pd.DataFrame:
    sig = MomentumSignal(lookback_months=6, top_n=3, cash_symbol=CASH)
    return sig.target_weights(closes)


def _make_combined_weights_fn(
    highs: pd.DataFrame, lows: pd.DataFrame, allocations: dict[str, float]
):
    def combined_weights(closes: pd.DataFrame) -> pd.DataFrame:
        trend_cols = [c for c in TREND_RISK if c in closes.columns] + [CASH]
        mom_cols = [c for c in MOMENTUM_RISK if c in closes.columns] + [CASH]
        mr_cols = [c for c in MEAN_REV_RISK if c in closes.columns] + [CASH]
        trend_w = TrendSignal(lookback_months=10, cash_symbol=CASH).target_weights(
            closes[trend_cols]
        )
        mom_w = MomentumSignal(lookback_months=6, top_n=3, cash_symbol=CASH).target_weights(
            closes[mom_cols]
        )
        # Bootstrap doesn't give us highs/lows for the resampled path,
        # but mean-reversion needs IBS — synthesize from the resampled
        # closes by assuming high/low = close * (1 ± mean_range), keeping
        # the IBS distribution roughly historical. Cheap proxy.
        h = closes.copy()
        lo = closes.copy()
        avg_h = (highs / closes.reindex(highs.index)).mean()
        avg_l = (lows / closes.reindex(lows.index)).mean()
        for c in closes.columns:
            if c in avg_h:
                h[c] = closes[c] * float(avg_h[c])
                lo[c] = closes[c] * float(avg_l[c])
        mr_w = MeanReversionSignal(cash_symbol=CASH).target_weights(
            closes[mr_cols], h[mr_cols], lo[mr_cols]
        )
        combined = combine_weights(
            {"trend": trend_w, "momentum": mom_w, "mean_reversion": mr_w}, allocations
        )
        if CASH not in combined.columns:
            combined[CASH] = 0.0
        return combined.reindex(columns=closes.columns, fill_value=0.0)

    return combined_weights


def _print_distribution(title: str, metrics: pd.DataFrame, realized: dict[str, float]) -> None:
    tbl = Table(title=title)
    tbl.add_column("metric")
    tbl.add_column("realized", justify="right")
    tbl.add_column("p5", justify="right")
    tbl.add_column("p50", justify="right")
    tbl.add_column("p95", justify="right")
    tbl.add_column("rank %", justify="right", style="dim")
    for col in ["sharpe", "max_drawdown", "cagr"]:
        series = metrics[col]
        p5 = float(np.nanpercentile(series, 5))
        p50 = float(np.nanpercentile(series, 50))
        p95 = float(np.nanpercentile(series, 95))
        realized_v = realized[col]
        rank = float((series <= realized_v).mean() * 100.0)
        scale = 100.0 if col != "sharpe" else 1.0
        suffix = "%" if col != "sharpe" else ""
        tbl.add_row(
            col,
            f"{realized_v * scale:+.2f}{suffix}",
            f"{p5 * scale:+.2f}{suffix}",
            f"{p50 * scale:+.2f}{suffix}",
            f"{p95 * scale:+.2f}{suffix}",
            f"{rank:.0f}",
        )
    console.print(tbl)


@app.command()
def run(
    strategy: Annotated[
        str,
        typer.Option("--strategy", help="trend | momentum | combined"),
    ] = "combined",
    n_paths: Annotated[int, typer.Option("--n-paths")] = 200,
    expected_block_size: Annotated[int, typer.Option("--block-size")] = 10,
    seed: Annotated[int, typer.Option("--seed")] = 42,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
    gate_low: Annotated[
        float,
        typer.Option("--gate-low", help="Realized Sharpe minimum for exit 0"),
    ] = 0.4,
) -> None:
    cache_root = cache_dir or (get_settings().quant_data_dir / "parquet")

    if strategy == "trend":
        universe = [*TREND_RISK, CASH]
        closes, _, _ = _load_ohlc(universe, cache_root)
        weights_fn = _trend_weights_fn
    elif strategy == "momentum":
        universe = [*MOMENTUM_RISK, CASH]
        closes, _, _ = _load_ohlc(universe, cache_root)
        weights_fn = _momentum_weights_fn
    elif strategy == "combined":
        universe = sorted(set(TREND_RISK + MOMENTUM_RISK + MEAN_REV_RISK + [CASH]))
        closes, highs, lows = _load_ohlc(universe, cache_root)
        weights_fn = _make_combined_weights_fn(
            highs,
            lows,
            allocations={
                "trend": 0.4 / 0.85,
                "momentum": 0.3 / 0.85,
                "mean_reversion": 0.15 / 0.85,
            },
        )
    else:
        raise typer.BadParameter(f"unknown strategy: {strategy!r}")

    console.print(
        f"[bold]bootstrap[/bold] strategy={strategy} "
        f"n_paths={n_paths} block_size={expected_block_size}  "
        f"({closes.index[0].date()} → {closes.index[-1].date()}, {len(closes)} bars)"
    )

    # Realized point estimate.
    realized_w = weights_fn(closes)
    realized_result = run_backtest(closes, realized_w, **COSTS)
    realized_sharpe = float(annualized_sharpe(realized_result.returns))
    running_max = realized_result.equity.cummax()
    realized_dd = float((realized_result.equity / running_max - 1.0).min())
    n_years = max(len(realized_result.equity) / 252.0, 1.0 / 252.0)
    realized_cagr = float(
        (realized_result.equity.iloc[-1] / realized_result.equity.iloc[0]) ** (1.0 / n_years) - 1.0
    )
    realized = {
        "sharpe": realized_sharpe,
        "max_drawdown": realized_dd,
        "cagr": realized_cagr,
    }

    # Bootstrap distribution.
    metrics = bootstrap_backtest(
        closes,
        weights_fn,
        n_paths=n_paths,
        expected_block_size=expected_block_size,
        seed=seed,
        **COSTS,
    )

    _print_distribution(f"{strategy} bootstrap distribution", metrics, realized)

    p5_sharpe = float(np.nanpercentile(metrics["sharpe"], 5))
    if realized_sharpe < gate_low:
        console.print(
            f"[red]FAIL[/red]: realized Sharpe {realized_sharpe:+.2f} below --gate-low={gate_low}"
        )
        raise typer.Exit(code=1)
    if realized_sharpe < p5_sharpe:
        console.print(
            f"[red]FAIL[/red]: realized Sharpe {realized_sharpe:+.2f} below 5th percentile "
            f"{p5_sharpe:+.2f} of bootstrap distribution — looks like a fluke"
        )
        raise typer.Exit(code=1)
    console.print("[green]PASS[/green]: realized Sharpe is robust under bootstrap")


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        console.print(f"[red]failed[/red]: {exc}")
        sys.exit(1)
