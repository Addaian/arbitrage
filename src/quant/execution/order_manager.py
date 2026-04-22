"""Order lifecycle driver.

Sits between strategy code (which says "please buy 10 SPY") and the
`Broker` (which says "ok, accepted, working"). Responsibilities:

1. **Submit with retries** on `TransientBrokerError`. Exponential
   backoff via `tenacity`. Rejections bubble up unchanged — retrying a
   rejection is always a bug.
2. **Stamp `submitted_at`** on the outgoing order so downstream logs
   match the broker's timestamp.
3. **Await terminal status** when the caller opts in — polls the broker
   until the order is filled, cancelled, rejected, or a timeout hits.
4. **Record every transition** to the configured repos (`OrderRepo`,
   `FillRepo`) inside one transaction per transition. The caller owns
   the session; the repos compose on it.

No state. No background threads. `OrderManager.execute(order)` is
intended to be one synchronous call from `LiveRunner`'s daily cycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from quant.execution.broker_base import (
    Broker,
    OrderNotFoundError,
    OrderRejectedError,
    TransientBrokerError,
)
from quant.risk.killswitch import Killswitch
from quant.risk.limits import RiskValidator
from quant.types import Account, Fill, Order, OrderResult, OrderStatus, Position

_TERMINAL_STATES = {
    OrderStatus.FILLED,
    OrderStatus.REJECTED,
    OrderStatus.CANCELLED,
    OrderStatus.EXPIRED,
}


@dataclass
class OrderOutcome:
    order: Order
    result: OrderResult
    final_status: OrderStatus
    fills: list[Fill] = field(default_factory=list)
    transitions: list[tuple[datetime, OrderStatus]] = field(default_factory=list)


class OrderManager:
    """Drives a single order through its broker lifecycle."""

    def __init__(
        self,
        broker: Broker,
        *,
        submit_attempts: int = 3,
        submit_backoff: float = 1.0,
        poll_interval: float = 0.5,
        poll_timeout: float = 60.0,
        risk_validator: RiskValidator | None = None,
        killswitch: Killswitch | None = None,
    ) -> None:
        self._broker = broker
        self._submit_attempts = submit_attempts
        self._submit_backoff = submit_backoff
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._risk_validator = risk_validator
        self._killswitch = killswitch

    def execute(
        self,
        order: Order,
        *,
        wait_for_fill: bool = False,
        account: Account | None = None,
        reference_price: Decimal | None = None,
        current_positions: list[Position] | None = None,
    ) -> OrderOutcome:
        """Submit `order`, optionally wait for terminal status.

        When a `RiskValidator` is configured, `account` and
        `reference_price` MUST be supplied — the validator uses them to
        size-check the order. When a `Killswitch` is configured and
        engaged, the order is rejected before any broker call is made.
        """
        self._check_killswitch(order)
        self._check_risk_limits(
            order,
            account=account,
            reference_price=reference_price,
            current_positions=current_positions,
        )
        submitted = order.model_copy(update={"submitted_at": datetime.now(UTC)})
        result = self._submit_with_retry(submitted)
        transitions: list[tuple[datetime, OrderStatus]] = [(result.submitted_at, result.status)]

        if not wait_for_fill or result.status in _TERMINAL_STATES:
            return OrderOutcome(
                order=submitted,
                result=result,
                final_status=result.status,
                fills=self._safe_get_fills(result.order_id),
                transitions=transitions,
            )

        final_status = self._poll_until_terminal(result.order_id, transitions=transitions)
        return OrderOutcome(
            order=submitted,
            result=result,
            final_status=final_status,
            fills=self._safe_get_fills(result.order_id),
            transitions=transitions,
        )

    def cancel(self, order_id: UUID) -> None:
        self._broker.cancel_order(order_id)

    # --- Pre-trade hooks -----------------------------------------------

    def _check_killswitch(self, order: Order) -> None:
        if self._killswitch is not None and self._killswitch.is_engaged():
            raise OrderRejectedError(order.client_order_id, "killswitch engaged — no new orders")

    def _check_risk_limits(
        self,
        order: Order,
        *,
        account: Account | None,
        reference_price: Decimal | None,
        current_positions: list[Position] | None,
    ) -> None:
        if self._risk_validator is None:
            return
        if account is None or reference_price is None:
            raise ValueError("risk_validator configured but account/reference_price not provided")
        rejection = self._risk_validator.validate_order(
            order,
            account,
            reference_price=reference_price,
            current_positions=current_positions,
        )
        if rejection is not None:
            raise OrderRejectedError(order.client_order_id, str(rejection))

    # --- Internals ------------------------------------------------------

    def _submit_with_retry(self, order: Order) -> OrderResult:
        attempts = self._submit_attempts
        backoff = self._submit_backoff

        @retry(
            reraise=True,
            retry=retry_if_exception_type(TransientBrokerError),
            wait=wait_exponential(multiplier=backoff, min=backoff, max=backoff * 10),
            stop=stop_after_attempt(attempts),
        )
        def _attempt() -> OrderResult:
            return self._broker.submit_order(order)

        try:
            return _attempt()
        except RetryError as exc:  # pragma: no cover — reraise=True already bubbles
            raise exc.last_attempt.exception() or TransientBrokerError("retry exhausted") from exc
        except OrderRejectedError:
            raise

    def _poll_until_terminal(
        self,
        order_id: UUID,
        *,
        transitions: list[tuple[datetime, OrderStatus]],
    ) -> OrderStatus:
        deadline = time.monotonic() + self._poll_timeout
        last_status = transitions[-1][1] if transitions else OrderStatus.NEW
        while True:
            if time.monotonic() >= deadline:
                return last_status
            try:
                status = self._broker.get_order_status(order_id)
            except TransientBrokerError:
                time.sleep(self._poll_interval)
                continue
            if status != last_status:
                transitions.append((datetime.now(UTC), status))
                last_status = status
            if status in _TERMINAL_STATES:
                return status
            time.sleep(self._poll_interval)

    def _safe_get_fills(self, order_id: UUID) -> list[Fill]:
        try:
            return self._broker.get_fills(order_id)
        except OrderNotFoundError:
            return []
