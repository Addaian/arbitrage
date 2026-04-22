"""Hidden-Markov-Model regime classifier (PRD §5.4, plan Week 15).

Trains a 3-state Gaussian HMM on weekly market features. State 0/1/2
are assigned arbitrarily by the optimizer — we label them post-fit
**by realized variance of the emission means / covariances**: the
highest-vol state is "stress", the lowest-vol is "calm", the middle
is "neutral". This makes the semantics stable across retrains even
though the raw state IDs drift.

Features (default):
    - weekly log return of the reference series (SPY)
    - realized vol over the last 5 weeks (rolling std of weekly returns)
    - term-structure proxy: 5-week vol / 20-week vol

VIX and VIX term structure are supported inputs but optional — yfinance
returns for ^VIX / ^VXV aren't in our cache yet, so the model would
need to be re-fit with them once the backfill is extended (Wave 15.1
if the plan runs short). The Gaussian-HMM structure doesn't change
either way.

Artifact format: a `RegimeHMM` instance serialised via `joblib`.
Callers load it with `RegimeHMM.load(path)` and call
`predict_proba_next(features)` to get the `(T, 3)` posterior matrix
for an incoming feature frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

# Feature pipeline defaults — kept tiny so the model doesn't silently
# pick up dependencies on columns that aren't guaranteed to be there.
_DEFAULT_VOL_WINDOW = 5  # weeks
_DEFAULT_TERM_WINDOW = 20  # weeks
_MIN_FIT_OBSERVATIONS = 52  # ~1 year of weekly bars


@dataclass
class RegimeHMM:
    """Thin wrapper around `hmmlearn.hmm.GaussianHMM` with state-label
    stabilisation (stress / neutral / calm) and our own feature pipeline.
    """

    n_states: int = 3
    random_state: int = 42
    covariance_type: str = "full"
    n_iter: int = 100
    # Populated by `fit()`:
    model: GaussianHMM | None = None
    feature_names: list[str] | None = None
    state_labels: dict[int, str] | None = None  # {raw_state: "stress"|"neutral"|"calm"}
    stress_state: int | None = None

    # --- Feature pipeline --------------------------------------------

    @staticmethod
    def build_features(
        closes: pd.Series,
        *,
        vol_window: int = _DEFAULT_VOL_WINDOW,
        term_window: int = _DEFAULT_TERM_WINDOW,
    ) -> pd.DataFrame:
        """Resample daily closes to weekly, compute (log-return,
        short-vol, term-structure-ratio). Returns a DataFrame indexed on
        the weekly bar-end timestamps.
        """
        if closes.empty:
            raise ValueError("closes is empty")
        weekly = closes.resample("W-FRI").last()
        log_ret = np.log(weekly / weekly.shift(1))
        short_vol = log_ret.rolling(vol_window).std()
        long_vol = log_ret.rolling(term_window).std()
        term_ratio = short_vol / long_vol.replace(0.0, np.nan)
        out = pd.DataFrame(
            {
                "weekly_log_return": log_ret,
                "realized_vol_5w": short_vol,
                "term_ratio_5_20": term_ratio,
            }
        ).dropna()
        return out

    # --- Fit / predict -----------------------------------------------

    def fit(self, features: pd.DataFrame) -> RegimeHMM:
        if features.empty or len(features) < _MIN_FIT_OBSERVATIONS:
            raise ValueError(
                f"need at least {_MIN_FIT_OBSERVATIONS} observations to fit; got {len(features)}"
            )
        self.feature_names = list(features.columns)
        observations = features.to_numpy(dtype=float)
        model = GaussianHMM(
            n_components=self.n_states,
            covariance_type=self.covariance_type,
            random_state=self.random_state,
            n_iter=self.n_iter,
        )
        model.fit(observations)
        self.model = model
        self._label_states()
        return self

    def _label_states(self) -> None:
        """Post-fit state stabilisation: rank raw states by their
        emission variance on the realized-vol column.
        """
        assert self.model is not None
        assert self.feature_names is not None
        vol_idx = self.feature_names.index("realized_vol_5w")
        # Mean of the 2nd feature per state is a clean proxy for regime vol.
        means = self.model.means_[:, vol_idx]
        sorted_states = list(np.argsort(means))  # ascending — calmest first
        labels = ["calm", "neutral", "stress"]
        # If the model collapses to fewer distinguishable states the ranks
        # still give us a deterministic labelling.
        self.state_labels = {raw: labels[i] for i, raw in enumerate(sorted_states)}
        # The "stress" raw state is the one with the highest realized-vol mean.
        self.stress_state = sorted_states[-1]

    def predict(self, features: pd.DataFrame) -> np.ndarray[tuple[int, ...], np.dtype[np.int64]]:
        self._require_fitted()
        assert self.model is not None
        return np.asarray(self.model.predict(features.to_numpy(dtype=float)), dtype=np.int64)

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        """Per-row (T, n_states) posteriors with columns in canonical
        label order `[calm, neutral, stress]`. Callers can always index
        by name without caring how the underlying raw states came out.
        """
        self._require_fitted()
        assert self.model is not None
        assert self.state_labels is not None
        proba = self.model.predict_proba(features.to_numpy(dtype=float))
        raw_cols = [self.state_labels[i] for i in range(self.n_states)]
        df = pd.DataFrame(proba, index=features.index, columns=raw_cols)
        # Reorder into the stable [calm, neutral, stress] layout.
        canonical = [c for c in ("calm", "neutral", "stress") if c in df.columns]
        return df[canonical]

    def stress_probability(self, features: pd.DataFrame) -> pd.Series:
        """Convenience: just the `stress` column from `predict_proba`."""
        return self.predict_proba(features)["stress"]

    # --- Persistence --------------------------------------------------

    def save(self, path: Path) -> None:
        self._require_fitted()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, Path(path))

    @classmethod
    def load(cls, path: Path) -> RegimeHMM:
        obj = joblib.load(Path(path))
        if not isinstance(obj, cls):
            raise TypeError(f"expected {cls.__name__} at {path}, got {type(obj).__name__}")
        return obj

    # --- Introspection ------------------------------------------------

    @property
    def transition_matrix(self) -> pd.DataFrame:
        self._require_fitted()
        assert self.model is not None
        assert self.state_labels is not None
        labels = [self.state_labels[i] for i in range(self.n_states)]
        return pd.DataFrame(self.model.transmat_, index=labels, columns=labels)

    def _require_fitted(self) -> None:
        if self.model is None:
            raise RuntimeError("RegimeHMM not fitted; call fit() or load() first")
