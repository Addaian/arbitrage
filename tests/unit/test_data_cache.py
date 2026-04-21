"""Tests for the Parquet bar cache."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from quant.data.cache import CacheKey, ParquetBarCache
from quant.types import Bar


def _bar(ts: date, price: str) -> Bar:
    return Bar(
        symbol="SPY",
        ts=ts,
        open=Decimal(price),
        high=Decimal(price) + Decimal("1"),
        low=Decimal(price) - Decimal("1"),
        close=Decimal(price),
        volume=Decimal("1000"),
    )


def test_put_then_get_round_trips(tmp_path: Path) -> None:
    cache = ParquetBarCache(tmp_path)
    key = CacheKey(symbol="SPY", start=date(2026, 1, 1), end=date(2026, 1, 3))
    bars = [_bar(date(2026, 1, 2), "100"), _bar(date(2026, 1, 3), "101")]
    cache.put(key, bars)

    got = cache.get(key)
    assert got is not None
    assert len(got) == 2
    assert got[0].ts == date(2026, 1, 2)
    assert got[0].close == Decimal("100")
    assert got[0].adjusted is True


def test_get_returns_none_for_missing(tmp_path: Path) -> None:
    cache = ParquetBarCache(tmp_path)
    key = CacheKey(symbol="SPY", start=date(2026, 1, 1), end=date(2026, 1, 3))
    assert cache.get(key) is None
    assert cache.exists(key) is False


def test_invalidate_removes_file(tmp_path: Path) -> None:
    cache = ParquetBarCache(tmp_path)
    key = CacheKey(symbol="SPY", start=date(2026, 1, 1), end=date(2026, 1, 3))
    cache.put(key, [_bar(date(2026, 1, 2), "100")])
    assert cache.exists(key)

    removed = cache.invalidate(key)
    assert removed is True
    assert cache.exists(key) is False

    # Second invalidate is a no-op.
    assert cache.invalidate(key) is False


def test_cache_hit_avoids_loader_call(tmp_path: Path) -> None:
    """If a key is already cached, callers should be able to short-circuit
    without ever invoking the loader. We simulate a loader that blows up on
    any call; a cache hit should still succeed."""

    cache = ParquetBarCache(tmp_path)
    key = CacheKey(symbol="SPY", start=date(2026, 1, 1), end=date(2026, 1, 3))
    cache.put(key, [_bar(date(2026, 1, 2), "100")])

    call_count = {"n": 0}

    class BrokenLoader:
        def fetch(self, symbol: str, start: date, end: date) -> list[Bar]:
            call_count["n"] += 1
            raise RuntimeError("loader should not be called on cache hit")

    loader = BrokenLoader()
    bars = cache.get(key) if cache.exists(key) else loader.fetch(key.symbol, key.start, key.end)
    assert len(bars) == 1
    assert call_count["n"] == 0


def test_cache_path_is_deterministic(tmp_path: Path) -> None:
    cache = ParquetBarCache(tmp_path)
    key = CacheKey(symbol="SPY", start=date(2026, 1, 1), end=date(2026, 12, 31))
    expected = tmp_path / "SPY" / "2026-01-01_2026-12-31.parquet"
    assert key.as_path(tmp_path) == expected
    cache.put(key, [_bar(date(2026, 6, 1), "100")])
    assert expected.exists()
