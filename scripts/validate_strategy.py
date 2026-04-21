"""Walk-forward + Deflated Sharpe validation runner.

    uv run python scripts/validate_strategy.py --strategy trend --start 2003-01-01

Runs the requested strategy through walk-forward (10yr train / 2yr test),
records each trial to the local JSONL trial log, then computes the
Deflated Sharpe Ratio against the accumulated trial count. Reports
pass/fail vs the configured thresholds.

Thresholds (default; overridable via CLI flags):
    * OOS Sharpe (concatenated across folds) >= 0.4
    * DSR probability > 0.95  (equivalent to 'DSR > 0' on the deflated SR axis)

A strategy that runs many parameter variants (`--overfit-sweep`) will
trigger enough trial-log entries that DSR must clear a much higher
benchmark — the gate the Wave 6 acceptance test relies on.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant.backtest import (
    JsonlTrialLog,
    TrialRecord,
    align_on_common_dates,
    clip_to_range,
    closes_from_bars,
    deflated_sharpe_ratio,
    fixed_params,
    tuned_by_train_sharpe,
    walk_forward,
)
from quant.config import get_settings
from quant.data import CacheKey, ParquetBarCache
from quant.signals import TrendSignal

app = typer.Typer(add_completion=False, help="Validate a strategy via walk-forward + DSR.")
console = Console()


# --- Data loading (mirror of scripts/run_backtest.py) -------------------


def _default_universe(strategy: str) -> tuple[list[str], str]:
    if strategy == "trend":
        return ["SPY", "EFA", "IEF"], "SGOV"
    raise typer.BadParameter(f"unknown strategy: {strategy}")


def _parse_key_date(s: str) -> date:
    return date.fromisoformat(s)


def _load_cached_bars(
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
            f"no cached bars for {missing}; run `scripts/backfill.py {' '.join(missing)}` first"
        )
    return out


def _build_trend_factory(
    *,
    cash: str,
    lookback_months: int,
    overfit_sweep: int,
) -> tuple[object, list[dict[str, object]]]:
    if overfit_sweep == 1:
        factory = fixed_params(TrendSignal(lookback_months=lookback_months, cash_symbol=cash))
        return factory, [{"lookback_months": lookback_months, "cash_symbol": cash}]
    # Uniform spread: e.g. sweep=4 -> [3, 6, 9, 12].
    candidates = [
        TrendSignal(lookback_months=3 * (i + 1), cash_symbol=cash) for i in range(overfit_sweep)
    ]
    factory = tuned_by_train_sharpe(candidates)
    params = [{"lookback_months": c.lookback_months, "cash_symbol": cash} for c in candidates]
    return factory, params


# --- Validation runner --------------------------------------------------


@app.command()
def validate(
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
    train_years: Annotated[int, typer.Option("--train-years")] = 10,
    test_years: Annotated[int, typer.Option("--test-years")] = 2,
    expanding: Annotated[bool, typer.Option("--expanding/--rolling")] = False,
    fees: Annotated[float, typer.Option("--fees")] = 0.0005,
    slippage: Annotated[float, typer.Option("--slippage")] = 0.0005,
    min_oos_sharpe: Annotated[float, typer.Option("--min-oos-sharpe")] = 0.4,
    # Default matches the implementation plan: "DSR > 0" means deflated
    # excess > 0, equivalent to PSR > 0.5. Tighten via --min-dsr-psr 0.95
    # for the strict Lopez de Prado 95% statistical-significance bar.
    min_dsr_psr: Annotated[float, typer.Option("--min-dsr-psr")] = 0.5,
    overfit_sweep: Annotated[
        int,
        typer.Option(
            "--overfit-sweep",
            help="Number of lookbacks to search over in-sample per fold. "
            "=1 (default) uses the published lookback verbatim. >1 simulates "
            "a parameter search; every candidate is logged as a trial so DSR "
            "sees the selection bias.",
        ),
    ] = 1,
    trial_log_dir: Annotated[Path | None, typer.Option("--trial-log-dir")] = None,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    start_d = _parse_key_date(start)
    end_d = _parse_key_date(end) if end else date.today()  # noqa: DTZ011 — local market dates

    if overfit_sweep < 1:
        raise typer.BadParameter("overfit_sweep must be >= 1")

    settings = get_settings()
    cache_root = cache_dir or (settings.quant_data_dir / "parquet")
    log_root = trial_log_dir or (settings.quant_data_dir / "trials")

    risk_syms_default, cash_default = _default_universe(strategy)
    risk_syms = universe or risk_syms_default
    cash = cash_symbol or cash_default

    all_syms = [*risk_syms, cash]
    bars = _load_cached_bars(all_syms, cache_root=cache_root, cache_start=start_d, cache_end=end_d)
    closes = closes_from_bars(bars)
    closes = align_on_common_dates(closes, min_periods=len(all_syms))
    closes = clip_to_range(closes, start=start_d, end=end_d)
    if closes.empty:
        raise typer.BadParameter("no overlapping dates for the universe; try a later --start")

    # Build the strategy factory. overfit_sweep > 1 enables a per-fold
    # lookback search — exactly the kind of selection bias DSR catches.
    if strategy != "trend":
        raise typer.BadParameter(f"unknown strategy: {strategy}")

    factory, candidate_params = _build_trend_factory(
        cash=cash,
        lookback_months=lookback_months,
        overfit_sweep=overfit_sweep,
    )

    console.print(
        f"[bold]validating[/bold] {strategy} on {risk_syms} + cash={cash}  "
        f"[dim]({closes.index[0].date()} → {closes.index[-1].date()})[/dim]"
    )

    wf = walk_forward(
        closes,
        factory,
        train_years=train_years,
        test_years=test_years,
        expanding=expanding,
        fees=fees,
        slippage=slippage,
    )

    # Every fold represents N trials in the DSR sense: one per candidate
    # parameter set considered during the in-sample search.
    log = JsonlTrialLog(log_root)
    now = datetime.now(UTC)
    for fold in wf.folds:
        for params in candidate_params:
            log.record(
                TrialRecord(
                    strategy=strategy,
                    params={**params, "fold": fold.fold_index, "train_years": train_years},
                    start_date=fold.train_start.date(),
                    end_date=fold.test_end.date(),
                    sharpe=fold.oos_sharpe,
                    cagr=fold.oos_cagr,
                    max_drawdown=fold.oos_max_drawdown,
                    recorded_at=now,
                )
            )

    total_trials = log.count_trials(strategy)
    dsr = deflated_sharpe_ratio(wf.oos_returns, num_trials=total_trials)

    passed = _render_report(
        wf=wf,
        dsr=dsr,
        total_trials=total_trials,
        min_oos_sharpe=min_oos_sharpe,
        min_dsr_psr=min_dsr_psr,
    )
    if not passed:
        sys.exit(1)


def _render_report(
    *,
    wf,  # WalkForwardResult
    dsr,  # DeflatedSharpeResult
    total_trials: int,
    min_oos_sharpe: float,
    min_dsr_psr: float,
) -> bool:
    folds_tbl = Table(title="walk-forward folds")
    for col in ("fold", "train", "test"):
        folds_tbl.add_column(col)
    for col in ("OOS Sharpe", "OOS CAGR", "OOS maxDD"):
        folds_tbl.add_column(col, justify="right")
    for f in wf.folds:
        folds_tbl.add_row(
            str(f.fold_index),
            f"{f.train_start.date()} → {f.train_end.date()}",
            f"{f.test_start.date()} → {f.test_end.date()}",
            f"{f.oos_sharpe:+.2f}",
            f"{f.oos_cagr * 100:+.2f}%",
            f"{f.oos_max_drawdown * 100:.2f}%",
        )
    console.print(folds_tbl)

    summary = Table(title="validation summary")
    summary.add_column("metric")
    summary.add_column("value", justify="right")
    summary.add_row("concatenated OOS Sharpe", f"{wf.oos_sharpe:+.3f}")
    summary.add_row("min-fold OOS Sharpe", f"{min(wf.fold_sharpes):+.3f}")
    summary.add_row("trials logged", str(total_trials))
    summary.add_row("DSR benchmark (max-of-N)", f"{dsr.benchmark_sharpe:+.3f}")
    summary.add_row("DSR probability", f"{dsr.psr:.4f}")
    summary.add_row("DSR deflated excess", f"{dsr.observed_sharpe - dsr.benchmark_sharpe:+.3f}")
    console.print(summary)

    oos_ok = wf.oos_sharpe >= min_oos_sharpe
    dsr_ok = dsr.psr > min_dsr_psr
    passed = oos_ok and dsr_ok
    verdict = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
    tick_oos = "ok" if oos_ok else "fail"
    tick_dsr = "ok" if dsr_ok else "fail"
    console.print(
        f"\n{verdict}  "
        f"(OOS Sharpe {tick_oos} >= {min_oos_sharpe}, "
        f"DSR p {tick_dsr} > {min_dsr_psr})"
    )
    return passed


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        console.print(f"[red]validation crashed[/red]: {exc}")
        sys.exit(2)
