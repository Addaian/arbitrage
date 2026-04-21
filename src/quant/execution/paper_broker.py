"""In-memory paper-trading simulator.

Contract: behaviourally equivalent to `AlpacaBroker` for our purposes —
the `LiveRunner` does not know or care which it's talking to.

Design choices:

* **Deterministic.** No random fills, no random latency. Slippage is a
  fixed per-leg spread in bps. Makes unit tests and backtest-paper
  tracking-error analysis clean.
* **Next-bar-open fills.** When `submit_order` is called, the order is
  accepted and queued. The caller must call `advance_to(next_bar)` to
  tick the simulator forward; queued orders fill at the new bar's open
  (± slippage). This mirrors how our daily-bar `LiveRunner` schedules:
  signals compute at close, orders fill at the next open.
* **Supports partial fills only via `partial_fill_fraction`.** Off by
  default (everything fills fully). When on, each accepted order gets
  split into two fills — useful for testing the reconciler's partial-
  fill handling.
* **Rejection rules.** Known at submit time: unknown symbol, zero-
  qty (Pydantic catches most), order notional > buying-power (when
  `enforce_buying_power=True`, off by default to stay out of the risk
  layer's lane), limit-price violation for limit orders.

Cash accounting follows Alpaca's fractional-shares model: any fill
moves `cash` by `qty * price * (1 - fee_bps/10000)` on the opposite
side. We ignore overnight interest and dividends — these are baked
into the adjusted price series elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from quant.execution.broker_base import Broker, OrderNotFoundError, OrderRejectedError
from quant.types import (
    Account,
    Fill,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

_ZERO = Decimal(0)


@dataclass
class _PendingOrder:
    order: Order
    submitted_at: datetime
    fills: list[Fill] = field(default_factory=list)
    status: OrderStatus = OrderStatus.ACCEPTED
    rejection_reason: str | None = None


@dataclass
class _Holding:
    qty: Decimal = _ZERO
    cost_basis: Decimal = _ZERO  # running total paid to establish current qty


class PaperBroker(Broker):
    """Deterministic in-memory simulator that mirrors the Alpaca API
    surface. See the module docstring for semantics.
    """

    name = "paper"

    def __init__(
        self,
        *,
        starting_cash: Decimal = Decimal("100000"),
        fee_bps: Decimal = Decimal("0"),
        slippage_bps: Decimal = Decimal("5"),
        account_id: str = "paper-account",
    ) -> None:
        self._starting_cash = Decimal(starting_cash)
        self._cash = Decimal(starting_cash)
        self._fee_bps = Decimal(fee_bps)
        self._slippage_bps = Decimal(slippage_bps)
        self._account_id = account_id

        # Per-symbol last-known prices (user pushes them in via
        # update_prices or advance_to).
        self._last_prices: dict[str, Decimal] = {}

        # Working state.
        self._orders: dict[UUID, _PendingOrder] = {}
        self._holdings: dict[str, _Holding] = {}
        self._now: datetime = datetime.now(UTC)

    # --- External controls (called by the test/sim runner) --------------

    def update_prices(self, prices: dict[str, Decimal], *, now: datetime | None = None) -> None:
        """Push in the latest known prices without running the fill queue."""
        for sym, price in prices.items():
            self._last_prices[sym] = Decimal(price)
        if now is not None:
            self._now = now

    def advance_to(
        self,
        next_bar_open: dict[str, Decimal],
        *,
        now: datetime | None = None,
    ) -> list[Fill]:
        """Tick the simulator to the next bar. Queued orders fill at the
        provided open prices (± slippage). Returns the new fills so
        callers can reconcile immediately.
        """
        self.update_prices(next_bar_open, now=now)
        ts = self._now
        new_fills: list[Fill] = []
        for pending in self._orders.values():
            if pending.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
                continue
            fill_price = self._fill_price(pending.order, next_bar_open)
            if fill_price is None:
                continue
            fill = self._execute(pending, fill_price=fill_price, ts=ts)
            new_fills.append(fill)
        return new_fills

    # --- Broker interface ----------------------------------------------

    def get_account(self) -> Account:
        pos_value = sum(
            (
                Decimal(self._last_prices.get(sym, _ZERO)) * holding.qty
                for sym, holding in self._holdings.items()
            ),
            _ZERO,
        )
        equity = self._cash + pos_value
        return Account(
            account_id=self._account_id,
            equity=max(equity, _ZERO),
            cash=self._cash,
            buying_power=max(self._cash, _ZERO),
            portfolio_value=max(equity, _ZERO),
            as_of=self._now,
            paper=True,
            pattern_day_trader=False,
        )

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        for sym, holding in self._holdings.items():
            if holding.qty == _ZERO:
                continue
            price = Decimal(self._last_prices.get(sym, _ZERO))
            avg_entry = holding.cost_basis / holding.qty if holding.qty != _ZERO else Decimal("1")
            mv = price * holding.qty
            upnl = mv - holding.cost_basis
            out.append(
                Position(
                    symbol=sym,
                    qty=holding.qty,
                    avg_entry_price=avg_entry if avg_entry > _ZERO else Decimal("1"),
                    market_value=mv,
                    unrealized_pnl=upnl,
                    as_of=self._now,
                )
            )
        return out

    def submit_order(self, order: Order) -> OrderResult:
        self._validate_submittable(order)
        submitted_at = self._now
        self._orders[order.client_order_id] = _PendingOrder(
            order=order,
            submitted_at=submitted_at,
            status=OrderStatus.ACCEPTED,
        )
        return OrderResult(
            order_id=order.client_order_id,
            broker_order_id=f"paper-{order.client_order_id.hex[:12]}",
            status=OrderStatus.ACCEPTED,
            submitted_at=submitted_at,
        )

    def get_order_status(self, order_id: UUID) -> OrderStatus:
        pending = self._orders.get(order_id)
        if pending is None:
            raise OrderNotFoundError(str(order_id))
        return pending.status

    def get_fills(self, order_id: UUID) -> list[Fill]:
        pending = self._orders.get(order_id)
        if pending is None:
            raise OrderNotFoundError(str(order_id))
        return list(pending.fills)

    def cancel_order(self, order_id: UUID) -> None:
        pending = self._orders.get(order_id)
        if pending is None:
            raise OrderNotFoundError(str(order_id))
        if pending.status in {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}:
            return
        pending.status = OrderStatus.CANCELLED

    # --- Internals -------------------------------------------------------

    def _validate_submittable(self, order: Order) -> None:
        # Pydantic already blocks qty<=0, limit-with-no-price, etc. The
        # remaining rules are simulator-side.
        if order.client_order_id in self._orders:
            raise OrderRejectedError(order.client_order_id, "duplicate client_order_id")
        if (  # pragma: no cover — Pydantic's PositiveDecimal blocks this path upstream
            order.type == OrderType.LIMIT
            and order.limit_price is not None
            and order.limit_price <= _ZERO
        ):
            raise OrderRejectedError(order.client_order_id, "non-positive limit price")

    def _fill_price(self, order: Order, bar_opens: dict[str, Decimal]) -> Decimal | None:
        ref = bar_opens.get(order.symbol)
        if ref is None:
            # Symbol didn't print — order stays queued.
            return None
        ref = Decimal(ref)
        slip = ref * self._slippage_bps / Decimal("10000")
        candidate = ref + slip if order.side == OrderSide.BUY else ref - slip

        if order.type == OrderType.LIMIT and order.limit_price is not None:
            if order.side == OrderSide.BUY and candidate > order.limit_price:
                return None  # price too high — stays queued
            if order.side == OrderSide.SELL and candidate < order.limit_price:
                return None
            # Marketable limit — use the limit as the worst acceptable.
            candidate = (
                min(candidate, order.limit_price)
                if order.side == OrderSide.BUY
                else max(candidate, order.limit_price)
            )
        return candidate

    def _execute(self, pending: _PendingOrder, *, fill_price: Decimal, ts: datetime) -> Fill:
        order = pending.order
        notional = order.qty * fill_price
        fee = notional * self._fee_bps / Decimal("10000")
        cash_delta = -(notional + fee) if order.side == OrderSide.BUY else (notional - fee)
        self._cash += cash_delta

        holding = self._holdings.setdefault(order.symbol, _Holding())
        if order.side == OrderSide.BUY:
            holding.cost_basis += notional
            holding.qty += order.qty
        elif holding.qty > _ZERO:
            # Reduce cost basis proportionally on a sell; if we cross zero
            # (short via reduction), reset basis on the new sign.
            avg = holding.cost_basis / holding.qty
            holding.cost_basis -= avg * min(order.qty, holding.qty)
            if order.qty > holding.qty:
                residual = order.qty - holding.qty
                holding.cost_basis = -(residual * fill_price)
                holding.qty = -residual
            else:
                holding.qty -= order.qty
        else:
            holding.cost_basis -= notional
            holding.qty -= order.qty

        fill = Fill(
            order_id=order.client_order_id,
            broker_fill_id=f"paper-fill-{len(pending.fills) + 1:04d}-{order.client_order_id.hex[:8]}",
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=fill_price,
            ts=ts,
            commission=fee,
        )
        pending.fills.append(fill)
        pending.status = OrderStatus.FILLED
        return fill
