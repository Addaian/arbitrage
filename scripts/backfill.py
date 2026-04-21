"""Backfill historical daily bars for a set of symbols.

Acceptance (PRD / implementation plan Week 3):
    uv run python scripts/backfill.py SPY QQQ EFA EEM GLD IEF TLT VNQ DBC XLE --years 20

Second run with the same args should finish in <5s entirely from the
Parquet cache (no network). Malformed bars get dropped with a report.

Optionally writes the same bars to Postgres (`--write-db`) using the async
BarRepo — useful for the Live runner path; skip for pure backtest use.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from quant.config import get_settings
from quant.data import (
    AlpacaLoader,
    BarLoader,
    CacheKey,
    ParquetBarCache,
    YFinanceLoader,
    require_adjusted,
    validate_bars,
)
from quant.storage import BarRepo, session_scope

app = typer.Typer(help="Backfill historical daily bars to the Parquet cache.", add_completion=False)
console = Console()


def _build_loader(source: str) -> BarLoader:
    if source == "yfinance":
        return YFinanceLoader()
    if source == "alpaca":
        settings = get_settings()
        if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
            raise typer.BadParameter("Alpaca credentials missing in .env")
        return AlpacaLoader(settings.alpaca_api_key, settings.alpaca_api_secret)
    raise typer.BadParameter(f"unknown source: {source}")


async def _write_db(bars: list) -> int:
    async with session_scope() as session:
        return await BarRepo(session).upsert_many(bars)


@app.command()
def backfill(
    symbols: Annotated[list[str], typer.Argument(help="Ticker symbols (uppercase).")],
    years: Annotated[int, typer.Option("--years", min=1, max=30)] = 20,
    source: Annotated[str, typer.Option(help="Loader: yfinance or alpaca.")] = "yfinance",
    write_db: Annotated[bool, typer.Option("--write-db/--no-write-db")] = False,
    cache_dir: Annotated[
        Path | None,
        typer.Option("--cache-dir", help="Override cache root."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Invalidate cache first.")] = False,
) -> None:
    """Fetch, validate, cache (and optionally DB-persist) daily bars."""
    today = date.today()  # noqa: DTZ011 — market session dates are local
    start = today - timedelta(days=365 * years + 7)  # +7 day buffer for weekends
    cache_root = cache_dir or (get_settings().quant_data_dir / "parquet")
    cache = ParquetBarCache(cache_root)
    loader = _build_loader(source)

    totals = {"symbols": 0, "cache_hits": 0, "fetched": 0, "dropped": 0, "db_rows": 0}
    table = Table(title=f"Backfill {source} · {start} → {today}")
    for col in ("symbol", "bars", "source", "dropped"):
        table.add_column(col)

    for sym in [s.upper() for s in symbols]:
        key = CacheKey(symbol=sym, start=start, end=today)
        if force:
            cache.invalidate(key)

        hit = cache.exists(key)
        bars = cache.get(key) if hit else loader.fetch(sym, start, today)
        origin = "cache" if hit else source

        if not bars:
            logger.warning("no bars returned for {}", sym)
            table.add_row(sym, "0", origin, "-")
            totals["symbols"] += 1
            continue

        require_adjusted(bars)
        cleaned, report = validate_bars(bars)

        if not hit:
            cache.put(key, cleaned)
            totals["fetched"] += len(cleaned)
        else:
            totals["cache_hits"] += 1

        totals["symbols"] += 1
        totals["dropped"] += report.dropped

        if write_db:
            n = asyncio.run(_write_db(cleaned))
            totals["db_rows"] += n

        table.add_row(sym, str(len(cleaned)), origin, str(report.dropped))

    console.print(table)
    console.print(
        f"[green]ok[/green] symbols={totals['symbols']} "
        f"cache_hits={totals['cache_hits']} "
        f"fetched_rows={totals['fetched']} "
        f"dropped={totals['dropped']} "
        f"db_rows={totals['db_rows']}"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover — CLI boundary
        console.print(f"[red]failed[/red]: {exc}")
        sys.exit(1)
