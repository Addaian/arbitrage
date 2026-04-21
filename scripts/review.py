"""Daily-review dashboard for the paper run (Wave 9).

Operations tool: look at recent cycles without spinning up a notebook or
waiting for Grafana (Wave 18). Reads from Postgres (via the shared
session factory) and prints four sections:

    * equity curve (last N days, ASCII sparkline + table)
    * latest positions (what the broker held as of most recent cycle)
    * recent orders & fills (last K)
    * recent target signals (last K per strategy)

Usage:
    uv run python scripts/review.py [--days 30] [--limit 10]
"""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from quant.storage.db import dispose_engine, get_sessionmaker
from quant.storage.models import (
    FillORM,
    OrderORM,
    PnlSnapshotORM,
    PositionORM,
    SignalORM,
)

app = typer.Typer(add_completion=False, help="Daily-review dashboard.")
console = Console()


@app.command()
def review(
    days: Annotated[int, typer.Option("--days", help="equity-curve lookback window")] = 30,
    limit: Annotated[int, typer.Option("--limit", help="rows per section")] = 10,
) -> None:
    try:
        asyncio.run(_render(days=days, limit=limit))
    except Exception as exc:
        console.print(f"[red]review failed[/red]: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        asyncio.run(dispose_engine())


async def _render(*, days: int, limit: int) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        pnl_rows = (
            (
                await session.execute(
                    select(PnlSnapshotORM).order_by(PnlSnapshotORM.ts.desc()).limit(days)
                )
            )
            .scalars()
            .all()
        )
        positions = (
            (await session.execute(select(PositionORM).order_by(PositionORM.symbol)))
            .scalars()
            .all()
        )
        recent_orders = (
            (
                await session.execute(
                    select(OrderORM).order_by(OrderORM.created_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        recent_fills = (
            (await session.execute(select(FillORM).order_by(FillORM.ts.desc()).limit(limit)))
            .scalars()
            .all()
        )
        recent_signals = (
            (
                await session.execute(
                    select(SignalORM).order_by(SignalORM.ts.desc(), SignalORM.symbol).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    _print_equity(pnl_rows)
    _print_positions(positions)
    _print_orders_and_fills(recent_orders, recent_fills)
    _print_signals(recent_signals)


def _print_equity(rows: list) -> None:
    if not rows:
        console.print(Panel("[dim]no P&L snapshots yet — run the scheduler first[/dim]"))
        return

    rows = list(reversed(rows))  # chronological
    tbl = Table(title=f"equity curve ({len(rows)} snapshots)")
    tbl.add_column("date")
    tbl.add_column("equity", justify="right")
    tbl.add_column("cash", justify="right")
    tbl.add_column("gross exp.", justify="right")
    tbl.add_column("daily return", justify="right")
    tbl.add_column("drawdown", justify="right")
    for r in rows:
        ret = "" if r.daily_return is None else f"{float(r.daily_return) * 100:+.2f}%"
        dd = "" if r.drawdown is None else f"{float(r.drawdown) * 100:+.2f}%"
        tbl.add_row(
            r.ts.date().isoformat(),
            f"${float(r.equity):,.2f}",
            f"${float(r.cash):,.2f}",
            f"${float(r.gross_exposure):,.2f}",
            ret,
            dd,
        )
    console.print(tbl)

    # ASCII sparkline of equity over the window.
    equities = [float(r.equity) for r in rows]
    console.print(f"spark: {_sparkline(equities)}  ({_pct_change(equities):+.2f}%)")


def _print_positions(rows: list) -> None:
    if not rows:
        console.print(Panel("[dim]no open positions[/dim]"))
        return
    tbl = Table(title=f"positions ({len(rows)})")
    tbl.add_column("symbol")
    tbl.add_column("qty", justify="right")
    tbl.add_column("avg entry", justify="right")
    tbl.add_column("market value", justify="right")
    tbl.add_column("unrealized P&L", justify="right")
    tbl.add_column("as of")
    for p in rows:
        tbl.add_row(
            p.symbol,
            f"{float(p.qty):.4f}",
            f"${float(p.avg_entry_price):,.2f}",
            f"${float(p.market_value):,.2f}",
            f"${float(p.unrealized_pnl):+,.2f}",
            p.as_of.isoformat(timespec="seconds"),
        )
    console.print(tbl)


def _print_orders_and_fills(orders: list, fills: list) -> None:
    o_tbl = Table(title=f"recent orders (last {len(orders)})")
    for col in ("ts", "symbol", "side", "qty", "type", "status", "strategy"):
        o_tbl.add_column(col)
    for o in orders:
        o_tbl.add_row(
            o.created_at.isoformat(timespec="seconds"),
            o.symbol,
            o.side,
            f"{float(o.qty):.4f}",
            o.type,
            o.status,
            o.strategy or "",
        )
    console.print(o_tbl)

    f_tbl = Table(title=f"recent fills (last {len(fills)})")
    for col in ("ts", "symbol", "side", "qty", "price", "commission"):
        f_tbl.add_column(col)
    for f in fills:
        f_tbl.add_row(
            f.ts.isoformat(timespec="seconds"),
            f.symbol,
            f.side,
            f"{float(f.qty):.4f}",
            f"${float(f.price):,.2f}",
            f"${float(f.commission):,.2f}",
        )
    console.print(f_tbl)


def _print_signals(rows: list) -> None:
    if not rows:
        console.print(Panel("[dim]no signals recorded yet[/dim]"))
        return
    tbl = Table(title=f"recent signals (last {len(rows)})")
    for col in ("date", "strategy", "symbol", "direction", "target weight"):
        tbl.add_column(col)
    for s in rows:
        tbl.add_row(
            s.ts.isoformat(),
            s.strategy,
            s.symbol,
            s.direction,
            f"{float(s.target_weight) * 100:+.2f}%",
        )
    console.print(tbl)


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    blocks = " ▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    rng = hi - lo
    if rng == 0:
        return blocks[-1] * len(values)
    return "".join(blocks[int((v - lo) / rng * (len(blocks) - 1))] for v in values)


def _pct_change(values: list[float]) -> float:
    if len(values) < 2 or values[0] == 0:
        return 0.0
    return (values[-1] / values[0] - 1.0) * 100.0


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
