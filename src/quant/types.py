"""Shared domain types used across data, signals, execution, and storage.

These are the canonical shapes that cross module boundaries. The broker
interface speaks in `Order` / `Fill` / `Position` / `Account`; the data layer
speaks in `Bar`; the signal layer speaks in `Signal`. Pydantic validates at the
boundary so downstream code can trust the shapes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Enums ---------------------------------------------------------------


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    MOC = "moc"  # market-on-close
    MOO = "moo"  # market-on-open


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


# --- Primitives ----------------------------------------------------------

Symbol = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$", description="Ticker symbol")]
NonNegativeDecimal = Annotated[Decimal, Field(ge=Decimal(0))]
PositiveDecimal = Annotated[Decimal, Field(gt=Decimal(0))]


# --- Models --------------------------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class Bar(_Base):
    """Daily OHLCV bar, split/dividend-adjusted by convention."""

    symbol: Symbol
    ts: date
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal
    adjusted: bool = True

    @model_validator(mode="after")
    def _check_ohlc_consistency(self) -> Bar:
        if self.high < self.low:
            raise ValueError(f"high < low for {self.symbol} on {self.ts}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open outside [low, high] for {self.symbol} on {self.ts}")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close outside [low, high] for {self.symbol} on {self.ts}")
        return self


class Signal(_Base):
    """A strategy's view at a point in time: target weight for a symbol.

    `target_weight` is the fraction of the strategy's own sleeve allocated to
    this symbol — portfolio-level sizing is handled later by the combiner.
    """

    strategy: str
    symbol: Symbol
    ts: date
    direction: SignalDirection
    target_weight: float = Field(ge=-1.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, float | int | str | bool] = Field(default_factory=dict)


class Order(_Base):
    """A request to submit to the broker."""

    client_order_id: UUID = Field(default_factory=uuid4)
    symbol: Symbol
    side: OrderSide
    qty: PositiveDecimal
    type: OrderType = OrderType.MARKET
    limit_price: PositiveDecimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy: str | None = None
    submitted_at: datetime | None = None

    @model_validator(mode="after")
    def _limit_requires_price(self) -> Order:
        if self.type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.type != OrderType.LIMIT and self.limit_price is not None:
            raise ValueError(f"{self.type} must not set limit_price")
        return self


class Fill(_Base):
    """Realized fill for an order (may be partial)."""

    order_id: UUID
    broker_fill_id: str
    symbol: Symbol
    side: OrderSide
    qty: PositiveDecimal
    price: PositiveDecimal
    ts: datetime
    commission: NonNegativeDecimal = Decimal(0)


class OrderResult(_Base):
    """What the broker returns after accepting/rejecting an order."""

    order_id: UUID
    broker_order_id: str | None = None
    status: OrderStatus
    submitted_at: datetime
    reason: str | None = None  # populated on rejection


class Position(_Base):
    """Current holding in a symbol."""

    symbol: Symbol
    qty: Decimal  # can be zero or negative (short)
    avg_entry_price: PositiveDecimal
    market_value: Decimal
    unrealized_pnl: Decimal
    as_of: datetime

    @field_validator("qty")
    @classmethod
    def _non_zero_or_report(cls, v: Decimal) -> Decimal:
        # Zero qty is valid (just-closed position snapshot); no constraint beyond typing.
        return v


class Account(_Base):
    """Broker account snapshot."""

    account_id: str
    equity: NonNegativeDecimal
    cash: Decimal
    buying_power: NonNegativeDecimal
    portfolio_value: NonNegativeDecimal
    as_of: datetime
    paper: bool
    pattern_day_trader: bool = False
