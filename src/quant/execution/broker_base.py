"""Broker interface + shared exceptions (PRD §4.3).

Three implementations of `Broker` will exist in V1:

* `PaperBroker` — in-memory simulator. Deterministic; used in unit tests
  and paper mode. See `paper_broker.py`.
* `AlpacaBroker` — wraps `alpaca-py`'s `TradingClient`. See
  `alpaca_broker.py`.
* `BacktestBroker` — future: vectorbt integration. Not implemented in
  Wave 7.

The interface is deliberately minimal: everything `LiveRunner` needs to
submit orders and reconcile state. Domain types (`Order`, `Fill`,
`Position`, `Account`) come from `quant.types`; broker-specific types
never cross this line.

Sync vs async: the API is synchronous. Alpaca's paper endpoint is fast
and the daily-bar cadence doesn't need concurrency; keeping it sync
makes tests, retries, and error handling much simpler.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from quant.types import Account, Fill, Order, OrderResult, OrderStatus, Position


class BrokerError(Exception):
    """Base class for broker-layer failures."""


class OrderRejectedError(BrokerError):
    """The broker refused the order. Non-retryable by definition.

    `reason` is the human-readable cause; `order_id` is our client UUID.
    """

    def __init__(self, order_id: UUID, reason: str) -> None:
        super().__init__(f"order {order_id} rejected: {reason}")
        self.order_id = order_id
        self.reason = reason


class OrderNotFoundError(BrokerError):
    """Broker has no record of the order id."""


class TransientBrokerError(BrokerError):
    """Network / timeout / 5xx — the caller should retry with backoff."""


class Broker(ABC):
    """Minimal broker surface shared by paper, alpaca, and backtest."""

    name: str = "broker"

    @abstractmethod
    def get_account(self) -> Account:
        """Snapshot of equity, cash, buying power, PDT status."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """All non-zero positions (per-symbol)."""

    @abstractmethod
    def submit_order(self, order: Order) -> OrderResult:
        """Send an order to the broker. Returns immediately with the
        broker's acknowledgment. The order may still be filling in the
        background — poll `get_order_status` to track it.

        Raises `OrderRejectedError` if the broker refuses upfront;
        `TransientBrokerError` if the send failed transiently (callers
        retry), `BrokerError` for unexpected failures.
        """

    @abstractmethod
    def get_order_status(self, order_id: UUID) -> OrderStatus:
        """Latest status of a previously submitted order."""

    @abstractmethod
    def get_fills(self, order_id: UUID) -> list[Fill]:
        """All fills realized so far for a previously submitted order."""

    @abstractmethod
    def cancel_order(self, order_id: UUID) -> None:
        """Cancel a working order. No-op if already terminal."""
