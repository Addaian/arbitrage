"""Validation tests on shared domain types."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quant.types import (
    Bar,
    Fill,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Signal,
    SignalDirection,
    TimeInForce,
)


def _bar(**over: object) -> Bar:
    defaults: dict[str, object] = {
        "symbol": "SPY",
        "ts": date(2026, 1, 2),
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
        "volume": Decimal("1000"),
    }
    defaults.update(over)
    return Bar(**defaults)  # type: ignore[arg-type]


def test_bar_happy_path() -> None:
    b = _bar()
    assert b.symbol == "SPY"
    assert b.adjusted is True


def test_bar_high_below_low_rejected() -> None:
    with pytest.raises(ValidationError, match="high < low"):
        _bar(high=Decimal("50"))


def test_bar_open_outside_range_rejected() -> None:
    with pytest.raises(ValidationError, match="open outside"):
        _bar(open=Decimal("200"))


def test_bar_close_outside_range_rejected() -> None:
    with pytest.raises(ValidationError, match="close outside"):
        _bar(close=Decimal("200"))


def test_bar_negative_price_rejected() -> None:
    with pytest.raises(ValidationError):
        _bar(low=Decimal("-1"))


def test_bar_is_frozen() -> None:
    b = _bar()
    with pytest.raises(ValidationError):
        b.symbol = "QQQ"  # type: ignore[misc]


def test_symbol_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        _bar(symbol="spy")  # lowercase rejected
    with pytest.raises(ValidationError):
        _bar(symbol="1SPY")  # must start with a letter


def test_market_order_disallows_limit_price() -> None:
    with pytest.raises(ValidationError, match="must not set limit_price"):
        Order(
            symbol="SPY",
            side=OrderSide.BUY,
            qty=Decimal("1"),
            type=OrderType.MARKET,
            limit_price=Decimal("100"),
        )


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValidationError, match="requires limit_price"):
        Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("1"), type=OrderType.LIMIT)


def test_order_defaults() -> None:
    o = Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("10"))
    assert o.type == OrderType.MARKET
    assert o.time_in_force == TimeInForce.DAY
    assert o.limit_price is None


def test_order_qty_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("0"))


def test_fill_and_result_round_trip() -> None:
    o = Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("10"))
    result = OrderResult(
        order_id=o.client_order_id,
        broker_order_id="abc",
        status=OrderStatus.FILLED,
        submitted_at=datetime(2026, 4, 20, tzinfo=UTC),
    )
    fill = Fill(
        order_id=o.client_order_id,
        broker_fill_id="fill-1",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=Decimal("10"),
        price=Decimal("100.1"),
        ts=datetime(2026, 4, 20, tzinfo=UTC),
    )
    assert result.order_id == o.client_order_id
    assert fill.order_id == o.client_order_id


def test_signal_weight_bounds() -> None:
    Signal(
        strategy="trend",
        symbol="SPY",
        ts=date(2026, 1, 2),
        direction=SignalDirection.LONG,
        target_weight=1.0,
    )
    with pytest.raises(ValidationError):
        Signal(
            strategy="trend",
            symbol="SPY",
            ts=date(2026, 1, 2),
            direction=SignalDirection.LONG,
            target_weight=1.5,
        )
    with pytest.raises(ValidationError):
        Signal(
            strategy="trend",
            symbol="SPY",
            ts=date(2026, 1, 2),
            direction=SignalDirection.SHORT,
            target_weight=-1.5,
        )
