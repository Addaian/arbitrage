"""EWMA volatility forecaster (RiskMetrics, PRD §5.5 / plan Week 16).

Two entry points:

* `EWMAVolForecaster` — stateful helper that tracks a variance estimate
  across calls. Used by the `LiveRunner` in Wave 16+, where each daily
  cycle updates the forecaster with one new return and reads the
  current annualized vol.
* `forecast_vol_series()` — batch helper for backtests. Thin wrapper
  around `quant.features.technical.ewma_vol` so callers can import
  from one models-namespaced place.

Math (RiskMetrics 1996):
    var_t   = lam * var_{t-1} + (1 - lam) * r_t^2
    vol_t   = sqrt(var_t) * sqrt(periods_per_year)   # annualized

`lam = 0.94` (daily) is the JPMorgan RiskMetrics default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from quant.features.technical import ewma_vol


@dataclass
class EWMAVolForecaster:
    """Stateful RiskMetrics EWMA vol forecaster.

    Usage:
        fc = EWMAVolForecaster(lam=0.94)
        for r in daily_returns:
            fc.update(r)
        print(fc.current_vol())   # 1-step-ahead annualized vol

    Or for a one-shot batch forecast: use `forecast_vol_series(...)`.
    """

    lam: float = 0.94
    periods_per_year: int = 252
    _variance: float = 0.0
    _count: int = 0

    def __post_init__(self) -> None:
        if not 0.0 < self.lam < 1.0:
            raise ValueError(f"lam must be in (0, 1), got {self.lam}")
        if self.periods_per_year <= 0:
            raise ValueError(f"periods_per_year must be positive, got {self.periods_per_year}")

    def update(self, return_: float) -> None:
        """Absorb one new (daily) return into the variance estimate."""
        if self._count == 0:
            self._variance = float(return_) ** 2
        else:
            self._variance = self.lam * self._variance + (1.0 - self.lam) * (float(return_) ** 2)
        self._count += 1

    def current_vol(self) -> float:
        """Annualized 1-step-ahead vol forecast based on state so far.

        Returns 0.0 when no updates have been absorbed yet. Callers
        must guard against that when computing vol-target multipliers.
        """
        if self._count == 0:
            return 0.0
        return math.sqrt(self._variance) * math.sqrt(self.periods_per_year)

    def reset(self) -> None:
        self._variance = 0.0
        self._count = 0

    @property
    def n_updates(self) -> int:
        return self._count


def forecast_vol_series(
    returns: pd.Series,
    *,
    lam: float = 0.94,
    periods_per_year: int = 252,
) -> pd.Series:
    """Batch 1-step-ahead annualized vol forecast on a full return series.

    Thin wrapper over `quant.features.technical.ewma_vol`. Returns an
    annualized vol series aligned on the input's index. Day `t`'s value
    uses returns through `t` — caller must shift by 1 if they want to
    avoid the same-day lookahead (the `apply_regime_overlay` machinery
    already forward-fills, so shifting upstream is the cleanest pattern).
    """
    return ewma_vol(returns, lam=lam, annualize=True, periods_per_year=periods_per_year)
