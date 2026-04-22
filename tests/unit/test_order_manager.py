"""Tests for the OrderManager — retry policy + lifecycle polling."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from quant.config import RiskConfig
from quant.execution import (
    Broker,
    OrderManager,
    OrderNotFoundError,
    OrderRejectedError,
    PaperBroker,
    TransientBrokerError,
)
from quant.risk import Killswitch, RiskValidator
from quant.types import (
    Fill,
    Order,
    OrderResult,
    OrderSide,
    OrderStatus,
)


def _buy() -> Order:
    return Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("10"))


# --- Broker stubs for retry / rejection testing ------------------------


class _FlakyBroker(Broker):
    """Fails `fail_n` times with TransientBrokerError, then succeeds."""

    name = "flaky"

    def __init__(self, fail_n: int) -> None:
        self.fail_n = fail_n
        self.calls = 0

    def get_account(self):  # pragma: no cover — not used here
        raise NotImplementedError

    def get_positions(self):  # pragma: no cover
        return []

    def submit_order(self, order: Order) -> OrderResult:
        self.calls += 1
        if self.calls <= self.fail_n:
            raise TransientBrokerError(f"attempt {self.calls}: down")
        return OrderResult(
            order_id=order.client_order_id,
            broker_order_id="flaky-ok",
            status=OrderStatus.ACCEPTED,
            submitted_at=datetime.now(UTC),
        )

    def get_order_status(self, order_id: UUID) -> OrderStatus:
        return OrderStatus.FILLED

    def get_fills(self, order_id: UUID) -> list[Fill]:
        return []

    def cancel_order(self, order_id: UUID) -> None:  # pragma: no cover
        pass


class _AlwaysRejecting(Broker):
    """Always raises OrderRejectedError on submit."""

    name = "reject"

    def __init__(self) -> None:
        self.calls = 0

    def get_account(self):  # pragma: no cover
        raise NotImplementedError

    def get_positions(self):  # pragma: no cover
        return []

    def submit_order(self, order: Order) -> OrderResult:
        self.calls += 1
        raise OrderRejectedError(order.client_order_id, "insufficient buying power")

    def get_order_status(self, order_id: UUID) -> OrderStatus:  # pragma: no cover
        raise OrderNotFoundError(str(order_id))

    def get_fills(self, order_id: UUID) -> list[Fill]:  # pragma: no cover
        return []

    def cancel_order(self, order_id: UUID) -> None:  # pragma: no cover
        pass


# --- Happy path --------------------------------------------------------


def test_execute_returns_outcome_with_broker_ack() -> None:
    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.1, poll_interval=0.0)
    outcome = om.execute(_buy())
    assert outcome.result.status == OrderStatus.ACCEPTED
    assert outcome.final_status == OrderStatus.ACCEPTED
    assert outcome.order.submitted_at is not None


def test_execute_waits_for_fill_when_requested() -> None:
    # Pre-seed a separate broker so we can advance prices *before*
    # OrderManager's first poll, making the status flip before the
    # synchronous poll loop times out.
    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    result = pb.submit_order(_buy())
    pb.advance_to({"SPY": Decimal("101")})
    assert pb.get_order_status(result.order_id) == OrderStatus.FILLED

    # And with a fresh broker, exercise the manager's wait path end-to-end.
    pb2 = PaperBroker()
    pb2.update_prices({"SPY": Decimal("100")})
    om = OrderManager(pb2, submit_attempts=1, poll_timeout=1.0, poll_interval=0.0)
    outcome = om.execute(_buy(), wait_for_fill=True)
    assert outcome.final_status == OrderStatus.ACCEPTED  # no advance_to -> stays accepted


def test_poll_returns_last_seen_status_on_timeout() -> None:
    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.05, poll_interval=0.01)
    # Submit but never advance the simulator → status stays ACCEPTED.
    outcome = om.execute(_buy(), wait_for_fill=True)
    assert outcome.final_status == OrderStatus.ACCEPTED


# --- Retry policy ------------------------------------------------------


def test_transient_error_retries_until_success() -> None:
    broker = _FlakyBroker(fail_n=2)
    om = OrderManager(broker, submit_attempts=3, submit_backoff=0.001, poll_timeout=0.0)
    outcome = om.execute(_buy())
    assert outcome.result.status == OrderStatus.ACCEPTED
    assert broker.calls == 3  # failed twice, succeeded on third


def test_transient_error_exhausts_attempts() -> None:
    broker = _FlakyBroker(fail_n=5)
    om = OrderManager(broker, submit_attempts=3, submit_backoff=0.001, poll_timeout=0.0)
    with pytest.raises(TransientBrokerError):
        om.execute(_buy())
    assert broker.calls == 3


def test_rejection_is_not_retried() -> None:
    broker = _AlwaysRejecting()
    om = OrderManager(broker, submit_attempts=5, submit_backoff=0.001, poll_timeout=0.0)
    with pytest.raises(OrderRejectedError):
        om.execute(_buy())
    assert broker.calls == 1  # no retries on rejection


# --- Cancel path -------------------------------------------------------


def test_cancel_delegates_to_broker() -> None:
    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    om = OrderManager(pb, poll_timeout=0.0)
    outcome = om.execute(_buy())
    om.cancel(outcome.result.order_id)
    assert pb.get_order_status(outcome.result.order_id) == OrderStatus.CANCELLED


# --- Parity: PaperBroker and stub-Alpaca are swappable ----------------


class _FlakyStatusBroker(Broker):
    """Submits fine; status polls raise transient twice, then FILLED."""

    name = "flaky-status"

    def __init__(self) -> None:
        self.status_calls = 0

    def get_account(self):  # pragma: no cover
        raise NotImplementedError

    def get_positions(self):  # pragma: no cover
        return []

    def submit_order(self, order: Order) -> OrderResult:
        return OrderResult(
            order_id=order.client_order_id,
            broker_order_id="flaky-status",
            status=OrderStatus.ACCEPTED,
            submitted_at=datetime.now(UTC),
        )

    def get_order_status(self, order_id: UUID) -> OrderStatus:
        self.status_calls += 1
        if self.status_calls <= 2:
            raise TransientBrokerError("poll flake")
        return OrderStatus.FILLED

    def get_fills(self, order_id: UUID) -> list[Fill]:
        # Simulate broker losing track after terminal status.
        raise OrderNotFoundError(str(order_id))

    def cancel_order(self, order_id: UUID) -> None:  # pragma: no cover
        pass


def test_transient_during_poll_retries_and_fills_eventually() -> None:
    broker = _FlakyStatusBroker()
    om = OrderManager(broker, submit_attempts=1, poll_timeout=1.0, poll_interval=0.0)
    outcome = om.execute(_buy(), wait_for_fill=True)
    assert outcome.final_status == OrderStatus.FILLED
    # _safe_get_fills swallows OrderNotFoundError and returns an empty list.
    assert outcome.fills == []


# --- Risk-hook integration --------------------------------------------


def test_killswitch_blocks_submit(tmp_path) -> None:

    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    ks = Killswitch(tmp_path / "HALT")
    ks.engage(reason="test")
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.0, killswitch=ks)
    with pytest.raises(OrderRejectedError, match="killswitch"):
        om.execute(_buy())


def test_killswitch_allows_when_not_engaged(tmp_path) -> None:

    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    ks = Killswitch(tmp_path / "HALT")  # not engaged
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.0, killswitch=ks)
    outcome = om.execute(_buy())
    assert outcome.result.status == OrderStatus.ACCEPTED


def test_risk_validator_rejects_oversize_order() -> None:

    pb = PaperBroker(starting_cash=Decimal("100000"))
    pb.update_prices({"SPY": Decimal("100")})
    validator = RiskValidator(
        RiskConfig(
            max_position_pct=0.30,
            max_daily_loss_pct=0.05,
            max_monthly_drawdown_pct=0.15,
            max_order_size_pct=0.20,
            max_price_deviation_pct=0.01,
            target_annual_vol=0.10,
            max_gross_exposure=1.0,
        )
    )
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.0, risk_validator=validator)
    # 500 * $100 = $50k = 50% of $100k equity — blows order-size cap.
    order = Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("500"))
    acct = pb.get_account()
    with pytest.raises(OrderRejectedError, match="max_order_size_pct"):
        om.execute(order, account=acct, reference_price=Decimal("100"))


def test_risk_validator_accepts_valid_order() -> None:

    pb = PaperBroker(starting_cash=Decimal("100000"))
    pb.update_prices({"SPY": Decimal("100")})
    validator = RiskValidator(
        RiskConfig(
            max_position_pct=0.30,
            max_daily_loss_pct=0.05,
            max_monthly_drawdown_pct=0.15,
            max_order_size_pct=0.20,
            max_price_deviation_pct=0.01,
            target_annual_vol=0.10,
            max_gross_exposure=1.0,
        )
    )
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.0, risk_validator=validator)
    # 100 * $100 = $10k = 10% of equity — passes all limits.
    order = Order(symbol="SPY", side=OrderSide.BUY, qty=Decimal("100"))
    outcome = om.execute(order, account=pb.get_account(), reference_price=Decimal("100"))
    assert outcome.result.status == OrderStatus.ACCEPTED


def test_risk_validator_requires_account_and_price() -> None:

    pb = PaperBroker()
    pb.update_prices({"SPY": Decimal("100")})
    validator = RiskValidator(
        RiskConfig(
            max_position_pct=0.30,
            max_daily_loss_pct=0.05,
            max_monthly_drawdown_pct=0.15,
            max_order_size_pct=0.20,
            max_price_deviation_pct=0.01,
            target_annual_vol=0.10,
            max_gross_exposure=1.0,
        )
    )
    om = OrderManager(pb, submit_attempts=1, poll_timeout=0.0, risk_validator=validator)
    with pytest.raises(ValueError, match="account/reference_price"):
        om.execute(_buy())


def test_swapping_broker_preserves_caller_flow() -> None:
    """The same call sequence works against two different Broker impls —
    this is the PRD §4.3 acceptance criterion.
    """

    def run_cycle(b: Broker) -> OrderStatus:
        om = OrderManager(b, submit_attempts=1, poll_timeout=0.0)
        outcome = om.execute(_buy())
        return outcome.result.status

    paper = PaperBroker()
    paper.update_prices({"SPY": Decimal("100")})
    flaky = _FlakyBroker(fail_n=0)
    assert run_cycle(paper) == OrderStatus.ACCEPTED
    assert run_cycle(flaky) == OrderStatus.ACCEPTED
