"""Tests for the bar validation pipeline."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from quant.data.pipeline import (
    bars_from_ohlcv_frame,
    require_adjusted,
    validate_bars,
)
from quant.types import Bar


def _bar(ts: date, *, volume: Decimal = Decimal("100"), adjusted: bool = True) -> Bar:
    return Bar(
        symbol="SPY",
        ts=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=volume,
        adjusted=adjusted,
    )


def test_validate_bars_drops_zero_volume() -> None:
    bars = [_bar(date(2026, 1, 2)), _bar(date(2026, 1, 3), volume=Decimal("0"))]
    kept, report = validate_bars(bars)
    assert len(kept) == 1
    assert report.dropped == 1
    assert report.reasons == {"zero_or_negative_volume": 1}


def test_validate_bars_drops_duplicates() -> None:
    d = date(2026, 1, 2)
    bars = [_bar(d), _bar(d)]
    kept, report = validate_bars(bars)
    assert len(kept) == 1
    assert report.reasons == {"duplicate": 1}


def test_validate_bars_drop_rate() -> None:
    bars = [_bar(date(2026, 1, d)) for d in (2, 3, 4)] + [
        _bar(date(2026, 1, 5), volume=Decimal("0"))
    ]
    _, report = validate_bars(bars)
    assert report.drop_rate == pytest.approx(0.25)


def test_require_adjusted_raises_on_unadjusted() -> None:
    bars = [_bar(date(2026, 1, 2), adjusted=False)]
    with pytest.raises(ValueError, match="unadjusted bars"):
        require_adjusted(bars)


def test_require_adjusted_silent_on_clean() -> None:
    require_adjusted([_bar(date(2026, 1, 2))])  # should not raise


def test_bars_from_ohlcv_frame_converts_pandas() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1_000_000, 900_000],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-03"]),
    )
    bars = bars_from_ohlcv_frame(frame, symbol="SPY")
    assert len(bars) == 2
    assert bars[0].symbol == "SPY"
    assert bars[0].ts == date(2026, 1, 2)
    assert bars[0].adjusted is True


def test_bars_from_ohlcv_frame_skips_nans() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0, float("nan")],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1_000_000, 900_000],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-03"]),
    )
    bars = bars_from_ohlcv_frame(frame, symbol="SPY")
    assert len(bars) == 1
    assert bars[0].ts == date(2026, 1, 2)


def test_bars_from_ohlcv_frame_skips_ohlc_violations() -> None:
    # High < Low — Bar() will reject; loader should silently drop.
    frame = pd.DataFrame(
        {
            "Open": [100.0, 100.0],
            "High": [102.0, 98.0],  # second row: High < Low
            "Low": [99.0, 99.0],
            "Close": [101.0, 100.0],
            "Volume": [1_000, 1_000],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-03"]),
    )
    bars = bars_from_ohlcv_frame(frame, symbol="SPY")
    assert len(bars) == 1


def test_bars_from_ohlcv_frame_requires_columns() -> None:
    frame = pd.DataFrame({"Open": [1.0]})
    with pytest.raises(ValueError, match="missing required columns"):
        bars_from_ohlcv_frame(frame, symbol="SPY")
