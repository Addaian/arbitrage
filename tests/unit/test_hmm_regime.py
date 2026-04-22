"""Tests for RegimeHMM + portfolio.sizing regime overlay."""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from quant.models import RegimeHMM
from quant.portfolio import (
    apply_regime_overlay,
    regime_multiplier,
    regime_weighted_multiplier,
)

# --- Synthetic two-regime fixture -------------------------------------


def _synthetic_two_regime_closes(
    n_weeks: int = 300,
    calm_weekly_vol: float = 0.01,
    stress_weekly_vol: float = 0.05,
    seed: int = 0,
) -> tuple[pd.Series, np.ndarray]:
    """Generate daily closes from two regime-dependent Gaussians.

    Daily returns are drawn directly (not smeared from weekly) so the
    5-week rolling-vol feature actually picks up the regime difference
    rather than finding near-constant volatility on a piecewise-constant
    daily series.

    Returns `(daily_closes, weekly_regime_labels)` with labels aligned
    to W-FRI bar-ends.
    """
    rng = np.random.default_rng(seed)
    # 5 trading days per "week"; alternate 50-week blocks.
    block = 50
    weekly_labels: list[int] = []
    daily_returns: list[float] = []
    for start in range(0, n_weeks, block):
        regime = 0 if (start // block) % 2 == 0 else 1
        weekly_vol = calm_weekly_vol if regime == 0 else stress_weekly_vol
        daily_vol = weekly_vol / np.sqrt(5.0)
        # 5 daily draws per week, for `block` weeks.
        n_days = min(block, n_weeks - start) * 5
        rets = rng.normal(0.0, daily_vol, n_days)
        daily_returns.extend(rets.tolist())
        weekly_labels.extend([regime] * min(block, n_weeks - start))

    daily_idx = pd.date_range("2015-01-02", periods=len(daily_returns), freq="B")
    prices = 100.0 * np.cumprod(1 + np.asarray(daily_returns))
    return pd.Series(prices, index=daily_idx, name="SPY"), np.asarray(weekly_labels)


# --- Feature pipeline --------------------------------------------------


def test_build_features_requires_nonempty() -> None:
    with pytest.raises(ValueError, match="empty"):
        RegimeHMM.build_features(pd.Series([], dtype=float))


def test_build_features_shape() -> None:
    closes, _ = _synthetic_two_regime_closes(n_weeks=120)
    features = RegimeHMM.build_features(closes)
    # After dropna on 20-week term window, we lose ~20 weeks.
    assert len(features) > 80
    assert list(features.columns) == [
        "weekly_log_return",
        "realized_vol_5w",
        "term_ratio_5_20",
    ]


# --- Fit / predict -----------------------------------------------------


def test_fit_rejects_small_dataset() -> None:
    short_features = pd.DataFrame(
        {
            "weekly_log_return": np.random.default_rng(0).normal(0, 0.01, 10),
            "realized_vol_5w": np.full(10, 0.01),
            "term_ratio_5_20": np.full(10, 1.0),
        }
    )
    with pytest.raises(ValueError, match="at least"):
        RegimeHMM().fit(short_features)


def test_stress_state_labelling_assigns_highest_vol_state() -> None:
    closes, _ = _synthetic_two_regime_closes(n_weeks=300, seed=1)
    features = RegimeHMM.build_features(closes)
    hmm = RegimeHMM().fit(features)
    # stress_state is the state with the highest realized_vol_5w mean.
    assert hmm.stress_state is not None
    assert hmm.state_labels is not None
    assert hmm.state_labels[hmm.stress_state] == "stress"
    assert set(hmm.state_labels.values()) == {"calm", "neutral", "stress"}


def test_predict_proba_columns_are_stable_labels() -> None:
    closes, _ = _synthetic_two_regime_closes(n_weeks=250, seed=2)
    features = RegimeHMM.build_features(closes)
    hmm = RegimeHMM().fit(features)
    proba = hmm.predict_proba(features)
    assert list(proba.columns) == ["calm", "neutral", "stress"]
    # Rows sum to 1.
    np.testing.assert_allclose(proba.sum(axis=1).to_numpy(), 1.0, atol=1e-6)


def test_stress_probability_is_proba_stress_column() -> None:
    closes, _ = _synthetic_two_regime_closes(n_weeks=250, seed=3)
    features = RegimeHMM.build_features(closes)
    hmm = RegimeHMM().fit(features)
    p_stress = hmm.stress_probability(features)
    assert (p_stress >= 0.0).all()
    assert (p_stress <= 1.0).all()


def test_predict_before_fit_raises() -> None:
    hmm = RegimeHMM()
    with pytest.raises(RuntimeError, match="not fitted"):
        hmm.predict(pd.DataFrame({"x": [1.0, 2.0]}))


def test_transition_matrix_is_square_with_labelled_axes() -> None:
    closes, _ = _synthetic_two_regime_closes(n_weeks=250, seed=4)
    features = RegimeHMM.build_features(closes)
    hmm = RegimeHMM().fit(features)
    tm = hmm.transition_matrix
    assert tm.shape == (3, 3)
    assert set(tm.index) == {"calm", "neutral", "stress"}
    np.testing.assert_allclose(tm.sum(axis=1).to_numpy(), 1.0, atol=1e-6)


# --- Acceptance: recovers known regimes with >80% accuracy ------------


def test_known_state_recovery_better_than_chance() -> None:
    """Plan acceptance: synthetic data with 2 known regimes (mapped
    onto our 3-state model as stress + non-stress) — we should tag
    the stress weeks correctly > 80% of the time.
    """
    closes, labels = _synthetic_two_regime_closes(
        n_weeks=400, calm_weekly_vol=0.01, stress_weekly_vol=0.06, seed=7
    )
    features = RegimeHMM.build_features(closes)
    hmm = RegimeHMM().fit(features)
    p_stress = hmm.stress_probability(features)

    # Align the weekly synthetic labels with the feature frame. The
    # features frame drops the first ~20 weeks due to rolling windows,
    # and its index is Friday bar-ends.
    # Build a weekly-Friday series of labels by resampling the daily one.
    daily_label_series = (
        pd.Series(np.repeat(labels, 5)[: len(closes)], index=closes.index).resample("W-FRI").last()
    )
    daily_label_series = daily_label_series.reindex(features.index).dropna()

    predicted_stress = (p_stress >= 0.5).astype(int).reindex(daily_label_series.index)
    # Bar-by-bar accuracy on the stress-vs-non-stress classification.
    accuracy = (predicted_stress == daily_label_series).mean()
    assert accuracy > 0.80, f"accuracy {accuracy:.2%} below 80% threshold"


# --- Persistence -------------------------------------------------------


def test_save_load_roundtrip_preserves_predictions(tmp_path: Path) -> None:
    closes, _ = _synthetic_two_regime_closes(n_weeks=220, seed=5)
    features = RegimeHMM.build_features(closes)
    hmm = RegimeHMM().fit(features)
    expected = hmm.predict_proba(features)

    path = tmp_path / "regime.joblib"
    hmm.save(path)
    reloaded = RegimeHMM.load(path)
    actual = reloaded.predict_proba(features)
    pd.testing.assert_frame_equal(actual, expected)


def test_save_requires_fitted(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        RegimeHMM().save(tmp_path / "x.joblib")


def test_load_rejects_wrong_type(tmp_path: Path) -> None:
    path = tmp_path / "other.joblib"
    joblib.dump({"not": "an hmm"}, path)
    with pytest.raises(TypeError):
        RegimeHMM.load(path)


# --- Portfolio sizing overlay -----------------------------------------


def test_regime_multiplier_basic() -> None:
    p = pd.Series([0.0, 0.3, 0.7, 1.0, -0.1, 1.2])
    mult = regime_multiplier(p)
    np.testing.assert_allclose(mult.to_numpy(), [1.0, 0.7, 0.3, 0.0, 1.0, 0.0])


def test_regime_multiplier_empty() -> None:
    out = regime_multiplier(pd.Series([], dtype=float))
    assert out.empty


def test_apply_regime_overlay_scales_risk_and_grows_cash() -> None:
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    weights = pd.DataFrame(
        {"SPY": [0.6, 0.6, 0.6], "QQQ": [0.3, 0.3, 0.3], "CASH": [0.1, 0.1, 0.1]},
        index=idx,
    )
    mult = pd.Series([1.0, 0.5, 0.0], index=idx)
    scaled = apply_regime_overlay(weights, mult, cash_symbol="CASH")
    # Row 0: unchanged.
    assert scaled.iloc[0].tolist() == pytest.approx([0.6, 0.3, 0.1])
    # Row 1: risk halved, cash absorbs.
    assert scaled.iloc[1]["SPY"] == pytest.approx(0.3)
    assert scaled.iloc[1]["QQQ"] == pytest.approx(0.15)
    assert scaled.iloc[1]["CASH"] == pytest.approx(0.55)
    # Row 2: all cash.
    assert scaled.iloc[2]["SPY"] == pytest.approx(0.0)
    assert scaled.iloc[2]["CASH"] == pytest.approx(1.0)


def test_apply_regime_overlay_preserves_nan_rows() -> None:
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    weights = pd.DataFrame(
        {"SPY": [0.5, float("nan"), 0.5], "CASH": [0.5, float("nan"), 0.5]},
        index=idx,
    )
    mult = pd.Series([0.5, 0.5, 0.5], index=idx)
    scaled = apply_regime_overlay(weights, mult, cash_symbol="CASH")
    assert scaled.iloc[1].isna().all()  # hold-previous row stays NaN
    assert scaled.iloc[0]["SPY"] == pytest.approx(0.25)
    assert scaled.iloc[0]["CASH"] == pytest.approx(0.75)


def test_apply_regime_overlay_forward_fills_multiplier() -> None:
    idx = pd.date_range("2024-01-02", periods=4, freq="B")
    weights = pd.DataFrame({"SPY": [0.8] * 4, "CASH": [0.2] * 4}, index=idx)
    # Multiplier given only for day 0 and day 2.
    mult = pd.Series([0.5, 0.25], index=[idx[0], idx[2]])
    scaled = apply_regime_overlay(weights, mult, cash_symbol="CASH")
    # Days 0,1 → 0.5 multiplier; days 2,3 → 0.25.
    assert scaled.iloc[0]["SPY"] == pytest.approx(0.4)
    assert scaled.iloc[1]["SPY"] == pytest.approx(0.4)
    assert scaled.iloc[2]["SPY"] == pytest.approx(0.2)
    assert scaled.iloc[3]["SPY"] == pytest.approx(0.2)


def test_apply_regime_overlay_rejects_missing_cash() -> None:
    idx = pd.date_range("2024-01-02", periods=2, freq="B")
    weights = pd.DataFrame({"SPY": [1.0, 1.0]}, index=idx)
    mult = pd.Series([0.5, 0.5], index=idx)
    with pytest.raises(ValueError, match="cash_symbol"):
        apply_regime_overlay(weights, mult, cash_symbol="CASH")


def test_apply_regime_overlay_empty_frame() -> None:
    out = apply_regime_overlay(
        pd.DataFrame(columns=["SPY", "CASH"]), pd.Series([], dtype=float), cash_symbol="CASH"
    )
    assert out.empty


# --- regime_weighted_multiplier ---------------------------------------


def test_weighted_multiplier_basic() -> None:
    proba = pd.DataFrame(
        {
            "calm": [1.0, 0.0, 0.5],
            "neutral": [0.0, 0.0, 0.5],
            "stress": [0.0, 1.0, 0.0],
        }
    )
    # Weights: calm=1, neutral=0.5, stress=0
    out = regime_weighted_multiplier(proba, {"calm": 1.0, "neutral": 0.5, "stress": 0.0})
    np.testing.assert_allclose(out.to_numpy(), [1.0, 0.0, 0.75])


def test_weighted_multiplier_empty() -> None:
    out = regime_weighted_multiplier(pd.DataFrame(), {})
    assert out.empty


def test_weighted_multiplier_rejects_missing_state() -> None:
    proba = pd.DataFrame({"calm": [1.0], "stress": [0.0]})
    with pytest.raises(ValueError, match="missing"):
        regime_weighted_multiplier(proba, {"calm": 1.0, "neutral": 0.5, "stress": 0.0})


def test_weighted_multiplier_rejects_extra_column() -> None:
    proba = pd.DataFrame({"calm": [1.0], "neutral": [0.0], "stress": [0.0]})
    with pytest.raises(ValueError, match="without a state_weights"):
        regime_weighted_multiplier(proba, {"calm": 1.0})


def test_weighted_multiplier_rejects_out_of_range_weights() -> None:
    proba = pd.DataFrame({"calm": [1.0], "neutral": [0.0], "stress": [0.0]})
    with pytest.raises(ValueError, match="must be in"):
        regime_weighted_multiplier(proba, {"calm": 1.1, "neutral": 0.0, "stress": 0.0})
