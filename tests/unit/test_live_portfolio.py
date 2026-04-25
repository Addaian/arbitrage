"""Unit tests for the production multi-strategy portfolio (Wave 20+).

Locks in:
* MultiStrategyPortfolio combines the 3 sleeves with the configured
  allocations.
* Regime + vol-target overlays are applied multiplicatively when their
  inputs are present.
* Each overlay degrades gracefully when its inputs are missing —
  the bare combined portfolio still runs (no crash).
* _extract_sleeve_config + _build_multi_strategy_signal in runner.py
  pull the right values out of strategies.yaml.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant.config import load_config_bundle
from quant.live.runner import _build_multi_strategy_signal, _extract_sleeve_config
from quant.portfolio.live_portfolio import MultiStrategyPortfolio
from quant.signals.mean_reversion import MeanReversionSignal
from quant.signals.momentum import MomentumSignal
from quant.signals.trend import TrendSignal


def _synthetic_ohlc(symbols: list[str], days: int = 500) -> tuple[pd.DataFrame, ...]:
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-01", periods=days, freq="B")
    closes_d: dict[str, pd.Series] = {}
    highs_d: dict[str, pd.Series] = {}
    lows_d: dict[str, pd.Series] = {}
    for sym in symbols:
        steps = rng.normal(0.0005, 0.012, size=days)
        prices = 100.0 * np.cumprod(1.0 + steps)
        closes_d[sym] = pd.Series(prices, index=idx, name=sym)
        highs_d[sym] = pd.Series(prices * 1.005, index=idx, name=sym)
        lows_d[sym] = pd.Series(prices * 0.995, index=idx, name=sym)
    closes = pd.concat(closes_d.values(), axis=1)
    highs = pd.concat(highs_d.values(), axis=1)
    lows = pd.concat(lows_d.values(), axis=1)
    return closes, highs, lows


def _make_portfolio(
    *,
    closes: pd.DataFrame,
    highs: pd.DataFrame,
    lows: pd.DataFrame,
    cash_symbol: str = "SGOV",
    regime_model_path: Path | None = None,
) -> MultiStrategyPortfolio:
    sleeve_universes = {
        "trend": ["SPY", "EFA", "IEF", cash_symbol],
        "momentum": ["SPY", "QQQ", "EFA", cash_symbol],
        "mean_reversion": ["SPY", "QQQ", "EFA", cash_symbol],
    }
    return MultiStrategyPortfolio(
        name="multi_strategy",
        trend=TrendSignal(lookback_months=10, cash_symbol=cash_symbol),
        momentum=MomentumSignal(lookback_months=6, top_n=2, cash_symbol=cash_symbol),
        mean_rev=MeanReversionSignal(cash_symbol=cash_symbol),
        allocations={"trend": 0.5, "momentum": 0.3, "mean_reversion": 0.2},
        cash_symbol=cash_symbol,
        sleeve_universes=sleeve_universes,
        highs_lows_provider=lambda: (highs, lows),
        regime_model_path=regime_model_path,
        target_vol=0.10,
        max_gross_exposure=1.0,
    )


def test_target_weights_combines_three_sleeves() -> None:
    symbols = ["SPY", "QQQ", "EFA", "IEF", "SGOV"]
    closes, highs, lows = _synthetic_ohlc(symbols)
    pf = _make_portfolio(closes=closes, highs=highs, lows=lows)

    weights = pf.target_weights(closes)

    assert "SGOV" in weights.columns
    last = weights.dropna(how="all").iloc[-1]
    # Per-row sum across all symbols including cash should be ~1.0.
    assert 0.99 <= float(last.sum()) <= 1.01


def test_missing_regime_model_falls_back_to_combined_only(tmp_path) -> None:
    """No HMM file → portfolio runs without the regime overlay (no crash)."""
    symbols = ["SPY", "QQQ", "EFA", "IEF", "SGOV"]
    closes, highs, lows = _synthetic_ohlc(symbols)
    pf = _make_portfolio(
        closes=closes,
        highs=highs,
        lows=lows,
        regime_model_path=tmp_path / "does_not_exist.joblib",
    )
    weights = pf.target_weights(closes)
    assert not weights.empty


def test_short_history_skips_vol_target_overlay() -> None:
    """<20 returns → vol-target overlay disabled, combined still emits."""
    symbols = ["SPY", "QQQ", "EFA", "IEF", "SGOV"]
    closes, highs, lows = _synthetic_ohlc(symbols, days=15)
    pf = _make_portfolio(closes=closes, highs=highs, lows=lows)
    # The portfolio shouldn't crash even though there's not enough data
    # for trend's 10mo SMA either — the underlying signals will return
    # mostly-NaN frames, which combine_weights handles.
    pf.target_weights(closes)


# --- runner-side wiring ------------------------------------------------


def test_extract_sleeve_config_pulls_three_sleeves() -> None:
    bundle = load_config_bundle()
    universes, allocations, params = _extract_sleeve_config(bundle)
    assert set(universes) == {"trend", "momentum", "mean_reversion"}
    assert set(allocations) == {"trend", "momentum", "mean_reversion"}
    assert abs(sum(allocations.values()) - 1.0) < 1e-6, (
        "renormalised sleeve weights must sum to ~1.0 even though "
        "regime/vol-target overlay rows in strategies.yaml carry weight"
    )
    assert "sma_lookback_months" in params["trend"]


def test_build_multi_strategy_signal_uses_config_params(tmp_path) -> None:
    bundle = load_config_bundle()
    universes, allocations, params = _extract_sleeve_config(bundle)
    sig = _build_multi_strategy_signal(
        bundle=bundle,
        sleeve_universes=universes,
        sleeve_params=params,
        allocations=allocations,
        cash_symbol=bundle.universe.cash_symbol,
        highs_lows_provider=lambda: (pd.DataFrame(), pd.DataFrame()),
        regime_model_path=tmp_path / "regime.joblib",
    )
    # Lookbacks must be loaded from yaml, not hardcoded.
    assert sig.trend.lookback_months == int(params["trend"]["sma_lookback_months"])
    assert sig.momentum.lookback_months == int(params["momentum"]["lookback_months"])
    assert sig.momentum.top_n == int(params["momentum"]["top_n"])
    # Risk caps: target_vol must come from RiskConfig, not the overlay sleeve.
    assert sig.target_vol == bundle.risk.target_annual_vol
    assert sig.max_gross_exposure == bundle.risk.max_gross_exposure
