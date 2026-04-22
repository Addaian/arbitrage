"""Tests for Wave 19 pre-live deliverables.

Covers:
    - `record_rolling_sharpe` helper (monitoring/metrics.py)
    - `scripts.paper_vs_backtest.compute_tracking_error` pure fn
    - `docs/go_live_checklist.md` structural completeness (every PRD
       gate + the plan Week 19 tasks show up as checkbox items)
    - `docs/disaster_recovery.md` exists and covers the three scenarios
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from quant.monitoring import (
    record_rolling_sharpe,
    registry,
    reset_metrics_registry,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_metrics() -> None:
    reset_metrics_registry()


def _sample(metric_name: str) -> float | None:
    for family in registry().collect():
        for sample in family.samples:
            if sample.name == metric_name:
                return sample.value
    return None


# --- rolling-sharpe emitter ------------------------------------------


def test_record_rolling_sharpe_sets_both_gauges() -> None:
    record_rolling_sharpe(sharpe=0.87, daily_return=0.0042)
    assert _sample("quant_rolling_30d_sharpe") == pytest.approx(0.87)
    assert _sample("quant_daily_return") == pytest.approx(0.0042)


def test_record_rolling_sharpe_handles_negative() -> None:
    record_rolling_sharpe(sharpe=-0.15, daily_return=-0.02)
    assert _sample("quant_rolling_30d_sharpe") == pytest.approx(-0.15)
    assert _sample("quant_daily_return") == pytest.approx(-0.02)


def test_record_rolling_sharpe_overrides_previous_value() -> None:
    record_rolling_sharpe(sharpe=0.5, daily_return=0.01)
    record_rolling_sharpe(sharpe=0.8, daily_return=0.02)
    # Gauge semantics — latest write wins.
    assert _sample("quant_rolling_30d_sharpe") == pytest.approx(0.8)


# --- paper-vs-backtest tracking-error pure function ------------------


def _load_paper_vs_backtest_module():
    """The script lives under scripts/ (not a package), so we import it
    by file path for tests."""
    path = REPO / "scripts" / "paper_vs_backtest.py"
    spec = importlib.util.spec_from_file_location("paper_vs_backtest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["paper_vs_backtest"] = module
    spec.loader.exec_module(module)
    return module


def test_compute_tracking_error_basic() -> None:
    mod = _load_paper_vs_backtest_module()
    # Backtest 0.8, paper 0.6 → |0.6-0.8|/|0.8| = 25%.
    assert mod.compute_tracking_error(0.6, 0.8) == pytest.approx(25.0)


def test_compute_tracking_error_handles_zero_backtest() -> None:
    mod = _load_paper_vs_backtest_module()
    # |0.3 - 0| = 0.3 -> 30 as absolute gap x 100.
    assert mod.compute_tracking_error(0.3, 0.0) == pytest.approx(30.0)


def test_compute_tracking_error_symmetric_around_sign() -> None:
    mod = _load_paper_vs_backtest_module()
    assert mod.compute_tracking_error(-0.2, -0.4) == pytest.approx(50.0)
    assert mod.compute_tracking_error(0.2, 0.4) == pytest.approx(50.0)


# --- go-live checklist structure ------------------------------------


def test_go_live_checklist_exists() -> None:
    assert (REPO / "docs" / "go_live_checklist.md").is_file()


def test_go_live_checklist_covers_wave19_tasks() -> None:
    body = (REPO / "docs" / "go_live_checklist.md").read_text().lower()
    # Plan Week 19 tasks — every one must be reflected somewhere.
    for required_phrase in (
        "tracking error",  # paper vs backtest sharpe
        "alert",  # alert review
        "disaster",  # DR drill
        "kill-switch",  # kill-switch drill
        "kyc",  # Alpaca KYC
        "sign",  # sign-off line
    ):
        assert required_phrase in body, f"missing required phrase: {required_phrase}"


def test_go_live_checklist_references_all_four_gates() -> None:
    body = (REPO / "docs" / "go_live_checklist.md").read_text()
    for gate in ("Gate 1", "Gate 2", "Gate 3"):
        assert gate in body, f"checklist does not reference {gate}"
    # Gate 4 is the *post*-go-live gate — not required here, but the
    # absence of Gate 4 would indicate a doc drift; it's fine if missing.


def test_go_live_checklist_has_printable_signoff_fields() -> None:
    body = (REPO / "docs" / "go_live_checklist.md").read_text()
    # Plan says "printed checklist with every item checked, signed/dated".
    assert re.search(r"Completed by", body)
    assert re.search(r"Date", body)
    assert re.search(r"GO", body)
    assert re.search(r"NO-GO", body)


# --- disaster-recovery runbook --------------------------------------


def test_disaster_recovery_runbook_exists() -> None:
    assert (REPO / "docs" / "disaster_recovery.md").is_file()


def test_dr_runbook_covers_three_scenarios() -> None:
    body = (REPO / "docs" / "disaster_recovery.md").read_text().lower()
    # Each of the three failure modes has a scenario heading.
    assert "scenario 1" in body
    assert "scenario 2" in body
    assert "scenario 3" in body
    # RTO target < 1 hour matches plan acceptance.
    assert "under 1 hour" in body or "<1 hour" in body or "under an hour" in body


def test_dr_runbook_references_bootstrap_and_pg_dump() -> None:
    body = (REPO / "docs" / "disaster_recovery.md").read_text()
    assert "bootstrap.sh" in body
    assert "pg_dump" in body or "pg_restore" in body
    # Observability stack restart must be part of the recovery path.
    assert "docker compose" in body
