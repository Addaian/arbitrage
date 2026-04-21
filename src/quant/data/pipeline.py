"""Bar validation + cleaning.

Any bar that violates OHLC consistency, has null/negative prices, or has
volume <= 0 is dropped and reported. Callers decide what to do with the
report — production callers raise, exploratory callers log and continue.

Adjustment convention: every Bar entering the cache has `adjusted=True`.
Adjustments (splits + dividends) are performed upstream by the loader when
the source exposes them (yfinance does; Alpaca's `adjustment="all"` does
the same on the server side). A loader that returns unadjusted data must
mark bars `adjusted=False`, and `adjust_for_splits_and_dividends` will
refuse to cache them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from quant.types import Bar

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class ValidationReport:
    kept: int
    dropped: int
    reasons: dict[str, int]

    @property
    def drop_rate(self) -> float:
        total = self.kept + self.dropped
        return (self.dropped / total) if total else 0.0


def validate_bars(bars: list[Bar]) -> tuple[list[Bar], ValidationReport]:
    """Drop malformed bars, return (kept, report).

    Bars are already validated at construction (Pydantic), so by the time we
    see them most invariants hold. This catches the edge cases we can't
    express via the type system — e.g. duplicate timestamps or zero volume
    (we don't forbid zero volume in the model because data from holidays
    sometimes ships with it and we may want it visible).
    """
    reasons: dict[str, int] = {}
    kept: list[Bar] = []
    seen: set[tuple[str, object]] = set()

    for bar in bars:
        key = (bar.symbol, bar.ts)
        if key in seen:
            reasons["duplicate"] = reasons.get("duplicate", 0) + 1
            continue
        if bar.volume <= Decimal(0):
            reasons["zero_or_negative_volume"] = reasons.get("zero_or_negative_volume", 0) + 1
            continue
        seen.add(key)
        kept.append(bar)

    report = ValidationReport(
        kept=len(kept),
        dropped=len(bars) - len(kept),
        reasons=reasons,
    )
    return kept, report


def require_adjusted(bars: list[Bar]) -> None:
    """Refuse to proceed with unadjusted bars — the whole pipeline assumes
    split/dividend adjustment has already happened at the source.
    """
    unadjusted = [b for b in bars if not b.adjusted]
    if unadjusted:
        symbols = sorted({b.symbol for b in unadjusted})
        raise ValueError(
            f"{len(unadjusted)} unadjusted bars for {symbols}; loader must set adjusted=True"
        )


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    """Convert a list of Bar to a pandas DataFrame indexed by ts with columns
    (open, high, low, close, volume, adjusted). Decimals are coerced to float
    — feature engineering uses float throughout; the Decimal shape was only
    useful at the persistence boundary.
    """
    import pandas as pd

    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "adjusted"])

    records = [
        {
            "ts": pd.Timestamp(b.ts),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
            "adjusted": b.adjusted,
        }
        for b in bars
    ]
    df = pd.DataFrame.from_records(records).set_index("ts").sort_index()
    return df


def bars_from_ohlcv_frame(frame: pd.DataFrame, *, symbol: str, adjusted: bool = True) -> list[Bar]:
    """Convert a pandas DataFrame with (Open, High, Low, Close, Volume) columns
    indexed by date into a list of `Bar`. Missing/NaN rows are skipped with
    no complaint — callers should run `validate_bars` afterwards to catch
    semantic issues.
    """
    import pandas as pd  # local to keep module import cheap

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"frame missing required columns: {sorted(missing)}")

    bars: list[Bar] = []
    for idx, row in frame.iterrows():
        if any(pd.isna(row[col]) for col in required):
            continue
        ts = idx.date() if hasattr(idx, "date") else idx
        try:
            bars.append(
                Bar(
                    symbol=symbol,
                    ts=ts,
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    volume=Decimal(str(row["Volume"])),
                    adjusted=adjusted,
                )
            )
        except (ValueError, TypeError):
            # Bar() will reject OHLC inconsistencies; treat as noise.
            continue
    return bars
