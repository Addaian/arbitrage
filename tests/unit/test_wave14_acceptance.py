"""Wave 14 acceptance: a deliberately bad strategy is blocked by the
validation CLI AND by the CI strategy-artifact gate.

Two paths:

1. **Validator rejects a bad signal.** We build a losing strategy
   (short the top momentum names instead of long them) and run it
   through `validate_new_strategy.py` in-process. Expect exit code 1
   and at least one failing gate in the generated markdown.

2. **CI gate rejects a signal-only diff.** `check_strategy_artifacts.py`
   is handed a synthetic file list including a new `src/quant/signals/*.py`
   but no matching `docs/strategies/*.md`. Expect exit code 1 and a
   clear error message.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _minimal_env() -> dict[str, str]:
    """Tiny env for subprocess — preserves PATH, HOME, VIRTUAL_ENV."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV", ""),
    }


# --- (1) Validator rejects a losing strategy -------------------------


def test_validator_rejects_inverse_momentum(tmp_path: Path) -> None:
    """The CLI, driven via the class import path, rejects inverse momentum.

    We register the synthetic class into a namespace importable by path,
    then invoke the CLI as a subprocess so the full exit-code path is
    exercised (not just the in-process function).
    """
    # Drop the bad strategy into a local module in tmp_path so the CLI
    # can import it via "module:Class".
    pkg_dir = tmp_path / "bad_strategy_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "inverse.py").write_text(
        """from dataclasses import dataclass
import pandas as pd
from quant.signals import MomentumSignal


@dataclass
class InverseMomentum:
    name: str = "inverse_momentum"
    lookback_months: int = 6
    top_n: int = 3
    cash_symbol: str = "SHY"

    def target_weights(self, closes):
        inner = MomentumSignal(
            lookback_months=self.lookback_months,
            top_n=self.top_n,
            cash_symbol=self.cash_symbol,
        )
        risk = [c for c in closes.columns if c != self.cash_symbol]
        monthly_close = closes[risk].resample("ME").last()
        mom = monthly_close.pct_change(periods=self.lookback_months, fill_method=None)
        cols = list(closes.columns)
        bad = pd.DataFrame(float("nan"), index=closes.index, columns=cols, dtype=float)
        per_slot = 1.0 / self.top_n
        for ts, row in mom.iterrows():
            future = closes.index[closes.index > ts]
            if len(future) == 0:
                continue
            trade_day = future[0]
            bad.loc[trade_day] = 0.0
            if row.isna().all():
                bad.loc[trade_day, self.cash_symbol] = 1.0
                continue
            picks = row.dropna().sort_values(ascending=True).head(self.top_n)
            for sym in picks.index:
                bad.loc[trade_day, sym] = per_slot
            bad.loc[trade_day, self.cash_symbol] = max(
                0.0, 1.0 - per_slot * len(picks)
            )
        return bad
"""
    )

    env_pythonpath = f"{pkg_dir.parent}:{REPO_ROOT / 'src'}"
    output_path = tmp_path / "inverse_momentum.md"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_new_strategy.py"),
            "--strategy",
            "bad_strategy_pkg.inverse:InverseMomentum",
            "--name",
            "inverse_momentum",
            "--universe",
            "SPY,QQQ,EFA,EEM,GLD,IEF,TLT,VNQ,DBC,XLE,SHY",
            "--cash",
            "SHY",
            "--params",
            '{"lookback_months": 6, "top_n": 3, "cash_symbol": "SHY"}',
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        env={
            **_minimal_env(),
            "PYTHONPATH": env_pythonpath,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1, (
        f"expected non-zero exit for a losing strategy, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    body = output_path.read_text()
    assert "**FAIL**" in body
    # At least one gate is crossed out.
    assert "| oos_sharpe_ge_min | ✗ |" in body or "| dsr_psr_gt_min | ✗ |" in body


# --- (2) CI gate rejects signal-only diff ----------------------------


def test_ci_gate_rejects_signal_change_without_doc() -> None:
    """Feed a synthetic diff listing a new signal file with no strategy
    doc. The gate script exits non-zero."""
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_strategy_artifacts.py"),
        ],
        input="src/quant/signals/new_thing.py\n",
        cwd=REPO_ROOT,
        env={
            **_minimal_env(),
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert "adds/updates no" in result.stderr or "adds/updates no" in result.stdout


def test_ci_gate_accepts_signal_plus_doc() -> None:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_strategy_artifacts.py"),
        ],
        input="src/quant/signals/new_thing.py\ndocs/strategies/new_thing.md\n",
        cwd=REPO_ROOT,
        env={
            **_minimal_env(),
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0


def test_ci_gate_ignores_exempt_signal_files() -> None:
    """__init__.py and base.py don't require strategy docs."""
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_strategy_artifacts.py"),
        ],
        input="src/quant/signals/__init__.py\nsrc/quant/signals/base.py\n",
        cwd=REPO_ROOT,
        env={
            **_minimal_env(),
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0


def test_ci_gate_ignores_unrelated_changes() -> None:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_strategy_artifacts.py"),
        ],
        input="src/quant/data/loaders.py\nREADME.md\n",
        cwd=REPO_ROOT,
        env={
            **_minimal_env(),
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0


def test_ci_gate_empty_diff() -> None:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_strategy_artifacts.py"),
        ],
        input="",
        cwd=REPO_ROOT,
        env={
            **_minimal_env(),
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0


@pytest.mark.skip(
    reason="exercises the full subprocess + cache; covered by the subprocess test above"
)
def test_validator_rejects_inverse_momentum_in_process() -> None:
    """Kept for clarity — the meaningful test is the subprocess one."""
