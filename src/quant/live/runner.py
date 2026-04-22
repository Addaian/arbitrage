"""End-to-end daily cycle (PRD §4.2, implementation plan Week 8).

`LiveRunner.run_daily_cycle()` walks the full pipeline:

    closes  -> signal  -> target weights  -> target $ per symbol
         -> current broker state  -> delta orders
         -> submit via OrderManager  -> await fills
         -> reconcile  -> persist to Postgres  -> notify

For Wave 8 this is single-strategy (just `TrendSignal`). The combiner
that stacks multiple strategies arrives in Wave 10 — when it lands, the
cycle shape doesn't change, only how target weights are computed.

Two modes:

* **dry-run** — compute the plan (signals + intended orders) and emit
  a `CycleResult` without hitting the broker or the DB. Used for the
  <10s `python -m quant.live.runner --mode paper --dry-run` CLI
  acceptance criterion and for rehearsing production cycles.
* **live paper** — submit orders through `OrderManager`, poll to
  terminal (or timeout), replace the positions table with broker
  truth, write signals/orders/fills/pnl rows inside a single
  transaction per cycle, and ping Discord.

Reconciliation is broker-authoritative: after the cycle, `get_positions`
is the source of truth. `PositionRepo.replace_all` ensures ghost rows
from earlier cycles disappear. Mismatches between expected and actual
positions are captured in `CycleResult.drift` so downstream alerting
can flag them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quant.config import get_settings
from quant.data.cache import CacheKey, ParquetBarCache
from quant.execution.alpaca_broker import AlpacaBroker
from quant.execution.broker_base import Broker
from quant.execution.order_manager import OrderManager
from quant.execution.paper_broker import PaperBroker
from quant.live.notifier import DiscordNotifier
from quant.monitoring.metrics import (
    record_cycle_error,
    record_cycle_success,
    record_killswitch_state,
    set_position_values,
)
from quant.monitoring.sentry import capture_cycle_exception
from quant.risk.killswitch import Killswitch
from quant.signals.base import SignalStrategy
from quant.signals.trend import TrendSignal
from quant.storage.db import get_sessionmaker
from quant.storage.repos import (
    FillRepo,
    OrderRepo,
    PnlRepo,
    PositionRepo,
    SignalRepo,
)
from quant.types import (
    Account,
    Fill,
    Order,
    OrderSide,
    Position,
    Signal,
    SignalDirection,
)

ClosesProvider = Callable[[], pd.DataFrame]

_ZERO = Decimal(0)
_EPSILON_SHARES = Decimal("0.001")  # don't emit orders smaller than this


@dataclass
class PlannedOrder:
    symbol: str
    side: OrderSide
    qty: Decimal
    target_qty: Decimal
    current_qty: Decimal
    reference_price: Decimal

    def as_order(self, *, strategy: str) -> Order:
        return Order(
            symbol=self.symbol,
            side=self.side,
            qty=self.qty,
            strategy=strategy,
        )


@dataclass
class DriftRecord:
    symbol: str
    expected_qty: Decimal
    actual_qty: Decimal

    @property
    def delta(self) -> Decimal:
        return self.actual_qty - self.expected_qty


@dataclass
class CycleResult:
    as_of: datetime
    strategy: str
    dry_run: bool
    target_weights: dict[str, Decimal]
    planned_orders: list[PlannedOrder]
    submitted_orders: list[Order] = field(default_factory=list)
    fills_by_order: dict[str, list[Fill]] = field(default_factory=dict)
    final_positions: list[Position] = field(default_factory=list)
    drift: list[DriftRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def had_trades(self) -> bool:
        return len(self.submitted_orders) > 0


class LiveRunner:
    def __init__(
        self,
        *,
        broker: Broker,
        order_manager: OrderManager,
        signal: SignalStrategy,
        closes_provider: ClosesProvider,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        notifier: DiscordNotifier | None = None,
        killswitch: Killswitch | None = None,
        dry_run: bool = False,
        wait_for_fill: bool = True,
    ) -> None:
        self._broker = broker
        self._order_manager = order_manager
        self._signal = signal
        self._closes_provider = closes_provider
        self._session_factory = session_factory
        self._notifier = notifier or DiscordNotifier(webhook_url=None)
        self._killswitch = killswitch
        self._dry_run = dry_run
        self._wait_for_fill = wait_for_fill

    async def run_daily_cycle(self, *, as_of: datetime | None = None) -> CycleResult:
        now = as_of or datetime.now(UTC)
        strategy_name = getattr(self._signal, "name", "strategy")
        cycle_start = time.monotonic()

        try:
            self._notifier.cycle_start(strategy_name, now)

            # Killswitch check — if engaged, flatten and exit. No signal
            # computation, no broker round-trips beyond the flatten, no
            # DB writes beyond the resulting position snapshot.
            killswitch_engaged = self._killswitch is not None and self._killswitch.is_engaged()
            record_killswitch_state(killswitch_engaged)
            if killswitch_engaged:
                return await self._flatten_cycle(now=now, strategy_name=strategy_name)

            closes = self._closes_provider()
            if closes.empty:
                raise ValueError("closes_provider returned an empty frame")

            weights_frame = self._signal.target_weights(closes)
            applied = weights_frame.ffill().fillna(0.0)
            if applied.empty:
                raise ValueError("signal produced no weights")

            target_weights = {
                sym: Decimal(str(float(applied.iloc[-1][sym]))) for sym in applied.columns
            }
            latest_prices = {
                sym: Decimal(str(float(closes.iloc[-1][sym]))) for sym in closes.columns
            }

            account = self._broker.get_account()
            current_positions = {p.symbol: p for p in self._broker.get_positions()}
            planned = _plan_orders(
                target_weights=target_weights,
                latest_prices=latest_prices,
                current_positions=current_positions,
                equity=account.equity,
            )

            result = CycleResult(
                as_of=now,
                strategy=strategy_name,
                dry_run=self._dry_run,
                target_weights=target_weights,
                planned_orders=planned,
            )

            if self._dry_run:
                # Compute the plan but don't submit. Still report what WOULD
                # happen so operators can sanity-check the intent.
                result.final_positions = list(current_positions.values())
                self._notifier.cycle_complete(strategy_name, result)
                return result

            for plan in planned:
                order = plan.as_order(strategy=strategy_name)
                outcome = self._order_manager.execute(order, wait_for_fill=self._wait_for_fill)
                result.submitted_orders.append(order)
                result.fills_by_order[str(order.client_order_id)] = outcome.fills

            # Post-cycle reconciliation: broker is source of truth.
            result.final_positions = self._broker.get_positions()
            result.drift = _compute_drift(
                target_weights=target_weights,
                latest_prices=latest_prices,
                equity=account.equity,
                actual_positions=result.final_positions,
            )

            if self._session_factory is not None:
                await self._persist(result, account=account)

            # Metric emission — on the success path only, so cycle_errors
            # tracks failure count cleanly.
            record_cycle_success(
                equity=float(account.equity),
                cash=float(account.cash),
                position_count=len([p for p in result.final_positions if p.qty != 0]),
                duration_seconds=time.monotonic() - cycle_start,
            )
            set_position_values(
                {p.symbol: float(p.market_value) for p in result.final_positions if p.qty != 0}
            )

            self._notifier.cycle_complete(strategy_name, result)
            return result

        except Exception as exc:
            record_cycle_error()
            capture_cycle_exception(exc)
            msg = f"cycle failed: {exc}"
            self._notifier.cycle_error(strategy_name, msg)
            raise

    async def _flatten_cycle(self, *, now: datetime, strategy_name: str) -> CycleResult:
        """Kill-switch response: sell every open position at market and
        mark the cycle complete. Target weights go to the empty dict so
        the reconciliation step sees "zero-target" and stops emitting.

        Orders go through `OrderManager.execute()` without the risk
        validator so that the flatten itself can't be blocked by a
        position-size check. Killswitch itself would be the block there,
        which is self-defeating during a flatten.
        """
        current_positions = self._broker.get_positions()

        planned: list[PlannedOrder] = []
        for pos in current_positions:
            if pos.qty == _ZERO:
                continue
            planned.append(
                PlannedOrder(
                    symbol=pos.symbol,
                    side=OrderSide.SELL if pos.qty > _ZERO else OrderSide.BUY,
                    qty=abs(pos.qty),
                    target_qty=_ZERO,
                    current_qty=pos.qty,
                    reference_price=pos.avg_entry_price,
                )
            )

        result = CycleResult(
            as_of=now,
            strategy=strategy_name,
            dry_run=self._dry_run,
            target_weights={},
            planned_orders=planned,
            errors=["killswitch engaged — flattening"],
        )

        if not self._dry_run:
            for plan in planned:
                order = plan.as_order(strategy=strategy_name)
                # Bypass the order-manager's risk + killswitch hooks by
                # going straight to the broker for a pure close. The
                # manager would refuse the order because the killswitch
                # is engaged, which is exactly not what we want here.
                submit_result = self._broker.submit_order(order)
                result.submitted_orders.append(order)
                result.fills_by_order[str(order.client_order_id)] = self._broker.get_fills(
                    submit_result.order_id
                )

        result.final_positions = self._broker.get_positions()
        self._notifier.cycle_complete(strategy_name, result)
        return result

    async def _persist(self, result: CycleResult, *, account: Account) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            try:
                signal_repo = SignalRepo(session)
                order_repo = OrderRepo(session)
                fill_repo = FillRepo(session)
                position_repo = PositionRepo(session)
                pnl_repo = PnlRepo(session)

                # Signals — one row per symbol in today's target.
                signals = [
                    Signal(
                        strategy=result.strategy,
                        symbol=sym,
                        ts=result.as_of.date(),
                        direction=(
                            SignalDirection.LONG
                            if w > 0
                            else SignalDirection.SHORT
                            if w < 0
                            else SignalDirection.FLAT
                        ),
                        target_weight=float(w),
                        metadata={"cycle_ts": result.as_of.isoformat()},
                    )
                    for sym, w in result.target_weights.items()
                ]
                await signal_repo.record_many(signals)

                # Orders + fills.
                for order in result.submitted_orders:
                    order_pk = await order_repo.record_new(order)
                    for fill in result.fills_by_order.get(str(order.client_order_id), []):
                        await fill_repo.record(fill, order_pk=order_pk)

                # Positions — broker truth, nuke ghosts.
                await position_repo.replace_all(result.final_positions)

                # P&L snapshot.
                await pnl_repo.record_from_account(account)

                await session.commit()
            except Exception:
                await session.rollback()
                raise


def _plan_orders(
    *,
    target_weights: dict[str, Decimal],
    latest_prices: dict[str, Decimal],
    current_positions: dict[str, Position],
    equity: Decimal,
) -> list[PlannedOrder]:
    """Compute per-symbol target qty - current qty. Emit a `PlannedOrder`
    for each non-trivial delta.
    """
    planned: list[PlannedOrder] = []
    all_symbols = set(target_weights) | set(current_positions)
    for sym in sorted(all_symbols):
        price = latest_prices.get(sym)
        if price is None or price <= _ZERO:
            continue
        target_dollar = equity * target_weights.get(sym, _ZERO)
        target_qty = (target_dollar / price).quantize(Decimal("0.000001"))
        current_qty = current_positions[sym].qty if sym in current_positions else _ZERO
        delta = target_qty - current_qty
        if abs(delta) < _EPSILON_SHARES:
            continue
        planned.append(
            PlannedOrder(
                symbol=sym,
                side=OrderSide.BUY if delta > _ZERO else OrderSide.SELL,
                qty=abs(delta),
                target_qty=target_qty,
                current_qty=current_qty,
                reference_price=price,
            )
        )
    return planned


def _compute_drift(
    *,
    target_weights: dict[str, Decimal],
    latest_prices: dict[str, Decimal],
    equity: Decimal,
    actual_positions: list[Position],
) -> list[DriftRecord]:
    actual = {p.symbol: p.qty for p in actual_positions}
    drift: list[DriftRecord] = []
    for sym in set(target_weights) | set(actual):
        price = latest_prices.get(sym, _ZERO)
        if price <= _ZERO:
            continue
        target_qty = (equity * target_weights.get(sym, _ZERO) / price).quantize(Decimal("0.000001"))
        actual_qty = actual.get(sym, _ZERO)
        diff = actual_qty - target_qty
        if abs(diff) >= _EPSILON_SHARES:
            drift.append(DriftRecord(symbol=sym, expected_qty=target_qty, actual_qty=actual_qty))
    return sorted(drift, key=lambda d: d.symbol)


# --- CLI entry point ---------------------------------------------------


def _build_default_runner(
    *,
    broker_kind: str = "paper",
    dry_run: bool = False,
    persist: bool = False,
) -> LiveRunner:
    """Wire a default single-strategy runner from config + the Parquet cache.

    `broker_kind`:
        - "paper"         — in-memory PaperBroker simulator (no network)
        - "alpaca-paper"  — AlpacaBroker against paper-api.alpaca.markets;
                            requires ALPACA_API_KEY + ALPACA_API_SECRET

    `persist=True` wires Postgres persistence via the shared sessionmaker.
    Dry-run implies no persistence regardless.
    """
    settings = get_settings()
    cache_root: Path = settings.quant_data_dir / "parquet"

    risk_symbols = ["SPY", "EFA", "IEF"]
    cash_symbol = "SHY"
    all_symbols = [*risk_symbols, cash_symbol]

    def _closes_provider() -> pd.DataFrame:
        cache = ParquetBarCache(cache_root)
        series: dict[str, pd.Series] = {}
        for sym in all_symbols:
            symbol_dir = cache_root / sym
            parquets = sorted(symbol_dir.glob("*.parquet")) if symbol_dir.exists() else []
            if not parquets:
                raise ValueError(
                    f"no cached bars for {sym}; "
                    f"run `scripts/backfill.py {' '.join(all_symbols)}` first"
                )
            latest = parquets[-1]
            start_s, end_s = latest.stem.split("_")
            bars = cache.get(
                CacheKey(
                    symbol=sym, start=date.fromisoformat(start_s), end=date.fromisoformat(end_s)
                )
            )
            if bars is None:
                raise ValueError(f"cache miss for {sym}")
            series[sym] = pd.Series(
                [float(b.close) for b in bars],
                index=[pd.Timestamp(b.ts) for b in bars],
                name=sym,
            )
        frame = pd.concat(series.values(), axis=1).sort_index()
        return frame.ffill().dropna(how="all")

    broker: Broker
    if broker_kind == "paper":
        broker = PaperBroker(starting_cash=Decimal("100000"))
    elif broker_kind == "alpaca-paper":
        if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
            raise ValueError(
                "broker=alpaca-paper requires ALPACA_API_KEY and ALPACA_API_SECRET in .env"
            )
        broker = AlpacaBroker.from_credentials(
            api_key=settings.alpaca_api_key.get_secret_value(),
            api_secret=settings.alpaca_api_secret.get_secret_value(),
            paper=True,
        )
    else:
        raise ValueError(f"unknown broker kind: {broker_kind!r}")

    om = OrderManager(
        broker,
        poll_timeout=300.0 if broker_kind != "paper" else 0.0,
        poll_interval=2.0 if broker_kind != "paper" else 0.0,
    )
    signal = TrendSignal(lookback_months=10, cash_symbol=cash_symbol)

    webhook = (
        str(settings.discord_webhook_url) if settings.discord_webhook_url is not None else None
    )
    notifier = DiscordNotifier(webhook_url=webhook)

    session_factory = get_sessionmaker() if (persist and not dry_run) else None

    return LiveRunner(
        broker=broker,
        order_manager=om,
        signal=signal,
        closes_provider=_closes_provider,
        session_factory=session_factory,
        notifier=notifier,
        dry_run=dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quant.live.runner")
    parser.add_argument(
        "--broker",
        choices=["paper", "alpaca-paper"],
        default="paper",
        help="paper = local in-memory sim; alpaca-paper = Alpaca paper API",
    )
    parser.add_argument("--dry-run", action="store_true", help="plan only, no submits, no DB")
    parser.add_argument(
        "--persist", action="store_true", help="write signals/orders/fills/pnl to Postgres"
    )
    args = parser.parse_args(argv)

    runner = _build_default_runner(
        broker_kind=args.broker, dry_run=args.dry_run, persist=args.persist
    )
    result = asyncio.run(runner.run_daily_cycle())
    _print_result(result)
    return 0


def _print_result(result: CycleResult) -> None:
    console = Console()
    header = (
        f"[bold]{result.strategy}[/bold]  "
        f"[dim]{result.as_of.isoformat(timespec='seconds')}"
        f"{'  (dry-run)' if result.dry_run else ''}[/dim]"
    )
    console.print(header)

    tw = Table(title="target weights")
    tw.add_column("symbol")
    tw.add_column("weight", justify="right")
    for sym, w in result.target_weights.items():
        tw.add_row(sym, f"{float(w) * 100:+.2f}%")
    console.print(tw)

    if result.planned_orders:
        po = Table(title="planned orders")
        po.add_column("symbol")
        po.add_column("side")
        po.add_column("qty", justify="right")
        po.add_column("target qty", justify="right")
        po.add_column("current qty", justify="right")
        po.add_column("ref price", justify="right")
        for p in result.planned_orders:
            po.add_row(
                p.symbol,
                p.side.value,
                f"{p.qty:.4f}",
                f"{p.target_qty:.4f}",
                f"{p.current_qty:.4f}",
                f"{p.reference_price:.2f}",
            )
        console.print(po)
    else:
        console.print("[dim]no orders to submit (targets match current state)[/dim]")

    if result.drift:
        dr = Table(title="post-cycle drift")
        dr.add_column("symbol")
        dr.add_column("expected", justify="right")
        dr.add_column("actual", justify="right")
        dr.add_column("delta", justify="right")
        for d in result.drift:
            dr.add_row(d.symbol, f"{d.expected_qty:.4f}", f"{d.actual_qty:.4f}", f"{d.delta:+.4f}")
        console.print(dr)


if __name__ == "__main__":
    sys.exit(main())
