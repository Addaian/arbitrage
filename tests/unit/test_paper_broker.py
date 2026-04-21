"""Tests for the in-memory PaperBroker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant.execution import (
    Broker,
    OrderNotFoundError,
    OrderRejectedError,
    PaperBroker,
)
from quant.types import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)


def _buy(symbol: str = "SPY", qty: str = "10") -> Order:
    return Order(symbol=symbol, side=OrderSide.BUY, qty=Decimal(qty))


def _sell(symbol: str = "SPY", qty: str = "10") -> Order:
    return Order(symbol=symbol, side=OrderSide.SELL, qty=Decimal(qty))


def _limit_buy(symbol: str, qty: str, limit: str) -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal(qty),
        type=OrderType.LIMIT,
        limit_price=Decimal(limit),
    )


# --- Interface parity + happy path -------------------------------------


def test_paper_broker_implements_broker_interface() -> None:
    pb = PaperBroker()
    assert isinstance(pb, Broker)


def test_submit_accepts_order_and_returns_broker_id() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"))
    pb.update_prices({"SPY": Decimal("100")})
    res = pb.submit_order(_buy())
    assert res.status == OrderStatus.ACCEPTED
    assert res.broker_order_id is not None and res.broker_order_id.startswith("paper-")


def test_duplicate_client_order_id_is_rejected() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"))
    pb.update_prices({"SPY": Decimal("100")})
    order = _buy()
    pb.submit_order(order)
    with pytest.raises(OrderRejectedError, match="duplicate"):
        pb.submit_order(order)


def test_advance_to_fills_queued_orders_at_next_open() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.update_prices({"SPY": Decimal("100")})
    result = pb.submit_order(_buy(qty="10"))

    fills = pb.advance_to({"SPY": Decimal("101")}, now=datetime.now(UTC))
    assert len(fills) == 1
    assert pb.get_order_status(result.order_id) == OrderStatus.FILLED
    assert pb.get_fills(result.order_id)[0].price == Decimal("101")


def test_slippage_penalizes_buy_and_sell_symmetrically() -> None:
    pb = PaperBroker(starting_cash=Decimal("100000"), slippage_bps=Decimal("50"))
    pb.submit_order(_buy(qty="10"))
    fills_buy = pb.advance_to({"SPY": Decimal("100")})
    pb.submit_order(_sell(qty="10"))
    fills_sell = pb.advance_to({"SPY": Decimal("100")})
    # Buy fills 0.5% above reference, sell fills 0.5% below.
    assert fills_buy[0].price == Decimal("100.500")
    assert fills_sell[0].price == Decimal("99.500")


# --- Cash + position accounting ----------------------------------------


def test_buy_moves_cash_and_creates_position() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_buy("SPY", qty="10"))
    pb.advance_to({"SPY": Decimal("100")})
    account = pb.get_account()
    positions = pb.get_positions()
    assert account.cash == Decimal("9000.00")  # 10000 - 10*100
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert positions[0].qty == Decimal("10")
    assert positions[0].avg_entry_price == Decimal("100")


def test_equity_equals_cash_plus_mark_to_market() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_buy("SPY", qty="10"))
    pb.advance_to({"SPY": Decimal("100")})
    # Mark up.
    pb.update_prices({"SPY": Decimal("105")})
    account = pb.get_account()
    assert account.cash == Decimal("9000.00")
    assert account.equity == Decimal("9000") + Decimal("10") * Decimal("105")


def test_sell_fully_closes_position_and_returns_cash() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_buy("SPY", qty="10"))
    pb.advance_to({"SPY": Decimal("100")})
    pb.submit_order(_sell("SPY", qty="10"))
    pb.advance_to({"SPY": Decimal("110")})
    # 10 shares sold at 110 → +1100 back into cash.
    account = pb.get_account()
    assert account.cash == Decimal("10100.00")
    assert pb.get_positions() == []


def test_partial_sell_reduces_position() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_buy("SPY", qty="10"))
    pb.advance_to({"SPY": Decimal("100")})
    pb.submit_order(_sell("SPY", qty="4"))
    pb.advance_to({"SPY": Decimal("100")})
    positions = pb.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("6")


def test_sell_past_long_flips_to_short() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_buy("SPY", qty="5"))
    pb.advance_to({"SPY": Decimal("100")})
    pb.submit_order(_sell("SPY", qty="8"))
    pb.advance_to({"SPY": Decimal("100")})
    positions = pb.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("-3")


def test_commission_reduces_cash_further() -> None:
    pb = PaperBroker(
        starting_cash=Decimal("10000"),
        slippage_bps=Decimal("0"),
        fee_bps=Decimal("10"),  # 10 bps
    )
    pb.submit_order(_buy("SPY", qty="10"))
    pb.advance_to({"SPY": Decimal("100")})
    # Notional 1000, fee 1.00 → cash = 9000 - 1 = 8999.00.
    assert pb.get_account().cash == Decimal("8999.00")


# --- Limit orders ------------------------------------------------------


def test_limit_buy_does_not_fill_above_limit() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_limit_buy("SPY", "10", "95"))
    fills = pb.advance_to({"SPY": Decimal("100")})  # above limit
    assert fills == []


def test_limit_buy_fills_at_or_below_limit() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    pb.submit_order(_limit_buy("SPY", "10", "100"))
    fills = pb.advance_to({"SPY": Decimal("99")})
    assert len(fills) == 1
    assert fills[0].price <= Decimal("100")


def test_limit_order_rejects_nonpositive_limit() -> None:
    pb = PaperBroker()
    with pytest.raises((OrderRejectedError, ValueError)):
        order = Order(
            symbol="SPY",
            side=OrderSide.BUY,
            qty=Decimal("10"),
            type=OrderType.LIMIT,
            limit_price=Decimal("0"),  # rejected by Pydantic (PositiveDecimal)
        )
        pb.submit_order(order)


# --- Cancel + status lookups -------------------------------------------


def test_cancel_an_accepted_order() -> None:
    pb = PaperBroker()
    result = pb.submit_order(_buy())
    pb.cancel_order(result.order_id)
    assert pb.get_order_status(result.order_id) == OrderStatus.CANCELLED


def test_cancel_is_idempotent_on_terminal_orders() -> None:
    pb = PaperBroker()
    result = pb.submit_order(_buy())
    pb.advance_to({"SPY": Decimal("100")})
    assert pb.get_order_status(result.order_id) == OrderStatus.FILLED
    # Second cancel after fill is a no-op (no exception).
    pb.cancel_order(result.order_id)
    assert pb.get_order_status(result.order_id) == OrderStatus.FILLED


def test_cancel_unknown_raises() -> None:
    pb = PaperBroker()
    with pytest.raises(OrderNotFoundError):
        pb.cancel_order(_buy().client_order_id)


def test_get_fills_for_unknown_raises() -> None:
    pb = PaperBroker()
    with pytest.raises(OrderNotFoundError):
        pb.get_fills(_buy().client_order_id)


def test_get_status_for_unknown_raises() -> None:
    pb = PaperBroker()
    with pytest.raises(OrderNotFoundError):
        pb.get_order_status(_buy().client_order_id)


# --- Queue discipline --------------------------------------------------


def test_order_stays_queued_when_symbol_doesnt_print() -> None:
    pb = PaperBroker()
    result = pb.submit_order(_buy("SPY"))
    # Next bar has prices for QQQ only, not SPY.
    fills = pb.advance_to({"QQQ": Decimal("300")})
    assert fills == []
    assert pb.get_order_status(result.order_id) == OrderStatus.ACCEPTED


def test_limit_sell_does_not_fill_below_limit() -> None:
    pb = PaperBroker(starting_cash=Decimal("0"), slippage_bps=Decimal("0"))
    # Seed a long so we can sell it.
    pb.submit_order(Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("10")))
    pb.advance_to({"SPY": Decimal("100")})
    # Post a limit sell at 110, next bar prints 105 — stays queued.
    pb.submit_order(
        Order(
            symbol="SPY",
            side=OrderSide.SELL,
            qty=Decimal("5"),
            type=OrderType.LIMIT,
            limit_price=Decimal("110"),
        )
    )
    fills = pb.advance_to({"SPY": Decimal("105")})
    assert fills == []


def test_sell_deepens_existing_short() -> None:
    pb = PaperBroker(starting_cash=Decimal("10000"), slippage_bps=Decimal("0"))
    # First sell with no long → go short.
    pb.submit_order(_sell(qty="5"))
    pb.advance_to({"SPY": Decimal("100")})
    # Second sell adds to short.
    pb.submit_order(_sell(qty="3"))
    pb.advance_to({"SPY": Decimal("100")})
    positions = pb.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("-8")


def test_sequential_advances_do_not_double_fill() -> None:
    pb = PaperBroker()
    pb.submit_order(_buy(qty="10"))
    pb.advance_to({"SPY": Decimal("100")})
    # Nothing should fill on a second advance — the order is already filled.
    fills = pb.advance_to({"SPY": Decimal("105")}, now=datetime.now(UTC) + timedelta(days=1))
    assert fills == []
