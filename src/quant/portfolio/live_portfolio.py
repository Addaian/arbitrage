"""Production multi-strategy portfolio for the live runner.

Stacks the validated 3-strategy combination (trend + momentum +
mean-reversion) per `config/strategies.yaml`, then composes the regime
and vol-target overlays multiplicatively per Wave 16 acceptance.

Implements the `SignalStrategy.target_weights(closes) -> pd.DataFrame`
shape so it slots into `LiveRunner`'s existing `signal` parameter.
The mean-reversion sleeve needs OHLC, so the constructor takes a
`highs_lows_provider` callable that the runner-side wiring keeps
consistent with the closes_provider it hands to `LiveRunner`.

Overlays degrade gracefully:
* No HMM model file → regime overlay is skipped (logged WARNING).
* Reference-symbol returns insufficient → vol-target overlay is skipped.
This means a fresh deploy that hasn't run `scripts/train_regime.py` yet
runs the bare combined portfolio (still better than trend-only).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from loguru import logger

from quant.models.hmm_regime import RegimeHMM
from quant.models.volatility import forecast_vol_series
from quant.portfolio.combiner import combine_weights
from quant.portfolio.sizing import (
    apply_regime_overlay,
    regime_multiplier,
    vol_target_multiplier,
)
from quant.signals.mean_reversion import MeanReversionSignal
from quant.signals.momentum import MomentumSignal
from quant.signals.trend import TrendSignal

HighsLowsProvider = Callable[[], tuple[pd.DataFrame, pd.DataFrame]]


@dataclass
class MultiStrategyPortfolio:
    name: str
    trend: TrendSignal
    momentum: MomentumSignal
    mean_rev: MeanReversionSignal
    allocations: dict[str, float]
    cash_symbol: str
    sleeve_universes: dict[str, list[str]]
    highs_lows_provider: HighsLowsProvider
    regime_model_path: Path | None = None
    regime_reference_symbol: str = "SPY"
    target_vol: float = 0.10
    max_gross_exposure: float = 1.0

    def _slice_cols(self, sleeve: str) -> list[str]:
        """Sleeve's risk universe + cash. The yaml is inconsistent about
        whether cash is listed per sleeve; signals require it for
        rebalance remainders, so we always include it here."""
        cols = list(self.sleeve_universes[sleeve])
        if self.cash_symbol not in cols:
            cols.append(self.cash_symbol)
        return cols

    def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
        trend_w = self.trend.target_weights(closes[self._slice_cols("trend")])
        mom_w = self.momentum.target_weights(closes[self._slice_cols("momentum")])

        highs, lows = self.highs_lows_provider()
        mr_cols = self._slice_cols("mean_reversion")
        mr_w = self.mean_rev.target_weights(
            closes[mr_cols],
            highs[mr_cols],
            lows[mr_cols],
        )

        combined = combine_weights(
            {"trend": trend_w, "momentum": mom_w, "mean_reversion": mr_w},
            self.allocations,
        )
        if self.cash_symbol not in combined.columns:
            combined[self.cash_symbol] = 0.0

        multiplier = pd.Series(1.0, index=combined.index)
        regime_mult = self._regime_multiplier(closes)
        if regime_mult is not None:
            multiplier = multiplier * regime_mult.reindex(combined.index).ffill().fillna(1.0)
        vol_mult = self._vol_target_multiplier(closes)
        if vol_mult is not None:
            multiplier = multiplier * vol_mult.reindex(combined.index).ffill().fillna(1.0)

        if (multiplier == 1.0).all():
            return combined
        return apply_regime_overlay(combined, multiplier, cash_symbol=self.cash_symbol)

    def _regime_multiplier(self, closes: pd.DataFrame) -> pd.Series | None:
        if self.regime_model_path is None or not self.regime_model_path.exists():
            logger.warning(
                "regime model not found at {}; running without regime overlay",
                self.regime_model_path,
            )
            return None
        try:
            model = RegimeHMM.load(self.regime_model_path)
            features = RegimeHMM.build_features(closes[self.regime_reference_symbol])
            p_stress = model.stress_probability(features)
            return regime_multiplier(p_stress)
        except Exception as exc:
            logger.warning("regime overlay failed, skipping: {}", exc)
            return None

    def _vol_target_multiplier(self, closes: pd.DataFrame) -> pd.Series | None:
        try:
            ref_returns = closes[self.regime_reference_symbol].pct_change(fill_method=None).dropna()
            if len(ref_returns) < 20:
                return None
            forecast = forecast_vol_series(ref_returns)
            return vol_target_multiplier(
                forecast,
                target_vol=self.target_vol,
                max_gross_exposure=self.max_gross_exposure,
            )
        except Exception as exc:
            logger.warning("vol-target overlay failed, skipping: {}", exc)
            return None
