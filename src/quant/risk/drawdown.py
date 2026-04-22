"""Rolling drawdown tracker (PRD §6.1 rows 2-3).

Two time windows:

* **Daily loss** — today's equity vs the prior day's close. Breach at
  `-max_daily_loss_pct` triggers the 24h-halt killswitch.
* **Monthly drawdown** — trailing peak-to-current over a rolling
  30-calendar-day window. Breach at `-max_monthly_drawdown_pct`
  triggers flatten + manual-restart halt.

State: a list of `(ts, equity)` snapshots ordered by time. The tracker
is pure — callers push snapshots as the cycle produces them and query
either the current metric or whether it has breached. No DB here; the
runner is responsible for persistence via `PnlRepo`.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class EquitySnapshot:
    ts: datetime
    equity: Decimal


@dataclass
class DrawdownTracker:
    max_daily_loss_pct: float
    max_monthly_drawdown_pct: float
    monthly_window_days: int = 30
    _snapshots: list[EquitySnapshot] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 < self.max_daily_loss_pct < 1.0:
            raise ValueError(f"max_daily_loss_pct must be in (0, 1), got {self.max_daily_loss_pct}")
        if not 0.0 < self.max_monthly_drawdown_pct < 1.0:
            raise ValueError(
                f"max_monthly_drawdown_pct must be in (0, 1), got {self.max_monthly_drawdown_pct}"
            )
        if self.monthly_window_days <= 0:
            raise ValueError(
                f"monthly_window_days must be positive, got {self.monthly_window_days}"
            )

    def push(self, ts: datetime, equity: Decimal) -> None:
        """Append a snapshot. Caller's responsibility to push in order."""
        if self._snapshots and ts <= self._snapshots[-1].ts:
            raise ValueError(f"snapshot {ts} is not strictly after last {self._snapshots[-1].ts}")
        if equity < Decimal(0):
            raise ValueError(f"equity must be non-negative, got {equity}")
        self._snapshots.append(EquitySnapshot(ts=ts, equity=Decimal(equity)))

    @property
    def snapshots(self) -> list[EquitySnapshot]:
        return list(self._snapshots)

    @property
    def latest(self) -> EquitySnapshot | None:
        return self._snapshots[-1] if self._snapshots else None

    # --- Metrics -------------------------------------------------------

    def daily_loss_pct(self) -> float:
        """Today's equity / yesterday's equity - 1. Returns 0.0 on first
        snapshot (no prior to compare against).
        """
        if len(self._snapshots) < 2:
            return 0.0
        latest = self._snapshots[-1]
        prior = self._snapshots[-2]
        if prior.equity <= Decimal(0):
            return 0.0
        return float(latest.equity / prior.equity - Decimal(1))

    def monthly_drawdown_pct(self) -> float:
        """Current equity vs the rolling-30-day peak. Negative when in DD."""
        if not self._snapshots:
            return 0.0
        latest = self._snapshots[-1]
        window_start = latest.ts - timedelta(days=self.monthly_window_days)
        # Binary search for the first snapshot >= window_start.
        timestamps = [s.ts for s in self._snapshots]
        idx = bisect_left(timestamps, window_start)
        window = self._snapshots[idx:]
        # `window` is always non-empty here because bisect_left includes
        # the last snapshot when window_start <= latest.ts, which holds
        # by construction (window_start = latest.ts - timedelta).
        peak = max(s.equity for s in window)
        if peak <= Decimal(0):
            return 0.0
        return float(latest.equity / peak - Decimal(1))

    def breached_daily_loss(self) -> bool:
        return self.daily_loss_pct() <= -self.max_daily_loss_pct

    def breached_monthly_drawdown(self) -> bool:
        return self.monthly_drawdown_pct() <= -self.max_monthly_drawdown_pct

    def reset(self) -> None:
        """Clear all snapshots. Only call after an operational halt has
        been resolved manually — never automatically.
        """
        self._snapshots.clear()
