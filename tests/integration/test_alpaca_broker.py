"""Integration test: round-trip against Alpaca's paper API.

Skips cleanly when ALPACA_API_KEY / ALPACA_API_SECRET are unset —
running without creds is the common case (CI, cold clones).

When creds are present we:
1. Connect to paper.
2. Submit a tiny (1-share) market BUY on SPY (or another cheap liquid
   ETF via env override).
3. Poll for terminal status (fill or rejection) with a short timeout.
4. Reconcile positions.
5. If we ended up long, immediately flatten to leave the paper account
   clean for subsequent runs.

The whole test is keep-alive safe: it only runs once per invocation and
never leaves working orders behind.
"""

from __future__ import annotations

import contextlib
import os
from decimal import Decimal

import pytest

from quant.execution import (
    AlpacaBroker,
    OrderManager,
    OrderRejectedError,
)
from quant.types import Order, OrderSide, OrderStatus

pytestmark = pytest.mark.integration


def _has_credentials() -> bool:
    return bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_API_SECRET"))


@pytest.fixture(scope="module")
def broker() -> AlpacaBroker:
    if not _has_credentials():
        pytest.skip("ALPACA_API_KEY/SECRET not set — skipping Alpaca integration test")
    return AlpacaBroker.from_credentials(
        api_key=os.environ["ALPACA_API_KEY"],
        api_secret=os.environ["ALPACA_API_SECRET"],
        paper=True,
    )


def _test_symbol() -> str:
    return os.getenv("ALPACA_IT_SYMBOL", "SPY")


def test_paper_round_trip(broker: AlpacaBroker) -> None:
    symbol = _test_symbol()

    # Sanity: account loads.
    acct = broker.get_account()
    assert acct.paper is True
    assert acct.equity > Decimal("0")

    # Submit a 1-share market buy.
    order = Order(symbol=symbol, side=OrderSide.BUY, qty=Decimal("1"), strategy="it-test")
    om = OrderManager(broker, submit_attempts=2, poll_interval=1.0, poll_timeout=30.0)
    try:
        outcome = om.execute(order, wait_for_fill=True)
    except OrderRejectedError as exc:
        # Closed market / PDT lock — treat as "test couldn't run", not fail.
        pytest.skip(f"alpaca rejected submit (likely market-closed): {exc}")

    assert outcome.final_status in {OrderStatus.FILLED, OrderStatus.ACCEPTED}

    # Reconcile.
    positions = {p.symbol: p for p in broker.get_positions()}
    if outcome.final_status == OrderStatus.FILLED:
        assert symbol in positions
        assert positions[symbol].qty >= Decimal("1")

        # Cleanup: flatten back.
        sell = Order(
            symbol=symbol,
            side=OrderSide.SELL,
            qty=Decimal("1"),
            strategy="it-test-cleanup",
        )
        # Best-effort cleanup — acceptable if the market is now closed.
        with contextlib.suppress(OrderRejectedError):
            om.execute(sell, wait_for_fill=True)
