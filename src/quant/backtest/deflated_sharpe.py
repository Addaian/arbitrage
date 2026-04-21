"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

The observed Sharpe ratio of a backtest is upward-biased when (a) many
parameter trials were run and (b) returns are non-normal. This module
implements two related corrections from Bailey & Lopez de Prado's
*"The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
Overfitting, and Non-Normality"* (JPM 2014):

1. **Probabilistic Sharpe Ratio** — `probabilistic_sharpe_ratio(sr, sr_star, T, skew, kurt)`
   returns `P[SR_true > sr_star]` given a sample Sharpe `sr` over `T`
   returns, accounting for skewness and kurtosis via the asymptotic
   standard error of SR.

2. **Expected max-of-N Sharpe** — `expected_max_sharpe(num_trials, sr_variance)`
   is the benchmark `sr_star` one should beat to claim skill over
   `num_trials` random candidate strategies with Sharpe-variance
   `sr_variance`. Uses the closed-form Gumbel approximation from the
   paper (Eq. 6 in Bailey & Lopez de Prado 2014).

The `deflated_sharpe_ratio(...)` wrapper composes both: it computes
`sr_star` from the trial count and then returns the PSR against that
benchmark. A DSR probability > 0.95 is the conventional "pass" line.

All Sharpe inputs are **annualized**; the number of return
observations (daily = 252/yr) is passed as `num_observations`. The
formulas internally convert.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
from scipy.stats import norm

_TRADING_DAYS_PER_YEAR = 252
_EULER_MASCHERONI = 0.5772156649015329


@dataclass(frozen=True)
class DeflatedSharpeResult:
    observed_sharpe: float  # annualized
    benchmark_sharpe: float  # sr_star — expected max from num_trials
    psr: float  # P[SR_true > benchmark] in [0, 1]
    num_trials: int
    num_observations: int  # T, in return periods (daily)
    skew: float
    excess_kurtosis: float  # Fisher convention: normal distribution is 0
    passes: bool  # psr > 0.95


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    benchmark_sharpe: float,
    *,
    num_observations: int,
    skew: float = 0.0,
    excess_kurtosis: float = 0.0,
) -> float:
    """PSR = Phi( (SR - SR*) * sqrt(T-1) / se(SR) )

    se(SR)^2 = 1 - skew*SR + (excess_kurt/4)*SR^2   (a normal
    distribution has excess_kurt = 0 in this convention)

    All Sharpe arguments are *annualized*. Internally we deflate to
    per-period scale; otherwise the per-period standard error formula
    does not apply.
    """
    if num_observations <= 1:
        raise ValueError(f"num_observations must be > 1, got {num_observations}")

    # Convert annualized Sharpe to per-period Sharpe for the standard-error
    # formula, which is derived on the per-period return series.
    scale = math.sqrt(_TRADING_DAYS_PER_YEAR)
    sr = observed_sharpe / scale
    sr_star = benchmark_sharpe / scale

    variance_factor = 1.0 - skew * sr + (excess_kurtosis / 4.0) * sr**2
    if variance_factor <= 0.0:
        # Pathological inputs: return 0.5 rather than crashing with a
        # math-domain error. Caller sees "passes=False" and can drill in.
        return 0.5
    se = math.sqrt(variance_factor / (num_observations - 1))

    z = (sr - sr_star) / se
    return float(norm.cdf(z))


def expected_max_sharpe(num_trials: int, sr_variance: float) -> float:
    """E[max over N trials of SR_n] for N independent trials with Sharpe
    variance `sr_variance`. Closed-form Gumbel approximation:

        E[max] = sqrt(V) * [(1 - g)*Phi^-1(1 - 1/N) + g*Phi^-1(1 - 1/(N*e))]

    where `g` is the Euler-Mascheroni constant. Returns an *annualized*
    Sharpe because `sr_variance` is expected in annualized units.
    """
    if num_trials < 1:
        raise ValueError(f"num_trials must be >= 1, got {num_trials}")
    if sr_variance < 0:
        raise ValueError(f"sr_variance must be >= 0, got {sr_variance}")
    if num_trials == 1:
        return 0.0

    # Inverse normal evaluated near 1 — numerically safe for large N.
    q1 = norm.ppf(1.0 - 1.0 / num_trials)
    q2 = norm.ppf(1.0 - 1.0 / (num_trials * math.e))
    expected = (1.0 - _EULER_MASCHERONI) * q1 + _EULER_MASCHERONI * q2
    return float(math.sqrt(sr_variance) * expected)


def deflated_sharpe_ratio(
    returns: pd.Series,
    *,
    num_trials: int,
    sr_variance: float | None = None,
) -> DeflatedSharpeResult:
    """Compute DSR for a daily-return series.

    `returns` must be **daily** net returns (decimal, e.g. 0.01 for 1%).
    `sr_variance` is the annualized variance of Sharpe estimates across
    the trial population. If `None`, we use the asymptotic approximation
    `Var(SR_annualized) ≈ (1 + 0.5·SR²) · (252/T)` — a common default
    when per-trial Sharpes aren't catalogued.
    """
    r = returns.dropna().astype(float)
    num_obs = len(r)
    if num_obs < 30:
        raise ValueError(f"need at least 30 return observations, got {num_obs}")

    daily_std = float(r.std(ddof=1))
    if daily_std <= 0.0:
        observed_sharpe = 0.0
    else:
        observed_sharpe = float(r.mean() / daily_std) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    skew = float(r.skew())
    # pandas .kurt() returns excess kurtosis (Fisher); normal is 0.
    excess_kurtosis = float(r.kurt())

    if sr_variance is None:
        sr_variance = (1.0 + 0.5 * observed_sharpe**2) * _TRADING_DAYS_PER_YEAR / num_obs
    sr_star = expected_max_sharpe(num_trials, sr_variance)

    psr = probabilistic_sharpe_ratio(
        observed_sharpe,
        sr_star,
        num_observations=num_obs,
        skew=skew,
        excess_kurtosis=excess_kurtosis,
    )
    return DeflatedSharpeResult(
        observed_sharpe=observed_sharpe,
        benchmark_sharpe=sr_star,
        psr=psr,
        num_trials=num_trials,
        num_observations=num_obs,
        skew=skew,
        excess_kurtosis=excess_kurtosis,
        passes=psr > 0.95,
    )


def annualized_sharpe(returns: pd.Series, *, risk_free: float = 0.0) -> float:
    """Convenience: annualized Sharpe on a daily return series."""
    r = returns.dropna().astype(float)
    if len(r) < 2:
        return 0.0
    std = float(r.std(ddof=1))
    if std <= 0.0:
        return 0.0
    excess = r - risk_free / _TRADING_DAYS_PER_YEAR
    return float(excess.mean() / std) * math.sqrt(_TRADING_DAYS_PER_YEAR)
