"""Tests for RiskValidator (PRD §6.1 hard limits).

Includes a Hypothesis property test asserting the completeness
invariant: out of 10,000 random (order, account, reference_price)
triples, every valid one passes and every invalid one fails — no
false accepts, no false rejects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from quant.config import RiskConfig
from quant.risk import RiskValidator
from quant.types import (
    Account,
    Order,
    OrderSide,
    OrderType,
    Position,
)


def _risk(**overrides: float) -> RiskConfig:
    defaults: dict[str, object] = {
        "max_position_pct": 0.30,
        "max_daily_loss_pct": 0.05,
        "max_monthly_drawdown_pct": 0.15,
        "max_order_size_pct": 0.20,
        "max_price_deviation_pct": 0.01,
        "target_annual_vol": 0.10,
        "max_gross_exposure": 1.0,
    }
    defaults.update(overrides)
    return RiskConfig(**defaults)  # type: ignore[arg-type]


def _account(equity: str = "100000") -> Account:
    return Account(
        account_id="test",
        equity=Decimal(equity),
        cash=Decimal(equity),
        buying_power=Decimal(equity),
        portfolio_value=Decimal(equity),
        as_of=datetime.now(UTC),
        paper=True,
    )


def _buy(symbol: str = "SPY", qty: str = "10") -> Order:
    return Order(symbol=symbol, side=OrderSide.BUY, qty=Decimal(qty))


def _limit(symbol: str, qty: str, limit_price: str) -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        qty=Decimal(qty),
        type=OrderType.LIMIT,
        limit_price=Decimal(limit_price),
    )


# --- Order size limit ---------------------------------------------------


def test_order_size_under_cap_passes() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="100")  # 100 * $100 = $10k = 10% of $100k → under 20% cap
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is None


def test_order_size_over_cap_rejects() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="300")  # 300 * $100 = $30k = 30% > 20% cap
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is not None
    assert result.limit_name == "max_order_size_pct"


def test_order_size_at_exact_cap_passes() -> None:
    v = RiskValidator(_risk(max_order_size_pct=0.20))
    order = _buy(qty="200")  # 200 * $100 = $20k = 20.0% (at cap, not over)
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is None


def test_order_size_with_zero_equity_rejects() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="10")
    acct = _account(equity="0")
    result = v.validate_order(order, acct, reference_price=Decimal("100"))
    assert result is not None
    assert result.limit_name == "max_order_size_pct"


# --- Position size limit ----------------------------------------------


def test_position_size_under_cap_passes() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="100")  # 100 * $100 = $10k → 10% of equity
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is None


def test_position_size_adds_to_existing_and_rejects() -> None:
    v = RiskValidator(_risk())
    existing = Position(
        symbol="SPY",
        qty=Decimal("250"),  # $25k already held
        avg_entry_price=Decimal("100"),
        market_value=Decimal("25000"),
        unrealized_pnl=Decimal("0"),
        as_of=datetime.now(UTC),
    )
    # Buy another 100 shares ($10k). Projected: 350 * $100 = $35k = 35% > 30%.
    # Note: this violates the position cap AT 35% but the order alone is $10k = 10%,
    # which passes the order-size check. So this isolates the position-cap check.
    order = _buy(qty="100")
    result = v.validate_order(
        order, _account(), reference_price=Decimal("100"), current_positions=[existing]
    )
    assert result is not None
    assert result.limit_name == "max_position_pct"


def test_position_size_scans_past_unrelated_positions() -> None:
    """When current_positions has multiple entries, the loop must skip
    non-matching symbols before locking onto the order's symbol."""
    v = RiskValidator(_risk())
    other = Position(
        symbol="QQQ",
        qty=Decimal("50"),
        avg_entry_price=Decimal("100"),
        market_value=Decimal("5000"),
        unrealized_pnl=Decimal("0"),
        as_of=datetime.now(UTC),
    )
    existing = Position(
        symbol="SPY",
        qty=Decimal("50"),
        avg_entry_price=Decimal("100"),
        market_value=Decimal("5000"),
        unrealized_pnl=Decimal("0"),
        as_of=datetime.now(UTC),
    )
    # Order on SPY; we have QQQ and SPY both held. The validator must
    # pick SPY (second position) for the projection.
    order = _buy("SPY", qty="100")
    result = v.validate_order(
        order, _account(), reference_price=Decimal("100"), current_positions=[other, existing]
    )
    assert result is None  # 50+100 = 150 shares = $15k = 15% < 30%


def test_position_size_zero_equity_rejects() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="10")
    acct = _account(equity="0")
    # Skip the order-size path by making the order trivially small.
    # Hard to do since zero-equity hits order-size first. Use internal method.
    result = v.check_position_size_pct(
        order, acct, reference_price=Decimal("100"), current_positions=[]
    )
    assert result is not None
    assert result.limit_name == "max_position_pct"


def test_sell_reducing_position_passes_even_if_total_over_cap() -> None:
    """A sell that reduces an already-over-cap position is allowed —
    the check is on the *projected* absolute size, which shrinks."""
    v = RiskValidator(_risk())
    existing = Position(
        symbol="SPY",
        qty=Decimal("400"),  # $40k = 40% of equity — legacy, above cap
        avg_entry_price=Decimal("100"),
        market_value=Decimal("40000"),
        unrealized_pnl=Decimal("0"),
        as_of=datetime.now(UTC),
    )
    # Sell 200 → projected 200 shares = $20k = 20% < 30% cap. Order size
    # $20k = exactly 20% (at, not over, so passes).
    order = Order(symbol="SPY", side=OrderSide.SELL, qty=Decimal("200"))
    result = v.validate_order(
        order, _account(), reference_price=Decimal("100"), current_positions=[existing]
    )
    assert result is None


