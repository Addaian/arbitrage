"""On-disk Parquet cache for daily bars.

Layout: `<root>/parquet/<symbol>/<start>_<end>.parquet`. One file per
(symbol, start, end) range. Cache hits are O(single file read), so
backfilling the full V1 universe a second time costs a few dozen file
reads (<5s per the acceptance criterion).

We store full-precision `Decimal` prices as strings because Parquet's
native decimal type is clunkier to round-trip — the cost is negligible
at ~5k rows/ETF × 20 years.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from quant.types import Bar

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class CacheKey:
    symbol: str
    start: date
    end: date

    def as_path(self, root: Path) -> Path:
        return root / self.symbol / f"{self.start.isoformat()}_{self.end.isoformat()}.parquet"


class ParquetBarCache:
    """Minimal Parquet cache. Read/write in pandas; return domain `Bar`s."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def exists(self, key: CacheKey) -> bool:
        return key.as_path(self.root).exists()

    def get(self, key: CacheKey) -> list[Bar] | None:
        path = key.as_path(self.root)
        if not path.exists():
            return None
        return _read(path, symbol=key.symbol)

    def put(self, key: CacheKey, bars: list[Bar]) -> Path:
        path = key.as_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, bars)
        return path

    def invalidate(self, key: CacheKey) -> bool:
        path = key.as_path(self.root)
        if path.exists():
            path.unlink()
            return True
        return False


# --- serialization ------------------------------------------------------


def _read(path: Path, *, symbol: str) -> list[Bar]:
    import pandas as pd

    df = pd.read_parquet(path)
    bars: list[Bar] = []
    for _, row in df.iterrows():
        bars.append(
            Bar(
                symbol=symbol,
                ts=_to_date(row["ts"]),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                adjusted=bool(row["adjusted"]),
            )
        )
    return bars


def _write(path: Path, bars: list[Bar]) -> None:
    import pandas as pd

    records = [
        {
            "ts": pd.Timestamp(b.ts),
            "open": str(b.open),
            "high": str(b.high),
            "low": str(b.low),
            "close": str(b.close),
            "volume": str(b.volume),
            "adjusted": b.adjusted,
        }
        for b in bars
    ]
    df = pd.DataFrame.from_records(records)
    df.to_parquet(path, index=False, compression="zstd")


def _to_date(v: object) -> date:
    if isinstance(v, date):
        return v
    # pandas Timestamp / numpy datetime64 / str
    import pandas as pd

    return pd.Timestamp(v).date()
