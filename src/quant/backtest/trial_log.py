"""Append-only log of every backtest trial, keyed by strategy name.

The DSR (see `deflated_sharpe.py`) needs an accurate trial count per
strategy to deflate the benchmark Sharpe correctly. This module keeps
that count honest by persisting every run.

Two implementations share a tiny protocol:

* `JsonlTrialLog` — zero-dep, writes newline-delimited JSON under
  `data/`. Default for local dev and CI; no Postgres needed.
* `PostgresTrialLog` — wraps `quant.storage.repos.BacktestRunRepo`.
  Used in prod where the DB is always up.

Both expose `record(...)` and `count_trials(strategy)`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TrialRecord:
    strategy: str
    params: dict[str, object]
    start_date: date
    end_date: date
    sharpe: float | None
    cagr: float | None
    max_drawdown: float | None
    recorded_at: datetime

    @property
    def params_hash(self) -> str:
        canonical = json.dumps(self.params, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "params": self.params,
            "params_hash": self.params_hash,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "sharpe": self.sharpe,
            "cagr": self.cagr,
            "max_drawdown": self.max_drawdown,
            "recorded_at": self.recorded_at.isoformat(),
        }


@runtime_checkable
class TrialLog(Protocol):
    def record(self, trial: TrialRecord) -> None: ...
    def count_trials(self, strategy: str) -> int: ...


class JsonlTrialLog:
    """JSONL-backed trial log. One line per trial, append-only.

    Path layout: `<root>/<strategy>.jsonl`. A read of `count_trials`
    streams the file line-by-line so it scales without loading the
    whole history into memory.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, strategy: str) -> Path:
        # Strategy name may contain slashes in future combiners; keep it safe.
        safe = strategy.replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.jsonl"

    def record(self, trial: TrialRecord) -> None:
        path = self._path(trial.strategy)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trial.to_dict()) + "\n")

    def count_trials(self, strategy: str) -> int:
        path = self._path(strategy)
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def read_all(self, strategy: str) -> list[dict[str, object]]:
        path = self._path(strategy)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
