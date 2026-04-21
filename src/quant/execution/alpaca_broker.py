"""Alpaca broker adapter — wraps `alpaca-py`'s `TradingClient`.

Translates between our domain types (`Order`, `Fill`, `Position`,
`Account`) and Alpaca's SDK shapes. Holds no state: all reads hit the
broker and convert, all writes go through `submit_order` /
`cancel_order`.

Transient-failure handling is the caller's concern — `OrderManager`
applies the retry policy and this adapter just surfaces
`TransientBrokerError` where the SDK raises network-like exceptions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar, cast
from uuid import UUID

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaSide
from alpaca.trading.enums import OrderStatus as AlpacaStatus
from alpaca.trading.enums import TimeInForce as AlpacaTIF
from alpaca.trading.models import Order as AlpacaOrder
from alpaca.trading.models import Position as AlpacaPosition
from alpaca.trading.models import TradeAccount
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
)

from quant.execution.broker_base import (
    Broker,
    BrokerError,
    OrderNotFoundError,
    OrderRejectedError,
    TransientBrokerError,
)
from quant.types import (
    Account,
    Fill,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)

_T = TypeVar("_T")


_STATUS_MAP: dict[AlpacaStatus, OrderStatus] = {
    AlpacaStatus.NEW: OrderStatus.ACCEPTED,
    AlpacaStatus.ACCEPTED: OrderStatus.ACCEPTED,
    AlpacaStatus.ACCEPTED_FOR_BIDDING: OrderStatus.ACCEPTED,
    AlpacaStatus.PENDING_NEW: OrderStatus.NEW,
    AlpacaStatus.PENDING_REVIEW: OrderStatus.NEW,
    AlpacaStatus.HELD: OrderStatus.ACCEPTED,
    AlpacaStatus.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    AlpacaStatus.FILLED: OrderStatus.FILLED,
    AlpacaStatus.DONE_FOR_DAY: OrderStatus.EXPIRED,
    AlpacaStatus.CANCELED: OrderStatus.CANCELLED,
    AlpacaStatus.PENDING_CANCEL: OrderStatus.CANCELLED,
    AlpacaStatus.EXPIRED: OrderStatus.EXPIRED,
    AlpacaStatus.REJECTED: OrderStatus.REJECTED,
    AlpacaStatus.SUSPENDED: OrderStatus.REJECTED,
    AlpacaStatus.STOPPED: OrderStatus.CANCELLED,
    AlpacaStatus.REPLACED: OrderStatus.CANCELLED,
    AlpacaStatus.PENDING_REPLACE: OrderStatus.ACCEPTED,
    AlpacaStatus.CALCULATED: OrderStatus.ACCEPTED,
}

_TIF_MAP: dict[TimeInForce, AlpacaTIF] = {
    TimeInForce.DAY: AlpacaTIF.DAY,
    TimeInForce.GTC: AlpacaTIF.GTC,
    TimeInForce.IOC: AlpacaTIF.IOC,
    TimeInForce.FOK: AlpacaTIF.FOK,
}


class AlpacaBroker(Broker):
    """Thin adapter over `alpaca-py`. Safe to share across threads as
    long as `TradingClient` is — which it is per alpaca-py's docs.
    """

    name = "alpaca"

    def __init__(self, client: TradingClient, *, paper: bool = True) -> None:
        self._client = client
        self._paper = paper

    @classmethod
    def from_credentials(
        cls,
        *,
        api_key: str,
        api_secret: str,
        paper: bool = True,
    ) -> AlpacaBroker:
        client = TradingClient(api_key=api_key, secret_key=api_secret, paper=paper)
        return cls(client, paper=paper)

    # --- Broker interface ----------------------------------------------

    def get_account(self) -> Account:
        raw = cast(TradeAccount, self._call(self._client.get_account))
        return Account(
            account_id=str(raw.id),
            equity=Decimal(str(raw.equity)),
            cash=Decimal(str(raw.cash)),
            buying_power=Decimal(str(raw.buying_power)),
            portfolio_value=Decimal(str(raw.portfolio_value)),
            as_of=datetime.now(UTC),
            paper=self._paper,
            pattern_day_trader=bool(raw.pattern_day_trader),
        )

    def get_positions(self) -> list[Position]:
        raw_positions = cast(list[AlpacaPosition], self._call(self._client.get_all_positions))
        now = datetime.now(UTC)
        out: list[Position] = []
        for p in raw_positions:
            qty = Decimal(str(p.qty))
            if qty == 0:
                continue
            out.append(
                Position(
                    symbol=p.symbol,
                    qty=qty,
                    avg_entry_price=Decimal(str(p.avg_entry_price)),
                    market_value=Decimal(str(p.market_value)),
                    unrealized_pnl=Decimal(str(p.unrealized_pl)),
                    as_of=now,
                )
            )
        return out

    def submit_order(self, order: Order) -> OrderResult:
        req = self._to_alpaca_request(order)
        try:
            raw = cast(AlpacaOrder, self._client.submit_order(req))
        except APIError as exc:
            # Alpaca surfaces rejections with 4xx status codes. Rejections
            # are non-retryable; transient codes (5xx, network) we bubble
            # up for the OrderManager to retry.
            if _is_client_error(exc):
                raise OrderRejectedError(order.client_order_id, str(exc)) from exc
            raise TransientBrokerError(f"alpaca submit failed: {exc}") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise TransientBrokerError(str(exc)) from exc

        status = _STATUS_MAP.get(raw.status, OrderStatus.ACCEPTED)
        return OrderResult(
            order_id=order.client_order_id,
            broker_order_id=str(raw.id),
            status=status,
            submitted_at=datetime.now(UTC),
            reason=None,
        )

    def get_order_status(self, order_id: UUID) -> OrderStatus:
        raw = self._fetch_order(order_id)
        return _STATUS_MAP.get(raw.status, OrderStatus.ACCEPTED)

    def get_fills(self, order_id: UUID) -> list[Fill]:
        raw = self._fetch_order(order_id)
        # alpaca-py attaches legs/fills inline via `filled_qty` /
        # `filled_avg_price` — Alpaca doesn't expose individual fills
        # through this endpoint. We synthesize one aggregate Fill.
        filled_qty = Decimal(str(raw.filled_qty or "0"))
        if filled_qty == 0:
            return []
        filled_price = Decimal(str(raw.filled_avg_price or "0"))
        filled_at = raw.filled_at or datetime.now(UTC)
        side = OrderSide.BUY if raw.side == AlpacaSide.BUY else OrderSide.SELL
        if raw.symbol is None:  # pragma: no cover — every live order has a symbol
            raise BrokerError(f"alpaca order {raw.id} has no symbol")
        return [
            Fill(
                order_id=order_id,
                broker_fill_id=f"{raw.id}-agg",
                symbol=raw.symbol,
                side=side,
                qty=filled_qty,
                price=filled_price,
                ts=filled_at,
                commission=Decimal("0"),
            )
        ]

    def cancel_order(self, order_id: UUID) -> None:
        raw = self._fetch_order(order_id)
        try:
            self._client.cancel_order_by_id(str(raw.id))
        except APIError as exc:
            if _is_client_error(exc):
                # Already terminal — treat as idempotent success.
                return
            raise TransientBrokerError(f"alpaca cancel failed: {exc}") from exc

    # --- Helpers --------------------------------------------------------

    def _fetch_order(self, order_id: UUID) -> AlpacaOrder:
        try:
            return cast(AlpacaOrder, self._client.get_order_by_client_id(str(order_id)))
        except APIError as exc:
            if _is_client_error(exc):
                raise OrderNotFoundError(str(order_id)) from exc
            raise TransientBrokerError(str(exc)) from exc

    def _to_alpaca_request(self, order: Order) -> MarketOrderRequest | LimitOrderRequest:
        side = AlpacaSide.BUY if order.side == OrderSide.BUY else AlpacaSide.SELL
        tif = _TIF_MAP[order.time_in_force]
        if order.type == OrderType.LIMIT:
            if order.limit_price is None:  # pragma: no cover — Pydantic guards this
                raise BrokerError("limit order without limit_price")
            return LimitOrderRequest(
                symbol=order.symbol,
                qty=float(order.qty),
                side=side,
                time_in_force=tif,
                client_order_id=str(order.client_order_id),
                limit_price=float(order.limit_price),
            )
        # Market / MOO / MOC all go through MarketOrderRequest; TIF encodes
        # the session alignment for MOC / MOO.
        return MarketOrderRequest(
            symbol=order.symbol,
            qty=float(order.qty),
            side=side,
            time_in_force=tif,
            client_order_id=str(order.client_order_id),
        )

    @staticmethod
    def _call(fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except APIError as exc:
            if _is_client_error(exc):
                raise BrokerError(f"alpaca 4xx: {exc}") from exc
            raise TransientBrokerError(str(exc)) from exc
        except (TimeoutError, ConnectionError) as exc:
            raise TransientBrokerError(str(exc)) from exc


def _is_client_error(exc: APIError) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        n = int(code) if code is not None else 0
    except (TypeError, ValueError):
        return False
    return 400 <= n < 500
