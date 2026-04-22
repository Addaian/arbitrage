"""Generic strategy validator (Wave 14).

One command takes any `SignalStrategy`, runs the full Wave-6/13 evaluation
against cached OHLC, and writes a markdown report. Returns exit 0 on
pass / 1 on fail. Designed so adding a new strategy is an hours-not-days
process: drop a signal module into `src/quant/signals/`, run this CLI,
attach the generated markdown to the PR.

Gates (must all pass for exit 0):
    - concatenated OOS Sharpe >= min_oos_sharpe (default 0.4)
    - DSR probability > min_dsr_psr (default 0.5)
    - no single stress-window Sharpe below -2.5 (catastrophic blowup)
    - strategy earns positive Sharpe in at least 2 of 3 vol regimes

Usage examples:

    uv run python scripts/validate_new_strategy.py \\
        --strategy "quant.signals.trend:TrendSignal" \\
        --params '{"lookback_months": 10, "cash_symbol": "SHY"}' \\
        --universe "SPY,EFA,IEF,SHY" \\
        --cash SHY --name trend \\
        --output docs/strategies/trend.md

    uv run python scripts/validate_new_strategy.py \\
        --strategy "quant.signals.mean_reversion:MeanReversionSignal" \\
        --params '{"cash_symbol": "SHY"}' \\
        --universe "SPY,QQQ,EFA,EEM,GLD,IEF,TLT,VNQ,DBC,XLE,SHY" \\
        --cash SHY --name mean_reversion --ohlc \\
        --output docs/strategies/mean_reversion.md
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import pandas as pd
import typer
from rich.console import Console

from quant.backtest import compute_tearsheet, run_backtest
from quant.backtest.deflated_sharpe import annualized_sharpe, deflated_sharpe_ratio
from quant.backtest.walk_forward import walk_forward
from quant.data import CacheKey, ParquetBarCache

CONSOLE = Console()
CACHE_ROOT = Path("data/parquet")
COSTS = {"fees": 0.0, "slippage": 0.0003}

STRESS_WINDOWS: dict[str, tuple[date, date]] = {
    "2008 GFC": (date(2008, 9, 1), date(2009, 3, 31)),
    "2020 COVID": (date(2020, 2, 15), date(2020, 4, 30)),
    "2022 bonds+equity": (date(2022, 1, 1), date(2022, 12, 31)),
    "April 2025": (date(2025, 4, 1), date(2025, 4, 30)),
}


# --- Report types ------------------------------------------------------


@dataclass
class ValidationReport:
    name: str
    strategy_fqn: str
    params: dict[str, Any]
    universe: list[str]
    cash: str
    requires_ohlc: bool
    data_start: date
    data_end: date
    num_bars: int
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    num_rebalances: int
    oos_sharpe: float
    fold_sharpes: list[float]
    dsr_psr: float
    dsr_benchmark: float
    dsr_deflated_excess: float
    stress: dict[str, float]
    regimes: dict[str, float]
    gates: dict[str, bool]
    passed: bool


# --- Data loader -------------------------------------------------------


def _load_ohlc(symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cache = ParquetBarCache(CACHE_ROOT)
    per_symbol: dict[str, pd.DataFrame] = {}
    for s in symbols:
        parquets = sorted((CACHE_ROOT / s).glob("*.parquet"))
        if not parquets:
            raise RuntimeError(
                f"no cached bars for {s!r}; run `scripts/backfill.py {' '.join(symbols)}` first"
            )
        widest = min(parquets, key=lambda p: p.stem.split("_")[0])
        start_s, end_s = widest.stem.split("_")
        bars = cache.get(
            CacheKey(symbol=s, start=date.fromisoformat(start_s), end=date.fromisoformat(end_s))
        )
        if bars is None:
            raise RuntimeError(f"cache miss on {s}")
        idx = [pd.Timestamp(b.ts) for b in bars]
        per_symbol[s] = pd.DataFrame(
            {
                "high": [float(b.high) for b in bars],
                "low": [float(b.low) for b in bars],
                "close": [float(b.close) for b in bars],
            },
            index=idx,
        )

    common: pd.DatetimeIndex | None = None
    for df in per_symbol.values():
        common = df.index if common is None else common.intersection(df.index)
    assert common is not None
    closes = pd.DataFrame({s: per_symbol[s]["close"].loc[common] for s in symbols})
    highs = pd.DataFrame({s: per_symbol[s]["high"].loc[common] for s in symbols})
    lows = pd.DataFrame({s: per_symbol[s]["low"].loc[common] for s in symbols})
    return closes.sort_index(), highs.sort_index(), lows.sort_index()


# --- Strategy loader + adapter ----------------------------------------


def _import_strategy(fqn: str, params: dict[str, Any]) -> Any:
    module_name, _, class_name = fqn.partition(":")
    if not class_name:
        raise ValueError(f"strategy must be 'module:ClassName', got {fqn!r}")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls(**params)


def _make_weights_fn(strategy: Any, highs: pd.DataFrame, lows: pd.DataFrame, ohlc: bool):
    """Return a `target_weights(closes) -> weights` callable that handles
    both close-only and OHLC signal signatures uniformly.
    """
    if not ohlc:
        return strategy.target_weights

    def _weights(closes: pd.DataFrame) -> pd.DataFrame:
        h = highs.reindex(index=closes.index, columns=closes.columns)
        lo = lows.reindex(index=closes.index, columns=closes.columns)
        return strategy.target_weights(closes, h, lo)

    return _weights


# --- Evaluation -------------------------------------------------------


def _stress_sharpes(returns: pd.Series) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, (start, end) in STRESS_WINDOWS.items():
        mask = (returns.index >= pd.Timestamp(start)) & (returns.index <= pd.Timestamp(end))
        window = returns.loc[mask]
        out[name] = annualized_sharpe(window) if len(window) >= 20 else float("nan")
    return out


def _regime_sharpes(returns: pd.Series, closes: pd.DataFrame) -> dict[str, float]:
    spy_ret = (
        closes["SPY"].pct_change() if "SPY" in closes.columns else closes.iloc[:, 0].pct_change()
    )
    vol = spy_ret.rolling(60).std() * (252**0.5)
    vol = vol.reindex(returns.index)
    valid = vol.dropna()
    if valid.empty:
        return {"low": float("nan"), "mid": float("nan"), "high": float("nan")}
    low_q = valid.quantile(1 / 3)
    high_q = valid.quantile(2 / 3)
    out: dict[str, float] = {}
    for label, mask in [
        ("low", vol <= low_q),
        ("mid", (vol > low_q) & (vol <= high_q)),
        ("high", vol > high_q),
    ]:
        window = returns.loc[mask.fillna(False)]
        out[label] = annualized_sharpe(window) if len(window) > 30 else float("nan")
    return out


def _run_validation(
    *,
    name: str,
    strategy_fqn: str,
    params: dict[str, Any],
    universe: list[str],
    cash: str,
    ohlc: bool,
    min_oos_sharpe: float,
    min_dsr_psr: float,
    max_stress_loss_sharpe: float,
    min_positive_regimes: int,
) -> ValidationReport:
    closes, highs, lows = _load_ohlc(universe)

    strategy = _import_strategy(strategy_fqn, params)
    weights_fn = _make_weights_fn(strategy, highs, lows, ohlc)

    weights = weights_fn(closes)
    result = run_backtest(closes, weights, **COSTS)
    ts = compute_tearsheet(result)

    # Walk-forward: wrap strategy in an adapter that matches the close-only
    # contract the harness expects, but uses the OHLC weights fn if needed.
    strategy_name = name

    class _Adapter:
        def __init__(self) -> None:
            self.name = strategy_name

        def target_weights(self, sub_closes: pd.DataFrame) -> pd.DataFrame:
            return weights_fn(sub_closes)

    wf = walk_forward(closes, lambda _t: _Adapter(), train_years=10, test_years=2, **COSTS)
    dsr = deflated_sharpe_ratio(wf.oos_returns, num_trials=wf.num_folds)

    stress = _stress_sharpes(result.returns)
    regimes = _regime_sharpes(result.returns, closes)

    gates = {
        "oos_sharpe_ge_min": wf.oos_sharpe >= min_oos_sharpe,
        "dsr_psr_gt_min": dsr.psr > min_dsr_psr,
        "no_stress_blowup": all(
            np.isnan(v) or v >= max_stress_loss_sharpe for v in stress.values()
        ),
        "positive_in_multiple_regimes": (
            sum(1 for v in regimes.values() if not np.isnan(v) and v > 0) >= min_positive_regimes
        ),
    }
    passed = all(gates.values())

    return ValidationReport(
        name=name,
        strategy_fqn=strategy_fqn,
        params=params,
        universe=universe,
        cash=cash,
        requires_ohlc=ohlc,
        data_start=closes.index[0].date(),
        data_end=closes.index[-1].date(),
        num_bars=len(closes),
        cagr=ts.cagr,
        sharpe=ts.sharpe,
        sortino=ts.sortino,
        max_drawdown=ts.max_drawdown,
        num_rebalances=ts.num_rebalances,
        oos_sharpe=wf.oos_sharpe,
        fold_sharpes=wf.fold_sharpes,
        dsr_psr=dsr.psr,
        dsr_benchmark=dsr.benchmark_sharpe,
        dsr_deflated_excess=dsr.observed_sharpe - dsr.benchmark_sharpe,
        stress=stress,
        regimes=regimes,
        gates=gates,
        passed=passed,
    )


# --- Markdown renderer ------------------------------------------------


def _render_markdown(report: ValidationReport) -> str:
    verdict = "**PASS**" if report.passed else "**FAIL**"
    lines: list[str] = [
        f"# {report.name} — validation report",
        "",
        f"**Verdict:** {verdict}",
        "**Generated by:** `scripts/validate_new_strategy.py`",
        f"**Strategy:** `{report.strategy_fqn}`",
        "**Cost model:** 0 bp commission + 3 bp slippage",
        f"**Data window:** {report.data_start} → {report.data_end} ({report.num_bars} bars)",
        "",
        "## Hypothesis",
        "",
        "<!-- Describe the market inefficiency this strategy targets, the "
        "research it's grounded in, and the mechanism by which it should "
        "earn a risk premium. Fill this in by hand before opening the PR. -->",
        "",
        "## Configuration",
        "",
        f"- **Universe:** `{', '.join(report.universe)}`",
        f"- **Cash symbol:** `{report.cash}`",
        f"- **Requires OHLC:** {report.requires_ohlc}",
        f"- **Params:** `{json.dumps(report.params, sort_keys=True)}`",
        "",
        "## Full-history backtest",
        "",
        "| metric        | value |",
        "|---            |---    |",
        f"| CAGR          | {report.cagr * 100:+.2f}% |",
        f"| Sharpe        | {report.sharpe:.3f} |",
        f"| Sortino       | {report.sortino:.3f} |",
        f"| max drawdown  | {report.max_drawdown * 100:.2f}% |",
        f"| rebalances    | {report.num_rebalances} |",
        "",
        "## Walk-forward + Deflated Sharpe",
        "",
        "| metric                   | value |",
        "|---                       |---    |",
        f"| concatenated OOS Sharpe  | {report.oos_sharpe:+.3f} |",
        f"| per-fold Sharpes         | `{[round(s, 3) for s in report.fold_sharpes]}` |",
        f"| DSR probability          | {report.dsr_psr:.3f} |",
        f"| DSR benchmark (max-of-N) | {report.dsr_benchmark:+.3f} |",
        f"| DSR deflated excess      | {report.dsr_deflated_excess:+.3f} |",
        "",
        "## Stress windows",
        "",
        "| window | annualized Sharpe |",
        "|---     |---                |",
    ]
    for win, sh in report.stress.items():
        v = "NA" if np.isnan(sh) else f"{sh:+.2f}"
        lines.append(f"| {win} | {v} |")
    lines += [
        "",
        "## Vol regimes (SPY 60d vol terciles)",
        "",
        "| regime | annualized Sharpe |",
        "|---     |---                |",
    ]
    for regime, sh in report.regimes.items():
        v = "NA" if np.isnan(sh) else f"{sh:+.2f}"
        lines.append(f"| {regime} | {v} |")
    lines += [
        "",
        "## Gate checks",
        "",
        "| gate | pass |",
        "|---   |---   |",
    ]
    for gate, ok in report.gates.items():
        lines.append(f"| {gate} | {'✓' if ok else '✗'} |")
    lines += [
        "",
        "## Verdict",
        "",
        f"{verdict}.",
        "",
    ]
    if not report.passed:
        lines += [
            "The strategy failed one or more validation gates above. Do **not**",
            "wire it into `config/strategies.yaml`. Either tune the parameters,",
            "narrow the universe, or retire the hypothesis.",
            "",
        ]
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------


app = typer.Typer(add_completion=False, help="Validate a new strategy end-to-end.")


@app.command()
def validate(
    strategy: Annotated[
        str, typer.Option("--strategy", help="Import path, e.g. 'quant.signals.trend:TrendSignal'")
    ],
    name: Annotated[str, typer.Option("--name", help="Short identifier for filenames")],
    universe: Annotated[str, typer.Option("--universe", help="Comma-separated symbols")],
    cash: Annotated[str, typer.Option("--cash", help="Cash symbol (must be in --universe)")],
    params: Annotated[str, typer.Option("--params", help="JSON dict of constructor kwargs")] = "{}",
    ohlc: Annotated[
        bool, typer.Option("--ohlc", help="Signal needs highs+lows alongside closes")
    ] = False,
    output: Annotated[
        Path | None, typer.Option("--output", help="Write markdown to this path")
    ] = None,
    min_oos_sharpe: Annotated[float, typer.Option("--min-oos-sharpe")] = 0.4,
    min_dsr_psr: Annotated[float, typer.Option("--min-dsr-psr")] = 0.5,
    max_stress_loss_sharpe: Annotated[
        float, typer.Option("--max-stress-loss-sharpe", help="Reject if any window < this")
    ] = -2.5,
    min_positive_regimes: Annotated[int, typer.Option("--min-positive-regimes")] = 2,
) -> None:
    try:
        params_dict: dict[str, Any] = json.loads(params)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--params must be valid JSON: {exc}") from exc
    if not isinstance(params_dict, dict):
        raise typer.BadParameter("--params must be a JSON object")

    universe_list = [s.strip() for s in universe.split(",") if s.strip()]
    if cash not in universe_list:
        raise typer.BadParameter(f"--cash {cash!r} must be in --universe")

    report = _run_validation(
        name=name,
        strategy_fqn=strategy,
        params=params_dict,
        universe=universe_list,
        cash=cash,
        ohlc=ohlc,
        min_oos_sharpe=min_oos_sharpe,
        min_dsr_psr=min_dsr_psr,
        max_stress_loss_sharpe=max_stress_loss_sharpe,
        min_positive_regimes=min_positive_regimes,
    )

    markdown = _render_markdown(report)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        CONSOLE.print(f"[dim]wrote → {output}[/dim]")

    # Summary to stdout.
    verdict_color = "green" if report.passed else "red"
    CONSOLE.print(
        f"\n[{verdict_color}]{'PASS' if report.passed else 'FAIL'}[/{verdict_color}]  "
        f"{report.name}: OOS Sharpe {report.oos_sharpe:+.3f}, "
        f"DSR PSR {report.dsr_psr:.3f}, "
        f"gates {sum(report.gates.values())}/{len(report.gates)} cleared"
    )
    if not report.passed:
        for gate, ok in report.gates.items():
            if not ok:
                CONSOLE.print(f"  [red]✗[/red] {gate}")
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
