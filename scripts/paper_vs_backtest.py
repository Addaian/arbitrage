"""Paper-qualifier tracking-error analysis (Wave 19 / Gate 3).

Loads the last N days of `pnl_snapshots` rows from Postgres (the live
paper run's equity history) and compares to the same-window backtest
that *would have* run on the same bars had the strategy been driven
off-line. Emits:

    - 30-day paper Sharpe
    - 30-day backtest Sharpe (same window, same allocations, Alpaca cost model)
    - |paper - backtest| / |backtest| → tracking error
    - PASS if tracking error < 50% (plan's Gate 3), FAIL otherwise

PRD §1.2 long-run criterion: <30% tracking error on 90-day rolling.
Plan's Gate 3 is the looser <50% on 30 days to make the first paper
run cheaper to clear.

Usage:
    uv run python scripts/paper_vs_backtest.py [--days 30] [--min-dsr-psr 0.5]
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from quant.backtest import compute_tearsheet, run_backtest
from quant.backtest.deflated_sharpe import annualized_sharpe
from quant.config import get_settings, load_config_bundle
from quant.live.runner import (
    _build_multi_strategy_signal,
    _extract_sleeve_config,
    _load_cached_ohlc,
)
from quant.storage.db import dispose_engine, get_sessionmaker
from quant.storage.models import PnlSnapshotORM

CONSOLE = Console()

# Stylized Alpaca-paper cost model for the backtest counterpart. Keeps
# the comparison fair against a paper broker that doesn't itself model
# slippage. PRD §3.2 fees=0 (commission-free), 3 bps slippage covers
# bid/ask + market impact at retail size.
_COSTS = {"fees": 0.0, "slippage": 0.0003}


@dataclass
class TrackingResult:
    start_date: date
    end_date: date
    days: int
    paper_sharpe: float
    backtest_sharpe: float
    tracking_error_pct: float
    passes: bool


app = typer.Typer(add_completion=False, help="Paper-qualifier tracking-error analysis.")


@app.command()
def analyse(
    days: Annotated[int, typer.Option("--days", help="Trailing window size")] = 30,
    max_tracking_error: Annotated[
        float,
        typer.Option(
            "--max-tracking-error",
            help="Maximum allowed |paper-backtest|/|backtest|. Default 0.50 per Gate 3.",
        ),
    ] = 0.50,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    """Compute trailing paper vs backtest Sharpe + tracking error."""
    try:
        result = asyncio.run(_run(days=days, cache_dir=cache_dir))
    except Exception as exc:
        CONSOLE.print(f"[red]failed[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    _print_report(result)
    if result.tracking_error_pct > max_tracking_error * 100:
        raise typer.Exit(code=1)


async def _run(*, days: int, cache_dir: Path | None) -> TrackingResult:
    sessionmaker = get_sessionmaker()
    end_ts = datetime.now(UTC)
    start_ts = end_ts - timedelta(days=days + 5)  # +5 for lookback slack

    async with sessionmaker() as session:
        stmt = (
            select(PnlSnapshotORM.ts, PnlSnapshotORM.equity)
            .where(PnlSnapshotORM.ts >= start_ts)
            .order_by(PnlSnapshotORM.ts)
        )
        rows = (await session.execute(stmt)).all()
    await dispose_engine()

    if len(rows) < 2:
        raise RuntimeError(
            f"not enough PnL snapshots to compute tracking ({len(rows)} found); "
            "run the paper cycle for at least 2 trading days first"
        )

    paper_equity = pd.Series(
        [float(r.equity) for r in rows],
        index=pd.DatetimeIndex([r.ts for r in rows]),
    )
    paper_equity = paper_equity.sort_index().tail(days + 1)
    paper_returns = paper_equity.pct_change().dropna()
    paper_sharpe = annualized_sharpe(paper_returns)

    start_date = paper_equity.index[0].date()
    end_date = paper_equity.index[-1].date()

    # Backtest counterpart on the same window.
    backtest_sharpe = _backtest_sharpe_for_window(
        start=start_date, end=end_date, cache_dir=cache_dir
    )

    if abs(backtest_sharpe) < 1e-9:
        # Guard against div-by-zero if the backtest Sharpe happens to be
        # ~0 on a short window. Report the absolute gap instead.
        tracking_error_pct = abs(paper_sharpe - backtest_sharpe) * 100
    else:
        tracking_error_pct = abs(paper_sharpe - backtest_sharpe) / abs(backtest_sharpe) * 100

    return TrackingResult(
        start_date=start_date,
        end_date=end_date,
        days=len(paper_returns),
        paper_sharpe=paper_sharpe,
        backtest_sharpe=backtest_sharpe,
        tracking_error_pct=tracking_error_pct,
        passes=tracking_error_pct < 50.0,
    )


def _backtest_sharpe_for_window(*, start: date, end: date, cache_dir: Path | None = None) -> float:
    """Run the production multi-strategy assembly (combined sleeves +
    regime + vol-target overlays) over [start, end] using cached OHLC,
    return annualized Sharpe on the daily-return series.

    Reuses `_build_multi_strategy_signal` so the backtest counterpart
    matches the live runner exactly — tracking error then measures only
    cycle-level execution differences (slippage, partial fills, timing),
    not strategy-assembly mismatch.
    """
    settings = get_settings()
    cache_root = cache_dir or (settings.quant_data_dir / "parquet")

    bundle = load_config_bundle()
    sleeve_universes, allocations, sleeve_params = _extract_sleeve_config(bundle)
    cash_symbol = bundle.universe.cash_symbol
    union = sorted({sym for u in sleeve_universes.values() for sym in u} | {cash_symbol})

    closes, highs, lows = _load_cached_ohlc(cache_root, union, window=10_000)
    closes = closes.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    highs = highs.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    lows = lows.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    if closes.empty or len(closes) < 2:
        return 0.0

    signal = _build_multi_strategy_signal(
        bundle=bundle,
        sleeve_universes=sleeve_universes,
        sleeve_params=sleeve_params,
        allocations=allocations,
        cash_symbol=cash_symbol,
        highs_lows_provider=lambda: (highs, lows),
        regime_model_path=settings.quant_data_dir / "models" / "regime_latest.joblib",
    )
    weights = signal.target_weights(closes)
    if weights.dropna(how="all").empty:
        return 0.0
    aligned_closes = closes.reindex(columns=weights.columns).ffill()
    result = run_backtest(aligned_closes, weights, **_COSTS)
    return compute_tearsheet(result).sharpe


def _print_report(r: TrackingResult) -> None:
    tbl = Table(title=f"paper-vs-backtest tracking ({r.start_date} → {r.end_date})")
    tbl.add_column("metric")
    tbl.add_column("value", justify="right")
    tbl.add_row("days of data", str(r.days))
    tbl.add_row("paper Sharpe", f"{r.paper_sharpe:+.3f}")
    tbl.add_row("backtest Sharpe", f"{r.backtest_sharpe:+.3f}")
    tbl.add_row("tracking error", f"{r.tracking_error_pct:.1f}%")
    CONSOLE.print(tbl)
    verdict = "[green]PASS[/green]" if r.passes else "[red]FAIL[/red]"
    CONSOLE.print(f"\n{verdict}  tracking-error threshold 50% (Gate 3)")


def compute_tracking_error(paper_sharpe: float, backtest_sharpe: float) -> float:
    """Pure function exposed for testing — |paper - backtest| / |backtest|.

    Returns the absolute gap (fractional percent x100) when backtest
    Sharpe is near zero, matching `_run`'s behaviour.
    """
    if abs(backtest_sharpe) < 1e-9:
        return abs(paper_sharpe - backtest_sharpe) * 100
    return abs(paper_sharpe - backtest_sharpe) / abs(backtest_sharpe) * 100


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
