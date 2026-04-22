"""Pre-trade hard-limit validation (PRD §6.1).

This module is the **one place** where "is this order allowed?" gets
decided. It has four responsibilities:

1. **Per-limit predicates** — one method per PRD §6.1 row so each rule
   is independently testable and auditable.
2. **Composite `validate_order()`** — runs every predicate, returns
   the rejection reason on the first miss, or `None` if the order is
   clean.
3. **No side effects.** The validator neither submits nor records — it
   answers a question. Caller decides what to do on reject (the
   `OrderManager` raises `OrderRejectedError`).
4. **Deterministic.** Given the same `(RiskConfig, order, account,
   reference_price)`, the answer is fully determined. Property tests
   depend on this.

All limit thresholds come from `RiskConfig` (PRD §6.1 caps are enforced
at config-load time in `quant.config`). That means a risk limit can
only ever be *tighter* than the PRD cap, never looser. Code-enforced
by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quant.config import RiskConfig
from quant.types import Account, Order, OrderSide, OrderType, Position


@dataclass(frozen=True)
class RejectionReason:
    """Structured rejection. `limit_name` matches the RiskConfig field."""

    limit_name: str
    message: str

    def __str__(self) -> str:
        return f"{self.limit_name}: {self.message}"


class RiskValidator:
    """Pre-trade validator for PRD §6.1 hard limits.

    `validate_order(...)` returns `None` if the order is acceptable, or
    a `RejectionReason` describing the first violated limit.
    """

    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    @property
    def config(self) -> RiskConfig:
        return self._config

    # --- Per-limit predicates -----------------------------------------

    def check_order_size_pct(
        self, order: Order, account: Account, *, reference_price: Decimal
    ) -> RejectionReason | None:
        """PRD §6.1 row 4: single order > 20% of equity → reject."""
        notional = order.qty * reference_price
        if account.equity <= Decimal(0):
            return RejectionReason(
                "max_order_size_pct",
                f"account equity is {account.equity}, cannot size orders",
            )
        ratio = notional / account.equity
        cap = Decimal(str(self._config.max_order_size_pct))
        if ratio > cap:
            return RejectionReason(
                "max_order_size_pct",
                f"notional ${notional:,.2f} is {ratio * 100:.2f}% of equity, "
                f"exceeds {cap * 100:.2f}% cap",
            )
        return None

    def check_position_size_pct(
        self,
        order: Order,
        account: Account,
        *,
        reference_price: Decimal,
        current_positions: list[Position],
    ) -> RejectionReason | None:
        """PRD §6.1 row 1: resulting single-symbol position > 30% → reject.

        Applied to the *post-fill* position (current + signed delta).
        """
        existing_qty = Decimal(0)
        for p in current_positions:
            if p.symbol == order.symbol:
                existing_qty = p.qty
                break
        delta = order.qty if order.side == OrderSide.BUY else -order.qty
        projected_qty = existing_qty + delta
        projected_notional = abs(projected_qty) * reference_price
        if account.equity <= Decimal(0):
            return RejectionReason(
                "max_position_pct",
                f"account equity is {account.equity}, cannot evaluate position limit",
            )
        ratio = projected_notional / account.equity
        cap = Decimal(str(self._config.max_position_pct))
        if ratio > cap:
            return RejectionReason(
                "max_position_pct",
                f"projected position ${projected_notional:,.2f} is "
                f"{ratio * 100:.2f}% of equity, exceeds {cap * 100:.2f}% cap",
            )
        return None

    def check_price_deviation(
        self, order: Order, *, reference_price: Decimal
    ) -> RejectionReason | None:
        """PRD §6.1 row 5: limit price > 1% from reference → reject.

        Only applies to limit orders; market/MOC/MOO have no price to check.
        """
        if order.type != OrderType.LIMIT or order.limit_price is None:
            return None
        if reference_price <= Decimal(0):
            return RejectionReason(
                "max_price_deviation_pct",
                f"reference_price {reference_price} is non-positive",
            )
        deviation = abs(order.limit_price - reference_price) / reference_price
        cap = Decimal(str(self._config.max_price_deviation_pct))
        if deviation > cap:
            return RejectionReason(
                "max_price_deviation_pct",
                f"limit price {order.limit_price} deviates "
                f"{deviation * 100:.2f}% from reference {reference_price}, "
                f"exceeds {cap * 100:.2f}% cap",
            )
        return None

    # --- Composite -----------------------------------------------------

    def validate_order(
        self,
        order: Order,
        account: Account,
        *,
        reference_price: Decimal,
        current_positions: list[Position] | None = None,
    ) -> RejectionReason | None:
        """Run all limits in order. Return the first rejection, or `None`
        if the order passes every check.
        """
        if reference_price <= Decimal(0):
            return RejectionReason(
                "reference_price",
                f"reference_price {reference_price} must be positive",
            )
        # Order size comes first — it's the cheapest check and the most
        # likely to fire on signal errors.
        rej = self.check_order_size_pct(order, account, reference_price=reference_price)
        if rej is not None:
            return rej
        rej = self.check_position_size_pct(
            order,
            account,
            reference_price=reference_price,
            current_positions=current_positions or [],
        )
        if rej is not None:
            return rej
        rej = self.check_price_deviation(order, reference_price=reference_price)
        if rej is not None:
            return rej
        return None
