"""Loader tests. Real network calls are skipped — we focus on the frame
converter and retry wiring. YFinance/Alpaca happy paths are exercised
manually via `scripts/backfill.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from quant.data.loaders import _RETRY, BarLoader
from quant.data.pipeline import bars_from_ohlcv_frame


def test_loader_protocol_runtime_check() -> None:
    class Stub:
        def fetch(self, symbol: str, start: date, end: date) -> list:
            return []

    assert isinstance(Stub(), BarLoader)


def test_loader_protocol_rejects_wrong_shape() -> None:
    class NotALoader:
        def other(self) -> None:
            pass

    assert not isinstance(NotALoader(), BarLoader)


def test_retry_decorator_retries_on_transient_error() -> None:
    calls = {"n": 0}

    @_RETRY
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_decorator_gives_up_after_budget() -> None:
    @_RETRY
    def always_fails() -> None:
        raise ConnectionError("permanent")

    with pytest.raises(ConnectionError):
        always_fails()


def test_retry_decorator_does_not_retry_unknown_errors() -> None:
    calls = {"n": 0}

    @_RETRY
    def bug() -> None:
        calls["n"] += 1
        raise ValueError("programming error")

    with pytest.raises(ValueError, match="programming error"):
        bug()
    # ValueErrors aren't in the retry allowlist → one attempt only.
    assert calls["n"] == 1


def test_frame_to_bars_preserves_values() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.25, 101.10],
            "High": [102.00, 102.75],
            "Low": [99.50, 100.90],
            "Close": [101.00, 102.50],
            "Volume": [1_234_567, 987_654],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-03"]),
    )
    bars = bars_from_ohlcv_frame(frame, symbol="SPY")
    assert [b.ts for b in bars] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert bars[0].open == Decimal("100.25")
    assert bars[1].volume == Decimal("987654")
