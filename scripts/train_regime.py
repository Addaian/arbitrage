"""Train the regime HMM and save the model artifact (Wave 15).

Invocation (intended as a weekly cron — systemd timer in prod):

    uv run python scripts/train_regime.py \\
        [--reference SPY] \\
        [--output data/models/regime_<today>.joblib]

Loads the widest cached history for `--reference`, builds the
`RegimeHMM.build_features` bundle (weekly log-return, realized vol,
term-structure proxy), fits a 3-state Gaussian HMM, writes the
artifact, and prints a Rich summary (stress-state identification,
transition matrix, recent stress probabilities).

The output filename defaults to a date-stamped path, and a
`regime_latest.joblib` symlink-style copy is always written so the
LiveRunner can pick up "the most recent artifact" without tracking
timestamps.
"""

from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quant.config import get_settings
from quant.data import CacheKey, ParquetBarCache
from quant.models import RegimeHMM

CONSOLE = Console()

app = typer.Typer(add_completion=False, help="Train the HMM regime classifier.")


def _load_widest_closes(symbol: str, cache_root: Path) -> pd.Series:
    cache = ParquetBarCache(cache_root)
    parquets = sorted((cache_root / symbol).glob("*.parquet"))
    if not parquets:
        raise typer.BadParameter(
            f"no cached bars for {symbol}; run `scripts/backfill.py {symbol}` first"
        )
    widest = min(parquets, key=lambda p: p.stem.split("_")[0])
    start_s, end_s = widest.stem.split("_")
    bars = cache.get(
        CacheKey(symbol=symbol, start=date.fromisoformat(start_s), end=date.fromisoformat(end_s))
    )
    if bars is None:
        raise typer.BadParameter(f"cache miss on {symbol}")
    idx = [pd.Timestamp(b.ts) for b in bars]
    return pd.Series([float(b.close) for b in bars], index=idx, name=symbol)


@app.command()
def train(
    reference: Annotated[
        str, typer.Option("--reference", help="Symbol to derive regime features from")
    ] = "SPY",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", help="Override output path; defaults to data/models/regime_<date>.joblib"
        ),
    ] = None,
    random_state: Annotated[int, typer.Option("--random-state")] = 42,
) -> None:
    settings = get_settings()
    cache_root = settings.quant_data_dir / "parquet"

    closes = _load_widest_closes(reference, cache_root)
    CONSOLE.print(
        f"[bold]data:[/bold] {reference}  "
        f"{closes.index[0].date()} → {closes.index[-1].date()}  ({len(closes)} bars)"
    )

    features = RegimeHMM.build_features(closes)
    CONSOLE.print(f"[bold]features:[/bold] {len(features)} weekly rows")

    hmm = RegimeHMM(random_state=random_state).fit(features)

    models_root = settings.quant_data_dir / "models"
    target = output or (models_root / f"regime_{date.today().isoformat()}.joblib")  # noqa: DTZ011
    hmm.save(target)
    # Keep a stable filename pointing at the most recent artifact.
    latest = models_root / "regime_latest.joblib"
    shutil.copyfile(target, latest)

    CONSOLE.print(f"\n[green]saved[/green] {target}")
    CONSOLE.print(f"[green]alias[/green] {latest}")

    _print_summary(hmm, features)


def _print_summary(hmm: RegimeHMM, features: pd.DataFrame) -> None:
    tm = hmm.transition_matrix
    tbl = Table(title="transition matrix (rows → columns)")
    tbl.add_column("")
    for col in tm.columns:
        tbl.add_column(col, justify="right")
    for idx in tm.index:
        tbl.add_row(idx, *(f"{tm.loc[idx, c]:.3f}" for c in tm.columns))
    CONSOLE.print(tbl)

    proba = hmm.predict_proba(features)
    latest_row = proba.iloc[-1]
    assert hmm.state_labels is not None
    CONSOLE.print(
        "[bold]latest posterior[/bold]  "
        + "  ".join(f"{name}={latest_row[name]:.2f}" for name in proba.columns)
    )
    stress = hmm.stress_probability(features)
    CONSOLE.print(
        f"[bold]stress-probability[/bold]  "
        f"last-4w mean={stress.tail(4).mean():.3f}  "
        f"last-52w mean={stress.tail(52).mean():.3f}"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
