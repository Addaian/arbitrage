"""Technical indicators.

Pure functions over pandas Series / DataFrames. Every indicator at row `t`
uses only data `<= t` — there must be no look-ahead bias. The
`test_no_lookahead.py` property test enforces this across the module.

Conventions
-----------
- All functions accept and return pandas objects (Series or DataFrame) so
  they compose with `.pipe()` chains.
- Warmup periods are left as NaN — callers drop them explicitly.
- Column names follow `<base>_<indicator>_<param>` (e.g. `close_sma_10`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --- Basic returns ------------------------------------------------------


def returns(close: pd.Series) -> pd.Series:
    """Simple period-over-period returns."""
    return close.pct_change()


def log_returns(close: pd.Series) -> pd.Series:
    """Natural-log returns."""
    return np.log(close / close.shift(1))


# --- Moving averages ----------------------------------------------------


def sma(series: pd.Series, window: int) -> pd.Series:
    if window <= 0:
        raise ValueError(f"window must be positive, got {window}")
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    if span <= 0:
        raise ValueError(f"span must be positive, got {span}")
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


# --- Oscillators --------------------------------------------------------


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI. Uses an EWMA with alpha=1/window (smoothed moving average),
    which matches the original definition and differs subtly from a simple
    rolling-mean RSI.
    """
    if window <= 1:
        raise ValueError(f"rsi window must be > 1, got {window}")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder smoothing: alpha = 1/window, adjust=False.
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss is zero and gain is positive, RSI = 100.
    out = out.where(avg_loss != 0.0, 100.0)
    # When both gain and loss are zero, RSI is undefined — leave as NaN.
    both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
    out = out.where(~both_zero, np.nan)
    return out


def ibs(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Internal Bar Strength = (close - low) / (high - low). Range [0, 1].
    Used by the mean-reversion strategy (PRD §5.3).
    """
    span = high - low
    return ((close - low) / span.replace(0.0, np.nan)).clip(lower=0.0, upper=1.0)


# --- Volatility ---------------------------------------------------------


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def rolling_vol(
    series: pd.Series,
    window: int = 21,
    *,
    annualize: bool = True,
    periods_per_year: int = 252,
) -> pd.Series:
    """Rolling standard deviation of returns. Annualized by default (sqrt-T)."""
    rets = series.pct_change() if _looks_like_prices(series) else series
    vol = rets.rolling(window=window, min_periods=window).std(ddof=1)
    if annualize:
        vol = vol * np.sqrt(periods_per_year)
    return vol


def ewma_vol(
    returns_: pd.Series,
    lam: float = 0.94,
    *,
    annualize: bool = True,
    periods_per_year: int = 252,
) -> pd.Series:
    """RiskMetrics-style EWMA volatility forecast. Used by the vol-target
    overlay (PRD §5.5 / Week 16).
    """
    if not 0.0 < lam < 1.0:
        raise ValueError(f"lam must be in (0, 1), got {lam}")
    var = (returns_**2).ewm(alpha=1.0 - lam, adjust=False, min_periods=1).mean()
    vol = np.sqrt(var)
    if annualize:
        vol = vol * np.sqrt(periods_per_year)
    return vol


# --- Aggregator ---------------------------------------------------------


def compute_technical_features(
    bars: pd.DataFrame,
    *,
    sma_windows: tuple[int, ...] = (20, 50, 200),
    ema_spans: tuple[int, ...] = (12, 26),
    rsi_windows: tuple[int, ...] = (2, 14),
    atr_window: int = 14,
    vol_window: int = 21,
) -> pd.DataFrame:
    """Compute a canonical set of technical features on a single-symbol OHLCV
    frame (date-indexed, columns open/high/low/close/volume).

    Returns a DataFrame indexed identically to `bars`, containing the input
    columns plus feature columns with the naming convention described in the
    module docstring.
    """
    _require_ohlcv(bars)
    out = bars.copy()

    out["ret"] = returns(out["close"])
    out["log_ret"] = log_returns(out["close"])

    for w in sma_windows:
        out[f"close_sma_{w}"] = sma(out["close"], w)
    for s in ema_spans:
        out[f"close_ema_{s}"] = ema(out["close"], s)
    for w in rsi_windows:
        out[f"rsi_{w}"] = rsi(out["close"], w)

    out["ibs"] = ibs(out["high"], out["low"], out["close"])
    out[f"atr_{atr_window}"] = atr(out["high"], out["low"], out["close"], atr_window)
    out[f"vol_{vol_window}"] = rolling_vol(out["close"], vol_window)

    return out


# --- Helpers ------------------------------------------------------------


def _looks_like_prices(s: pd.Series) -> bool:
    """Cheap heuristic: if values look like price levels (positive and usually
    not tiny), treat the input as prices; otherwise assume returns. Only used
    by `rolling_vol` so callers don't have to remember which branch to take.
    """
    clean = s.dropna()
    if clean.empty:
        return False
    return bool((clean > 0).all()) and float(clean.abs().median()) > 1.0


def _require_ohlcv(df: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"frame missing required columns: {sorted(missing)}")
