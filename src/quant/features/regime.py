"""Regime-oriented features built on VIX family indices.

These feed the HMM regime classifier (PRD §5.4 / Week 15). All features are
strictly backward-looking.

Three observables in the V1 feature set:
- Raw VIX level (and the log of it, which is closer to Gaussian)
- Rolling percentile rank of VIX vs its own history
- Term-structure ratio between a short VIX (e.g. VIX9D) and the standard VIX.
  Ratio < 1 → contango (calm), > 1 → backwardation (stress).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def vix_log_level(vix: pd.Series) -> pd.Series:
    """Log of VIX. Closer to Gaussian than the raw level, better for HMM."""
    return np.log(vix.replace(0.0, np.nan))


def vix_percentile(vix: pd.Series, window: int = 252) -> pd.Series:
    """Rolling percentile rank of the current VIX value within its trailing
    `window`-day history. Returns value in [0, 1]; 0.95 ≈ top 5%.

    Uses only data up to and including `t`; the rolling window is
    left-anchored.
    """
    if window <= 0:
        raise ValueError(f"window must be positive, got {window}")

    def _last_pct(x: np.ndarray) -> float:
        current = x[-1]
        if np.isnan(current):
            return np.nan
        return float((x <= current).sum() / len(x))

    return vix.rolling(window=window, min_periods=window).apply(_last_pct, raw=True)


def term_structure_ratio(
    short_vix: pd.Series,
    long_vix: pd.Series,
) -> pd.Series:
    """Ratio of a short-dated VIX to a longer-dated VIX.

    `short_vix / long_vix`: >1 means short-term fear is elevated relative to
    medium term (backwardation, regime stress); <1 is normal contango.
    """
    denom = long_vix.replace(0.0, np.nan)
    return short_vix / denom


def compute_regime_features(
    vix: pd.Series,
    *,
    vix_short: pd.Series | None = None,
    percentile_window: int = 252,
) -> pd.DataFrame:
    """Assemble the regime feature DataFrame.

    Pass `vix_short` (e.g. VIX9D) to include the term-structure ratio; omit
    for a VIX-only set (used during warmup weeks before VIX9D data is
    wired in).
    """
    out = pd.DataFrame(index=vix.index)
    out["vix"] = vix
    out["vix_log"] = vix_log_level(vix)
    out["vix_pct"] = vix_percentile(vix, percentile_window)
    if vix_short is not None:
        aligned = vix_short.reindex(vix.index)
        out["vix_ts_ratio"] = term_structure_ratio(aligned, vix)
    return out
