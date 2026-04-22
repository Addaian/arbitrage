"""Portfolio-level sizing overlays.

Wave 15 — **regime multiplier**. Given a stress-probability series
`p_stress` from `RegimeHMM.stress_probability(...)`, the multiplier is
`1 - p_stress`, clamped to `[0, 1]`. Apply this to a combined
target-weights frame to dial exposure down during stress regimes.

Wave 16 — **vol targeting** will add a second overlay here (scale to
target realized vol). Both overlays are multiplicative scalars on
portfolio weights, composed on the caller's side.

Invariants:
    - Input weights sum to ≤ 1 on rebalance rows (cash = 1 - Σ risk).
    - Output weights sum to ≤ 1 on the same rows — risk portion shrinks,
      cash portion grows. Specifically: each non-cash weight is
      multiplied by `mult`, and the cash column absorbs the difference.
    - NaN rows in the input stay NaN in the output.
    - The multiplier's index is aligned to the weights index via
      forward-fill: weight rows later than the latest multiplier point
      use that last value.
"""

from __future__ import annotations

import pandas as pd


def regime_multiplier(p_stress: pd.Series) -> pd.Series:
    """Return `(1 - p_stress).clip(0, 1)`, aligned on the input index.

    A stress probability of 0.0 → multiplier 1.0 (no scaling).
    A stress probability of 1.0 → multiplier 0.0 (all cash).

    This is the PRD §5.4 / plan-Week-15 literal formula. In practice, a
    weighted form (`regime_weighted_multiplier`) is more effective on
    portfolios whose max drawdowns sit in the neutral-vol regime rather
    than the stress-vol regime — see Wave-15 CHANGELOG for the numbers.
    """
    if p_stress.empty:
        return p_stress.copy()
    mult = (1.0 - p_stress).clip(lower=0.0, upper=1.0)
    mult.name = "regime_multiplier"
    return mult


def regime_weighted_multiplier(
    proba: pd.DataFrame,
    state_weights: dict[str, float],
) -> pd.Series:
    """Compute a per-row multiplier as `Σ p(state) * state_weight`.

    `proba` is the `(T, n_states)` posterior matrix from
    `RegimeHMM.predict_proba(...)`, with columns named `calm` /
    `neutral` / `stress`. `state_weights` maps those labels to per-state
    multipliers in `[0, 1]`. The output is a weighted-average scalar
    per row, clamped to `[0, 1]`.

    Example (aggressive DD protection):
        `{"calm": 1.0, "neutral": 0.5, "stress": 0.0}`
    """
    if proba.empty:
        return pd.Series(dtype=float)
    missing = set(state_weights) - set(proba.columns)
    if missing:
        raise ValueError(f"state_weights names must be in proba.columns; missing {missing}")
    extra = set(proba.columns) - set(state_weights)
    if extra:
        raise ValueError(f"proba has columns without a state_weights entry: {sorted(extra)}")
    for label, w in state_weights.items():
        if not 0.0 <= w <= 1.0:
            raise ValueError(f"state_weights[{label!r}] must be in [0, 1], got {w}")

    mult = sum(proba[label] * w for label, w in state_weights.items())
    assert isinstance(mult, pd.Series)
    mult = mult.clip(lower=0.0, upper=1.0)
    mult.name = "regime_weighted_multiplier"
    return mult


def apply_regime_overlay(
    weights: pd.DataFrame,
    multiplier: pd.Series,
    *,
    cash_symbol: str,
) -> pd.DataFrame:
    """Scale each risk column by the (forward-filled) multiplier and
    let the cash column absorb the slack so each row still sums to 1.

    `weights` is the combined-portfolio target-weight frame; `multiplier`
    is a per-date scalar from `regime_multiplier()` (or any series in
    `[0, 1]`). `cash_symbol` must appear in `weights.columns`.
    """
    if cash_symbol not in weights.columns:
        raise ValueError(f"weights is missing cash_symbol {cash_symbol!r}")
    if weights.empty:
        return weights.copy()

    aligned = multiplier.reindex(weights.index).ffill()
    # Rows before the first multiplier observation are unscaled (1.0).
    aligned = aligned.fillna(1.0)

    risk_cols = [c for c in weights.columns if c != cash_symbol]
    scaled = weights.copy()
    scaled_risk = weights[risk_cols].mul(aligned, axis=0)
    scaled[risk_cols] = scaled_risk

    # Per-row cash = 1 - sum(risk). NaN-rows (hold-previous) stay NaN.
    cash = 1.0 - scaled_risk.sum(axis=1, min_count=1)
    # Preserve NaN where the original cash column was NaN (no rebalance).
    cash = cash.where(weights[cash_symbol].notna())
    scaled[cash_symbol] = cash
    return scaled
