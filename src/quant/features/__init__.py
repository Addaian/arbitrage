"""Feature engineering: technical, cross-sectional, regime.

Every feature is strictly backward-looking. The `test_no_lookahead` property
test enforces this across the module surface.
"""

from quant.features.cross_sectional import (
    demean_cross_sectional,
    rank_cross_sectional,
    top_n_mask,
    universe_momentum,
    zscore_cross_sectional,
)
from quant.features.regime import (
    compute_regime_features,
    term_structure_ratio,
    vix_log_level,
    vix_percentile,
)
from quant.features.technical import (
    atr,
    compute_technical_features,
    ema,
    ewma_vol,
    ibs,
    log_returns,
    returns,
    rolling_vol,
    rsi,
    sma,
)

__all__ = [
    "atr",
    "compute_regime_features",
    "compute_technical_features",
    "demean_cross_sectional",
    "ema",
    "ewma_vol",
    "ibs",
    "log_returns",
    "rank_cross_sectional",
    "returns",
    "rolling_vol",
    "rsi",
    "sma",
    "term_structure_ratio",
    "top_n_mask",
    "universe_momentum",
    "vix_log_level",
    "vix_percentile",
    "zscore_cross_sectional",
]
