"""Run a strategy backtest against cached bars.

    uv run python scripts/run_backtest.py --strategy trend --start 2003-01-01

Loads bars from the Parquet cache (backfill them first with
`scripts/backfill.py`), runs the requested strategy, prints a tearsheet.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant.backtest import (
    align_on_common_dates,
    clip_to_range,
    closes_from_bars,
    compute_tearsheet,
    run_backtest,
)
from quant.config import get_settings
from quant.data import CacheKey, ParquetBarCache
from quant.signals import TrendSignal

app = typer.Typer(add_completion=False, help="Run a strategy backtest.")
console = Console()


def _default_universe(strategy: str) -> tuple[list[str], str]:
    """(risk_symbols, cash_symbol) defaults per strategy."""
    if strategy == "trend":
        return ["SPY", "EFA", "IEF"], "SGOV"
    raise typer.BadParameter(f"unknown strategy: {strategy}")


def _load_closes(
    symbols: list[str],
    *,
    cache_root: Path,
    cache_start: date,
    cache_end: date,
) -> dict[str, list]:
    cache = ParquetBarCache(cache_root)
    out: dict[str, list] = {}
    missing: list[str] = []
    for sym in symbols:
        key = CacheKey(symbol=sym, start=cache_start, end=cache_end)
        bars = cache.get(key)
        if bars is None:
            # Try any cached range for this symbol — users backfill with
            # different --years values and we shouldn't force a re-fetch.
            matching = (
                sorted((cache_root / sym).glob("*.parquet")) if (cache_root / sym).exists() else []
            )
            if not matching:
                missing.append(sym)
                continue
            bars = cache.get(
                CacheKey(
                    symbol=sym,
                    start=_parse_key_date(matching[-1].stem.split("_")[0]),
                    end=_parse_key_date(matching[-1].stem.split("_")[1]),
                )
            )
        if bars is None:
            missing.append(sym)
            continue
        out[sym] = bars
    if missing:
        raise typer.BadParameter(
            f"no cached bars for {missing}; run "
            f"`uv run python scripts/backfill.py {' '.join(missing)}` first"
        )
    return out


def _parse_key_date(s: str) -> date:
    return date.fromisoformat(s)


@app.command()
def backtest(
    strategy: Annotated[str, typer.Option("--strategy")] = "trend",
    start: Annotated[str, typer.Option("--start", help="YYYY-MM-DD")] = "2003-01-01",
    end: Annotated[str | None, typer.Option("--end", help="YYYY-MM-DD")] = None,
    universe: Annotated[
        list[str] | None, typer.Option("--universe", help="Override risk symbols.")
    ] = None,
    cash_symbol: Annotated[
        str | None, typer.Option("--cash-symbol", help="Override cash ticker.")
    ] = None,
    lookback_months: Annotated[int, typer.Option("--lookback-months")] = 10,
    initial_cash: Annotated[float, typer.Option("--initial-cash")] = 100_000.0,
    fees: Annotated[float, typer.Option("--fees")] = 0.0005,
    slippage: Annotated[float, typer.Option("--slippage")] = 0.0005,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    start_d = _parse_key_date(start)
    end_d = _parse_key_date(end) if end else date.today()  # noqa: DTZ011 — market session dates are local

    risk_syms_default, cash_default = _default_universe(strategy)
    risk_syms = universe or risk_syms_default
    cash = cash_symbol or cash_default
    cache_root = cache_dir or (get_settings().quant_data_dir / "parquet")

    all_syms = [*risk_syms, cash]
    bars = _load_closes(all_syms, cache_root=cache_root, cache_start=start_d, cache_end=end_d)
    closes = closes_from_bars(bars)
    closes = align_on_common_dates(closes, min_periods=len(all_syms))
    closes = clip_to_range(closes, start=start_d, end=end_d)
    if closes.empty:
        raise typer.BadParameter(
            "no overlapping dates across the requested universe; try a later --start"
        )

    if strategy == "trend":
        signal = TrendSignal(lookback_months=lookback_months, cash_symbol=cash)
    else:
        raise typer.BadParameter(f"unknown strategy: {strategy}")

    weights = signal.target_weights(closes)
    result = run_backtest(closes, weights, initial_cash=initial_cash, fees=fees, slippage=slippage)
    ts = compute_tearsheet(result)

    # Pretty-print tearsheet.
    tbl = Table(title=f"{strategy} · {risk_syms} + cash={cash}")
    tbl.add_column("metric")
    tbl.add_column("value", justify="right")
    rows = [
        ("period", f"{ts.start.date()} → {ts.end.date()}  ({ts.years:.2f}y)"),
        ("initial cash", f"${result.initial_cash:,.2f}"),
        ("final equity", f"${ts.final_equity:,.2f}"),
        ("total return", f"{ts.total_return * 100:+.2f}%"),
        ("CAGR", f"{ts.cagr * 100:+.2f}%"),
        ("annual vol", f"{ts.annual_vol * 100:.2f}%"),
        ("Sharpe", f"{ts.sharpe:.2f}"),
        ("Sortino", f"{ts.sortino:.2f}"),
        ("Calmar", f"{ts.calmar:.2f}"),
        ("max drawdown", f"{ts.max_drawdown * 100:.2f}% ({ts.max_drawdown_duration_days}d)"),
        ("best month", f"{ts.best_month * 100:+.2f}%"),
        ("worst month", f"{ts.worst_month * 100:+.2f}%"),
        ("monthly hit rate", f"{ts.monthly_hit_rate * 100:.1f}%"),
        ("rebalances", f"{ts.num_rebalances}"),
        ("avg turnover", f"{ts.avg_turnover * 100:.1f}%"),
        ("total cost", f"${ts.total_cost * result.initial_cash:,.2f}"),
    ]
    for k, v in rows:
        tbl.add_row(k, v)
    console.print(tbl)


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        console.print(f"[red]failed[/red]: {exc}")
        sys.exit(1)
