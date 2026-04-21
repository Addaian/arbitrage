"""Data loaders with a shared interface.

`BarLoader` is the protocol that the cache + backfill script speak against.
Both real implementations (`YFinanceLoader`, `AlpacaLoader`) return bars in
the canonical `list[Bar]` form. Retries with exponential backoff are
provided by `tenacity`; failures past the retry budget surface to the
caller so the backfill script can halt rather than silently skip symbols.

Adjustments:
- yfinance: request `auto_adjust=True` → split/dividend-adjusted OHLC.
- Alpaca: request `adjustment=Adjustment.ALL` → same semantics.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from quant.data.pipeline import bars_from_ohlcv_frame
from quant.types import Bar

if TYPE_CHECKING:
    from pydantic import SecretStr

log = logging.getLogger(__name__)


# --- Protocol -----------------------------------------------------------


@runtime_checkable
class BarLoader(Protocol):
    """Shared shape for historical bar loaders."""

    def fetch(self, symbol: str, start: date, end: date) -> list[Bar]: ...


# --- Retry policy -------------------------------------------------------

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)


# --- YFinance -----------------------------------------------------------


class YFinanceLoader:
    """yfinance EOD loader.

    Research-grade data — fine for backtests, not suitable as the sole
    source for live trading (PRD §3.2).
    """

    name = "yfinance"

    def fetch(self, symbol: str, start: date, end: date) -> list[Bar]:
        return self._fetch(symbol, start, end)

    @_RETRY
    def _fetch(self, symbol: str, start: date, end: date) -> list[Bar]:
        import yfinance as yf

        # yfinance's `end` is exclusive, bump by 1 so callers can pass inclusive ranges.
        end_exclusive = end.toordinal() + 1
        end_inclusive_str = date.fromordinal(end_exclusive).isoformat()

        ticker = yf.Ticker(symbol)
        df = ticker.history(
            start=start.isoformat(),
            end=end_inclusive_str,
            interval="1d",
            auto_adjust=True,
            actions=False,
            raise_errors=True,
        )
        if df.empty:
            return []
        return bars_from_ohlcv_frame(df, symbol=symbol, adjusted=True)


# --- Alpaca -------------------------------------------------------------


class AlpacaLoader:
    """Alpaca historical bars via `alpaca-py`.

    Uses the free IEX feed by default; SIP is available on paid tiers but
    not required for V1 (daily bars only).
    """

    name = "alpaca"

    def __init__(
        self,
        api_key: SecretStr | str,
        api_secret: SecretStr | str,
        *,
        use_iex: bool = True,
    ) -> None:
        self._key = _unwrap_secret(api_key)
        self._secret = _unwrap_secret(api_secret)
        self._use_iex = use_iex

    def fetch(self, symbol: str, start: date, end: date) -> list[Bar]:
        return self._fetch(symbol, start, end)

    @_RETRY
    def _fetch(self, symbol: str, start: date, end: date) -> list[Bar]:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import Adjustment, DataFeed

        client = StockHistoricalDataClient(self._key, self._secret)
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=datetime.combine(start, time.min),
            end=datetime.combine(end, time.max),
            adjustment=Adjustment.ALL,
            feed=DataFeed.IEX if self._use_iex else DataFeed.SIP,
        )
        bars_by_symbol = client.get_stock_bars(req).data
        source = bars_by_symbol.get(symbol, [])
        out: list[Bar] = []
        for raw in source:
            try:
                out.append(
                    Bar(
                        symbol=symbol,
                        ts=raw.timestamp.date(),
                        open=Decimal(str(raw.open)),
                        high=Decimal(str(raw.high)),
                        low=Decimal(str(raw.low)),
                        close=Decimal(str(raw.close)),
                        volume=Decimal(str(raw.volume)),
                        adjusted=True,
                    )
                )
            except ValueError:
                continue
        return out


# --- helpers ------------------------------------------------------------


def _unwrap_secret(v: SecretStr | str) -> str:
    if hasattr(v, "get_secret_value"):
        return v.get_secret_value()  # type: ignore[union-attr]
    return str(v)


__all__ = ["AlpacaLoader", "BarLoader", "RetryError", "YFinanceLoader"]