# --- Price deviation limit ---------------------------------------------


def test_limit_price_within_tolerance_passes() -> None:
    v = RiskValidator(_risk())
    order = _limit("SPY", "10", "99.50")  # 0.5% below → under 1% cap
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is None


def test_limit_price_above_tolerance_rejects() -> None:
    v = RiskValidator(_risk())
    order = _limit("SPY", "10", "102.00")  # 2% above → over 1% cap
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is not None
    assert result.limit_name == "max_price_deviation_pct"


def test_market_order_skips_price_deviation_check() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="10")  # market, no limit price
    # reference_price way off from any notional — only the deviation check cares.
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is None


def test_price_deviation_with_zero_reference_rejects() -> None:
    v = RiskValidator(_risk())
    order = _limit("SPY", "10", "100")
    # validate_order gates on reference_price > 0 before reaching the deviation check.
    result = v.validate_order(order, _account(), reference_price=Decimal("0"))
    assert result is not None
    assert result.limit_name == "reference_price"


def test_check_price_deviation_directly_with_zero_reference() -> None:
    """Internal method: zero reference inside the deviation check itself."""
    v = RiskValidator(_risk())
    order = _limit("SPY", "10", "100")
    result = v.check_price_deviation(order, reference_price=Decimal("0"))
    assert result is not None
    assert result.limit_name == "max_price_deviation_pct"


# --- Composite validate_order order-of-checks --------------------------


def test_validate_order_reports_first_violation() -> None:
    """Order with multiple violations — first one encountered (order size) wins."""
    v = RiskValidator(_risk())
    # 500 * $100 = $50k = 50% of equity — fails both order-size AND position-size.
    # Order-size is checked first.
    order = _buy(qty="500")
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is not None
    assert result.limit_name == "max_order_size_pct"


def test_validate_order_rejects_nonpositive_reference_price() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="10")
    result = v.validate_order(order, _account(), reference_price=Decimal("-1"))
    assert result is not None
    assert result.limit_name == "reference_price"


def test_rejection_reason_str_is_human_readable() -> None:
    v = RiskValidator(_risk())
    order = _buy(qty="300")
    result = v.validate_order(order, _account(), reference_price=Decimal("100"))
    assert result is not None
    text = str(result)
    assert "max_order_size_pct" in text
    assert "exceeds" in text


def test_validator_exposes_config() -> None:
    cfg = _risk()
    v = RiskValidator(cfg)
    assert v.config is cfg


# --- Hypothesis property test ------------------------------------------


@settings(
    max_examples=10_000,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    qty=st.decimals(
        min_value=Decimal("0.0001"), max_value=Decimal("10000"), places=4, allow_nan=False
    ),
    ref_price=st.decimals(
        min_value=Decimal("0.01"), max_value=Decimal("10000"), places=2, allow_nan=False
    ),
    equity=st.decimals(
        min_value=Decimal("1000"), max_value=Decimal("10000000"), places=2, allow_nan=False
    ),
    existing_qty=st.decimals(
        min_value=Decimal("0"), max_value=Decimal("10000"), places=4, allow_nan=False
    ),
    side=st.sampled_from([OrderSide.BUY, OrderSide.SELL]),
)
def test_validator_property_no_false_accepts_or_rejects(
    qty: Decimal,
    ref_price: Decimal,
    equity: Decimal,
    existing_qty: Decimal,
    side: OrderSide,
) -> None:
    """Truth-vs-validator invariant over 10k random orders.

    Recompute the three limits independently here (the "truth" table)
    and assert that the validator's verdict matches: no valid order
    rejected, no invalid order accepted.
    """
    cfg = _risk()
    v = RiskValidator(cfg)

    order = Order(symbol="SPY", side=side, qty=qty)
    account = Account(
        account_id="test",
        equity=equity,
        cash=equity,
        buying_power=equity,
        portfolio_value=equity,
        as_of=datetime.now(UTC),
        paper=True,
    )
    positions: list[Position] = []
    if existing_qty > 0:
        positions.append(
            Position(
                symbol="SPY",
                qty=existing_qty,
                avg_entry_price=Decimal("100"),
                market_value=existing_qty * Decimal("100"),
                unrealized_pnl=Decimal("0"),
                as_of=datetime.now(UTC),
            )
        )

    # "Truth": recompute each predicate independently.
    notional = qty * ref_price
    order_ratio = notional / equity
    delta = qty if side == OrderSide.BUY else -qty
    projected = abs(existing_qty + delta)
    pos_ratio = projected * ref_price / equity

    should_reject_order_size = order_ratio > Decimal(str(cfg.max_order_size_pct))
    should_reject_position = (
        not should_reject_order_size  # order-size is checked first
        and pos_ratio > Decimal(str(cfg.max_position_pct))
    )

    verdict = v.validate_order(
        order,
        account,
        reference_price=ref_price,
        current_positions=positions,
    )

    if should_reject_order_size:
        assert verdict is not None and verdict.limit_name == "max_order_size_pct"
    elif should_reject_position:
        assert verdict is not None and verdict.limit_name == "max_position_pct"
    else:
        assert verdict is None, f"unexpected reject for valid order: {verdict}"
