"""Tests for the JSONL trial log."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from quant.backtest.trial_log import JsonlTrialLog, TrialLog, TrialRecord


def _record(strategy: str = "trend", lookback: int = 10, sharpe: float = 0.7) -> TrialRecord:
    return TrialRecord(
        strategy=strategy,
        params={"lookback_months": lookback},
        start_date=date(2003, 1, 1),
        end_date=date(2026, 1, 1),
        sharpe=sharpe,
        cagr=0.055,
        max_drawdown=-0.17,
        recorded_at=datetime.now(UTC),
    )


def test_jsonl_log_is_empty_on_fresh_dir(tmp_path: Path) -> None:
    log = JsonlTrialLog(tmp_path)
    assert log.count_trials("trend") == 0


def test_jsonl_log_records_and_counts(tmp_path: Path) -> None:
    log = JsonlTrialLog(tmp_path)
    for lb in (6, 9, 10, 12):
        log.record(_record(lookback=lb))
    assert log.count_trials("trend") == 4
    assert log.count_trials("momentum") == 0


def test_jsonl_log_persists_across_instances(tmp_path: Path) -> None:
    log = JsonlTrialLog(tmp_path)
    log.record(_record())
    again = JsonlTrialLog(tmp_path)
    assert again.count_trials("trend") == 1


def test_trial_record_hash_is_deterministic() -> None:
    a = _record(lookback=10)
    b = _record(lookback=10)
    c = _record(lookback=12)
    assert a.params_hash == b.params_hash
    assert a.params_hash != c.params_hash


def test_jsonl_log_round_trips_records(tmp_path: Path) -> None:
    log = JsonlTrialLog(tmp_path)
    log.record(_record(lookback=10, sharpe=0.72))
    rows = log.read_all("trend")
    assert len(rows) == 1
    assert rows[0]["params"] == {"lookback_months": 10}
    assert rows[0]["sharpe"] == 0.72


def test_satisfies_protocol(tmp_path: Path) -> None:
    log = JsonlTrialLog(tmp_path)
    assert isinstance(log, TrialLog)
