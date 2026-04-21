"""Unit tests for AlpacaBroker using a stub TradingClient.

Live-API paper-order tests live in `tests/integration/test_alpaca_broker.py`
and are gated on ALPACA_API_KEY. These tests cover translation logic in
isolation: status mapping, request construction, error classification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetExchange, PositionSide
from alpaca.trading.enums import OrderClass as AlpacaOrderClass
from alpaca.trading.enums import OrderSide as AlpacaSide
from alpaca.trading.enums import OrderStatus as AlpacaStatus
from alpaca.trading.enums import OrderType as AlpacaType
from alpaca.trading.enums import TimeInForce as AlpacaTIF
from alpaca.trading.models import Order as AlpacaOrder
from alpaca.trading.models import Position as AlpacaPosition
from alpaca.trading.models import TradeAccount
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from quant.execution.alpaca_broker import AlpacaBroker
from quant.execution.broker_base import (
    BrokerError,
    OrderNotFoundError,
    OrderRejectedError,
    TransientBrokerError,
)
from quant.types import Order, OrderSide, OrderStatus, OrderType


def _make_api_error(status_code: int, message: str = "boom") -> APIError:
    # APIError reads `.status_code` from the wrapped http_error's
    # response. Mimic that shape with a cheap stub.
    http_error = MagicMock()
    http_error.response.status_code = status_code
    return APIError(f'{{"code": {status_code}, "message": "{message}"}}', http_error)


def _fake_account() -> TradeAccount:
    return TradeAccount(
        id=uuid4(),
        account_number="PA000001",
        status="ACTIVE",
        currency="USD",
        cash="10000.00",
        buying_power="20000.00",
        portfolio_value="15000.00",
        pattern_day_trader=False,
        trading_blocked=False,
        transfers_blocked=False,
        account_blocked=False,
        created_at=datetime.now(UTC),
        trade_suspended_by_user=False,
        multiplier="2",
        shorting_enabled=True,
        equity="15000.00",
        last_equity="15000.00",
        long_market_value="5000.00",
        short_market_value="0.00",
        initial_margin="0.00",
        maintenance_margin="0.00",
        last_maintenance_margin="0.00",
        sma="0.00",
        daytrade_count=0,
    )


def _fake_position(symbol: str = "SPY", qty: str = "10", price: str = "100") -> AlpacaPosition:
    return AlpacaPosition(
        asset_id=uuid4(),
        symbol=symbol,
        exchange=AssetExchange.NASDAQ,
        asset_class=AssetClass.US_EQUITY,
        asset_marginable=True,
        qty=qty,
        avg_entry_price=price,
        side=PositionSide.LONG,
        market_value="1000.00",
        cost_basis="1000.00",
        unrealized_pl="0.00",
        unrealized_plpc="0.00",
        unrealized_intraday_pl="0.00",
        unrealized_intraday_plpc="0.00",
        current_price=price,
        lastday_price=price,
        change_today="0",
        qty_available=qty,
    )


def _fake_order(
    client_order_id: str,
    *,
    status: AlpacaStatus = AlpacaStatus.ACCEPTED,
    filled_qty: str = "0",
    filled_avg_price: str | None = None,
) -> AlpacaOrder:
    return AlpacaOrder(
        id=uuid4(),
        client_order_id=client_order_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        submitted_at=datetime.now(UTC),
        filled_at=datetime.now(UTC) if filled_qty != "0" else None,
        expired_at=None,
        canceled_at=None,
        failed_at=None,
        replaced_at=None,
        replaced_by=None,
        replaces=None,
        asset_id=uuid4(),
        symbol="SPY",
        asset_class=AssetClass.US_EQUITY,
        qty="10",
        filled_qty=filled_qty,
        type=AlpacaType.MARKET,
        side=AlpacaSide.BUY,
        time_in_force=AlpacaTIF.DAY,
        limit_price=None,
        stop_price=None,
        status=status,
        extended_hours=False,
        legs=None,
        trail_percent=None,
        trail_price=None,
        hwm=None,
        order_class=AlpacaOrderClass.SIMPLE,
        filled_avg_price=filled_avg_price,
    )


def _buy() -> Order:
    return Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("10"))


def _limit_buy(limit: str) -> Order:
    return Order(
        symbol="SPY",
        side=OrderSide.BUY,
        qty=Decimal("10"),
        type=OrderType.LIMIT,
        limit_price=Decimal(limit),
    )


# --- Happy path -----------------------------------------------------


def test_get_account_translates_alpaca_model() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_account.return_value = _fake_account()
    broker = AlpacaBroker(cast(TradingClient, client), paper=True)
    acct = broker.get_account()
    assert acct.cash == Decimal("10000.00")
    assert acct.paper is True


def test_get_positions_drops_zero_qty() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_all_positions.return_value = [
        _fake_position(symbol="SPY", qty="10"),
        _fake_position(symbol="QQQ", qty="0"),
    ]
    broker = AlpacaBroker(cast(TradingClient, client))
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"


def test_submit_order_builds_market_request_and_translates_status() -> None:
    client = MagicMock(spec=TradingClient)
    order = _buy()
    client.submit_order.return_value = _fake_order(
        str(order.client_order_id), status=AlpacaStatus.ACCEPTED
    )
    broker = AlpacaBroker(cast(TradingClient, client))
    result = broker.submit_order(order)
    assert result.status == OrderStatus.ACCEPTED

    req = client.submit_order.call_args.args[0]
    assert isinstance(req, MarketOrderRequest)
    assert req.symbol == "SPY"
    assert req.side == AlpacaSide.BUY
    assert req.time_in_force == AlpacaTIF.DAY


def test_submit_order_builds_limit_request() -> None:
    client = MagicMock(spec=TradingClient)
    order = _limit_buy("99.50")
    client.submit_order.return_value = _fake_order(
        str(order.client_order_id), status=AlpacaStatus.NEW
    )
    broker = AlpacaBroker(cast(TradingClient, client))
    broker.submit_order(order)
    req = client.submit_order.call_args.args[0]
    assert isinstance(req, LimitOrderRequest)
    assert float(req.limit_price) == 99.5


def test_get_fills_synthesizes_aggregate_fill() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.return_value = _fake_order(
        "coid", status=AlpacaStatus.FILLED, filled_qty="10", filled_avg_price="100.50"
    )
    broker = AlpacaBroker(cast(TradingClient, client))
    fills = broker.get_fills(uuid4())
    assert len(fills) == 1
    assert fills[0].qty == Decimal("10")
    assert fills[0].price == Decimal("100.50")


def test_get_fills_empty_when_not_filled() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.return_value = _fake_order(
        "coid", status=AlpacaStatus.ACCEPTED, filled_qty="0"
    )
    broker = AlpacaBroker(cast(TradingClient, client))
    assert broker.get_fills(uuid4()) == []


def test_get_order_status_maps_alpaca_statuses() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.return_value = _fake_order("coid", status=AlpacaStatus.REJECTED)
    broker = AlpacaBroker(cast(TradingClient, client))
    assert broker.get_order_status(uuid4()) == OrderStatus.REJECTED


def test_cancel_order_calls_cancel_by_id() -> None:
    client = MagicMock(spec=TradingClient)
    fake = _fake_order("coid")
    client.get_order_by_client_id.return_value = fake
    broker = AlpacaBroker(cast(TradingClient, client))
    broker.cancel_order(uuid4())
    client.cancel_order_by_id.assert_called_once_with(str(fake.id))


# --- Error translation ---------------------------------------------


def test_4xx_submit_becomes_order_rejected() -> None:
    client = MagicMock(spec=TradingClient)
    client.submit_order.side_effect = _make_api_error(400, "insufficient buying power")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(OrderRejectedError):
        broker.submit_order(_buy())


def test_5xx_submit_becomes_transient_error() -> None:
    client = MagicMock(spec=TradingClient)
    client.submit_order.side_effect = _make_api_error(503, "service unavailable")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.submit_order(_buy())


def test_network_error_on_submit_becomes_transient() -> None:
    client = MagicMock(spec=TradingClient)
    client.submit_order.side_effect = TimeoutError("connection reset")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.submit_order(_buy())


def test_missing_order_lookup_raises_not_found() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.side_effect = _make_api_error(404, "not found")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(OrderNotFoundError):
        broker.get_order_status(uuid4())


def test_5xx_on_account_lookup_becomes_transient() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_account.side_effect = _make_api_error(502, "bad gateway")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.get_account()


def test_4xx_on_account_lookup_becomes_broker_error() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_account.side_effect = _make_api_error(403, "forbidden")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(BrokerError):
        broker.get_account()


def test_cancel_swallows_4xx_as_idempotent() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.return_value = _fake_order("coid")
    client.cancel_order_by_id.side_effect = _make_api_error(422, "already cancelled")
    broker = AlpacaBroker(cast(TradingClient, client))
    broker.cancel_order(uuid4())  # no exception


def test_cancel_5xx_becomes_transient() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.return_value = _fake_order("coid")
    client.cancel_order_by_id.side_effect = _make_api_error(503, "down")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.cancel_order(uuid4())


def test_5xx_on_status_lookup_becomes_transient() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_order_by_client_id.side_effect = _make_api_error(503, "upstream")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.get_order_status(uuid4())


def test_network_error_on_account_lookup_becomes_transient() -> None:
    client = MagicMock(spec=TradingClient)
    client.get_account.side_effect = ConnectionError("peer reset")
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.get_account()


def test_is_client_error_with_non_numeric_code_returns_false() -> None:
    # Construct an APIError whose http_error has no numeric status_code
    # nor a parseable JSON code — our classifier must not raise.
    http_error = MagicMock()
    http_error.response.status_code = None
    err = APIError('{"code": "not-a-number", "message": "x"}', http_error)
    client = MagicMock(spec=TradingClient)
    client.submit_order.side_effect = err
    broker = AlpacaBroker(cast(TradingClient, client))
    with pytest.raises(TransientBrokerError):
        broker.submit_order(_buy())


def test_from_credentials_constructs_client(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[dict[str, object]] = []

    def fake_init(self, *, api_key=None, secret_key=None, paper=True, **_):
        constructed.append({"api_key": api_key, "secret_key": secret_key, "paper": paper})

    monkeypatch.setattr(TradingClient, "__init__", fake_init)
    AlpacaBroker.from_credentials(api_key="K", api_secret="S", paper=True)
    assert constructed == [{"api_key": "K", "secret_key": "S", "paper": True}]
