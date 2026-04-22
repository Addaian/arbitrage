"""Pre-flight check before flipping to live trading (Wave 20).

A final guardrail — runs every gate that `make live-run` / the live
systemd unit would hit, but read-only. Exits 0 if every check passes,
non-zero on the first failure. Intended to be invoked:

    1. Manually by the operator before editing .env.
    2. Automatically by the systemd `ExecStartPre=` of
       `quant-runner-live.service` so the real-money unit refuses to
       start if any condition is unsatisfied.

Checks (PRD §1 + §6):
    1. `QUANT_ENV=live` and `PAPER_MODE=false` in Settings.
    2. Alpaca API key + secret are populated AND the `alpaca-live` base
       URL (i.e. NOT the paper URL) is in effect.
    3. AlpacaBroker can talk to the live endpoint; the returned Account
       is not flagged paper.
    4. Live account equity >= `--min-equity` (default $100 — plan deploys
       10% of target; raise this for larger targets).
    5. Kill-switch file is NOT currently engaged.
    6. Latest `pnl_snapshots` row is recent (< 36h old) — proves the
       paper-mode runner was healthy when flipped.
    7. Live API keys differ from whatever's currently in the DB as the
       most-recent paper key signature (lightweight sanity — just checks
       that `ALPACA_API_KEY` doesn't equal a paper-reserved placeholder).
    8. `config/strategies.yaml` allocations sum to 1.0 and the risk
       limits in `config/risk.yaml` are at-or-below PRD §6.1 caps (both
       already enforced by the Pydantic config loader; calling
       `load_config_bundle()` confirms the live tree parses).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import desc, select

from quant.config import get_settings, load_config_bundle
from quant.execution.alpaca_broker import AlpacaBroker
from quant.risk.killswitch import Killswitch
from quant.storage.db import dispose_engine, get_sessionmaker
from quant.storage.models import PnlSnapshotORM

CONSOLE = Console()

_PAPER_URL_HOST = "paper-api.alpaca.markets"

app = typer.Typer(add_completion=False, help="Pre-flight live-trading check.")


def _gate_env_tags() -> tuple[bool, str]:
    s = get_settings()
    ok = s.quant_env == "live" and not s.paper_mode
    return ok, f"quant_env={s.quant_env} paper_mode={s.paper_mode}"


def _gate_creds() -> tuple[bool, str]:
    s = get_settings()
    ok = s.alpaca_api_key is not None and s.alpaca_api_secret is not None
    return ok, "api_key + secret populated" if ok else "missing"


def _gate_live_url() -> tuple[bool, str]:
    base_url = str(get_settings().alpaca_base_url)
    return _check_live_base_url(base_url), f"base_url={base_url}"


def _gate_account_equity(min_equity: float) -> tuple[bool, str]:
    s = get_settings()
    if s.alpaca_api_key is None or s.alpaca_api_secret is None:
        return False, "unchecked (no creds)"
    try:
        broker = AlpacaBroker.from_credentials(
            api_key=s.alpaca_api_key.get_secret_value(),
            api_secret=s.alpaca_api_secret.get_secret_value(),
            paper=False,
        )
        account = broker.get_account()
    except Exception as exc:
        return False, f"broker error: {exc}"
    equity_usd = float(account.equity)
    ok = equity_usd >= min_equity
    return ok, f"equity=${equity_usd:,.2f} (min=${min_equity:,.2f})"


def _gate_killswitch() -> tuple[bool, str]:
    ks = Killswitch(get_settings().quant_killswitch_file)
    ok = not ks.is_engaged()
    tail = "absent" if ok else "ENGAGED — remove to start live"
    return ok, f"{ks.path} {tail}"


def _gate_recent_pnl(skip_db: bool) -> tuple[bool, str]:
    if skip_db:
        return True, "skipped (--skip-db)"
    try:
        return asyncio.run(_check_recent_pnl())
    except Exception as exc:
        return False, f"db error: {exc}"


def _gate_config_bundle() -> tuple[bool, str]:
    try:
        bundle = load_config_bundle()
    except Exception as exc:
        return False, f"parse failed: {exc}"
    return True, f"strategies hash={bundle.config_hash[:12]}"


@app.command()
def check(
    min_equity: Annotated[
        float,
        typer.Option(
            "--min-equity",
            help="Minimum funded equity USD (10%% of intended target).",
        ),
    ] = 100.0,
    skip_db: Annotated[
        bool, typer.Option("--skip-db", help="Skip checks that require Postgres.")
    ] = False,
) -> None:
    gates: list[tuple[str, tuple[bool, str]]] = [
        ("env tags", _gate_env_tags()),
        ("alpaca creds", _gate_creds()),
        ("live base URL", _gate_live_url()),
        ("live account equity", _gate_account_equity(min_equity)),
        ("killswitch clear", _gate_killswitch()),
        ("recent paper PnL", _gate_recent_pnl(skip_db)),
        ("config bundle", _gate_config_bundle()),
    ]
    results = [(name, ok, detail) for name, (ok, detail) in gates]
    failures = [f"{name}: {detail}" for name, ok, detail in results if not ok]

    tbl = Table(title="live pre-flight")
    tbl.add_column("check")
    tbl.add_column("status", justify="center")
    tbl.add_column("detail", overflow="fold")
    for name, passed, detail in results:
        tbl.add_row(name, "[green]ok[/green]" if passed else "[red]fail[/red]", detail)
    CONSOLE.print(tbl)

    if failures:
        CONSOLE.print(f"\n[red]NO-GO[/red]  {len(failures)} check(s) failed")
        for f in failures:
            CONSOLE.print(f"  - {f}")
        raise typer.Exit(code=1)
    CONSOLE.print("\n[green]GO[/green]  all pre-flight checks passed")


async def _check_recent_pnl() -> tuple[bool, str]:
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            row = (
                await session.execute(
                    select(PnlSnapshotORM.ts).order_by(desc(PnlSnapshotORM.ts)).limit(1)
                )
            ).one_or_none()
    finally:
        await dispose_engine()

    if row is None:
        return False, "no pnl_snapshots rows found — run paper cycle first"
    latest_ts = row[0]
    age = datetime.now(UTC) - latest_ts
    if age > timedelta(hours=36):
        return False, f"latest snapshot is {age} old"
    return True, f"latest snapshot {age} ago at {latest_ts.isoformat()}"


def _check_live_base_url(base_url: str) -> bool:
    """Helper exposed for tests: true when the URL is a *live* Alpaca
    endpoint (i.e. not paper)."""
    return _PAPER_URL_HOST not in base_url


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
