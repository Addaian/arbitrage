"""End-to-end smoke test for `_build_default_runner` + `run_daily_cycle`.

Locks in production wiring against the two real-world bug classes that
slipped past 487 unit tests on 2026-04-26:

1. `_load_cached_ohlc` picking the lexicographically-last parquet
   (narrow recent file) instead of the widest-coverage one.
2. `MultiStrategyPortfolio` slicing closes by sleeve universe without
   including the cash symbol the underlying signals require — the
   strategies.yaml entry for momentum/mean_reversion omits cash.

Uses a synthetic Parquet cache under `tmp_path` so it's CI-safe — no
dependency on whatever's in `data/parquet/` locally. Runs `dry_run=True`
so no broker calls and no Postgres needed.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from quant.data.cache import CacheKey, ParquetBarCache
from quant.live.runner import _build_default_runner, _load_cached_ohlc
from quant.portfolio.live_portfolio import MultiStrategyPortfolio
from quant.types import Bar

# Production universe matches config/strategies.yaml union + cash.
_UNIVERSE = ["SPY", "QQQ", "EFA", "EEM", "GLD", "IEF", "TLT", "VNQ", "DBC", "XLE", "SGOV"]


def _make_bars(symbol: str, start: date, end: date, rng: np.random.Generator) -> list[Bar]:
    idx = pd.date_range(start, end, freq="B")
    n = len(idx)
    log_returns = rng.normal(0.0003, 0.012, size=n)
    prices = 100.0 * np.exp(np.cumsum(log_returns))
    return [
        Bar(
            symbol=symbol,
            ts=ts.date(),
            open=Decimal(f"{px:.4f}"),
            high=Decimal(f"{px * 1.005:.4f}"),
            low=Decimal(f"{px * 0.995:.4f}"),
            close=Decimal(f"{px:.4f}"),
            volume=Decimal("1000000"),
            adjusted=True,
        )
        for ts, px in zip(idx, prices, strict=False)
    ]


def _seed_cache(cache_root: Path, symbols: list[str]) -> None:
    """Write two parquets per symbol: a wide 6-year one (start 2020) +
    a narrow 60-day one (start ~last quarter). The narrow file's start
    date sorts lexicographically AFTER the wide file's start, which is
    the exact condition that previously made the loader pick the narrow
    one.
    """
    rng = np.random.default_rng(42)
    cache = ParquetBarCache(cache_root)
    end_d = date(2026, 4, 24)

    start_wide = date(2020, 6, 1)
    start_narrow = end_d - timedelta(days=60)

    for sym in symbols:
        bars_wide = _make_bars(sym, start_wide, end_d, rng)
        cache.put(CacheKey(symbol=sym, start=start_wide, end=end_d), bars_wide)
        bars_narrow = bars_wide[-60:]
        cache.put(CacheKey(symbol=sym, start=start_narrow, end=end_d), bars_narrow)


@pytest.fixture
def synthetic_cache(tmp_path, monkeypatch):
    cache_root = tmp_path / "parquet"
    cache_root.mkdir(parents=True)
    _seed_cache(cache_root, _UNIVERSE)

    fake_settings = MagicMock()
    fake_settings.alpaca_api_key = None
    fake_settings.alpaca_api_secret = None
    fake_settings.quant_env = "dev"
    fake_settings.paper_mode = True
    fake_settings.quant_data_dir = tmp_path
    fake_settings.discord_webhook_url = None
    fake_settings.quant_killswitch_file = tmp_path / "HALT"
    monkeypatch.setattr("quant.live.runner.get_settings", lambda: fake_settings)

    return tmp_path


def test_load_cached_ohlc_picks_widest_parquet(synthetic_cache: Path) -> None:
    """Regression: with a wide and a narrow parquet in the same dir,
    `_load_cached_ohlc` must pick the wide one. `parquets[-1]` (the
    pre-fix code) returned the narrow file because its start-date
    sorts lexicographically last."""
    cache_root = synthetic_cache / "parquet"
    closes, highs, lows = _load_cached_ohlc(cache_root, ["SPY"], window=10_000)

    assert len(closes) > 1000, (
        f"loader picked the narrow parquet: only {len(closes)} bars "
        "(expected ~1500 from the wide parquet). Lexicographic-sort bug regressed."
    )
    assert closes.shape == highs.shape == lows.shape


def test_full_production_cycle_runs_without_error(synthetic_cache: Path) -> None:
    """End-to-end: production runner with multi-strategy + overlays runs
    a complete cycle, produces non-empty target weights, sums to ~1.0."""
    runner = _build_default_runner(broker_kind="paper", dry_run=True, persist=False)

    assert isinstance(runner._signal, MultiStrategyPortfolio), (  # type: ignore[attr-defined]
        "production runner must use MultiStrategyPortfolio, not a single-strategy stand-in"
    )
    portfolio = runner._signal  # type: ignore[attr-defined]
    assert set(portfolio.allocations.keys()) == {"trend", "momentum", "mean_reversion"}

    result = asyncio.run(runner.run_daily_cycle())

    assert result.target_weights, "cycle produced no target weights"
    total = sum(float(w) for w in result.target_weights.values())
    assert 0.95 <= total <= 1.05, f"target weights should sum to ~1.0, got {total:.4f}"
    assert result.dry_run is True
    assert not result.submitted_orders, "dry-run must not submit orders"


def test_multi_strategy_slices_include_cash(synthetic_cache: Path) -> None:
    """Regression: every sleeve slice must include the cash symbol even
    when strategies.yaml's universe entry for that sleeve omits it.
    Trend's yaml entry includes SGOV; momentum/mean_reversion don't —
    yet all three signals require cash for rebalance remainders.
    """
    runner = _build_default_runner(broker_kind="paper", dry_run=True, persist=False)
    portfolio = runner._signal  # type: ignore[attr-defined]
    assert isinstance(portfolio, MultiStrategyPortfolio)

    for sleeve in ("trend", "momentum", "mean_reversion"):
        cols = portfolio._slice_cols(sleeve)
        assert portfolio.cash_symbol in cols, (
            f"sleeve {sleeve!r} slice missing cash {portfolio.cash_symbol!r}: {cols}"
        )


def test_cycle_with_killswitch_engaged_flattens(synthetic_cache: Path) -> None:
    """Sanity: the production runner respects the killswitch even when
    constructed with the multi-strategy assembly."""
    runner = _build_default_runner(broker_kind="paper", dry_run=True, persist=False)
    runner._killswitch.engage(reason="e2e test")  # type: ignore[attr-defined]
    try:
        result = asyncio.run(runner.run_daily_cycle())
        assert result.errors and "killswitch engaged" in result.errors[0]
        assert result.target_weights == {}
    finally:
        runner._killswitch.disengage()  # type: ignore[attr-defined]
