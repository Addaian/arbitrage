"""Tests for DrawdownTracker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant.risk import DrawdownTracker


def _mk(max_daily: float = 0.05, max_monthly: float = 0.15, window: int = 30) -> DrawdownTracker:
    return DrawdownTracker(
        max_daily_loss_pct=max_daily,
        max_monthly_drawdown_pct=max_monthly,
        monthly_window_days=window,
    )


def _ts(day: int) -> datetime:
    return datetime(2026, 4, day, 21, 0, tzinfo=UTC)


# --- Constructor guards -----------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_daily": 0.0},
        {"max_daily": 1.0},
        {"max_monthly": 0.0},
        {"max_monthly": 1.0},
        {"window": 0},
    ],
)
def test_rejects_bad_constructor_args(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        _mk(**kwargs)  # type: ignore[arg-type]


# --- Push ordering + validation ---------------------------------------


def test_push_rejects_out_of_order() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("100000"))
    with pytest.raises(ValueError, match="strictly after"):
        t.push(_ts(1), Decimal("100000"))


def test_push_rejects_negative_equity() -> None:
    t = _mk()
    with pytest.raises(ValueError, match="non-negative"):
        t.push(_ts(1), Decimal("-1"))


def test_zero_equity_allowed() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("0"))
    assert t.latest is not None
    assert t.latest.equity == Decimal("0")


# --- Daily loss -------------------------------------------------------


def test_daily_loss_first_snapshot_is_zero() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("100000"))
    assert t.daily_loss_pct() == 0.0
    assert not t.breached_daily_loss()


def test_daily_loss_computes_delta_vs_prior() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("100000"))
    t.push(_ts(2), Decimal("97000"))
    # 97k/100k - 1 = -0.03
    assert t.daily_loss_pct() == pytest.approx(-0.03)
    assert not t.breached_daily_loss()  # -3% < -5% threshold


def test_daily_loss_breach_at_threshold() -> None:
    t = _mk(max_daily=0.05)
    t.push(_ts(1), Decimal("100000"))
    t.push(_ts(2), Decimal("94000"))  # -6% → breached
    assert t.breached_daily_loss()


def test_daily_loss_exact_threshold_breached() -> None:
    t = _mk(max_daily=0.05)
    t.push(_ts(1), Decimal("100000"))
    t.push(_ts(2), Decimal("95000"))  # exactly -5%
    assert t.breached_daily_loss()  # <= crosses


def test_daily_loss_handles_zero_prior_equity() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("0"))
    t.push(_ts(2), Decimal("100"))
    # divide-by-zero guarded → 0.0
    assert t.daily_loss_pct() == 0.0


# --- Monthly drawdown -------------------------------------------------


def test_monthly_drawdown_empty_returns_zero() -> None:
    t = _mk()
    assert t.monthly_drawdown_pct() == 0.0


def test_monthly_drawdown_single_snapshot() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("100000"))
    assert t.monthly_drawdown_pct() == 0.0


def test_monthly_drawdown_peak_within_window() -> None:
    t = _mk(window=30)
    t.push(_ts(1), Decimal("100000"))
    t.push(_ts(5), Decimal("110000"))  # new peak
    t.push(_ts(10), Decimal("99000"))  # down 10% from peak
    assert t.monthly_drawdown_pct() == pytest.approx(-0.1, abs=1e-9)
    assert not t.breached_monthly_drawdown()


def test_monthly_drawdown_breach_at_threshold() -> None:
    t = _mk(max_monthly=0.15, window=30)
    t.push(_ts(1), Decimal("100000"))
    t.push(_ts(15), Decimal("83000"))  # -17% from peak
    assert t.breached_monthly_drawdown()


def test_monthly_drawdown_excludes_peak_before_window() -> None:
    """Peak that's outside the 30-day window should NOT count."""
    t = _mk(max_monthly=0.15, window=30)
    base_ts = datetime(2026, 1, 1, 21, 0, tzinfo=UTC)
    t.push(base_ts, Decimal("200000"))  # very high peak, 60 days ago
    t.push(base_ts + timedelta(days=60), Decimal("100000"))  # today
    # Peak outside window → drawdown is 0 (today is the only in-window peak).
    assert t.monthly_drawdown_pct() == pytest.approx(0.0, abs=1e-9)


def test_monthly_drawdown_uses_bisect_for_window_boundary() -> None:
    """Snapshot exactly at the window boundary should be included."""
    t = _mk(window=30)
    base = datetime(2026, 3, 1, 21, 0, tzinfo=UTC)
    t.push(base, Decimal("100000"))
    t.push(base + timedelta(days=30), Decimal("90000"))
    # Both included → peak 100k, latest 90k → -10%.
    assert t.monthly_drawdown_pct() == pytest.approx(-0.1, abs=1e-9)


def test_monthly_drawdown_handles_zero_peak() -> None:
    """Degenerate: window is all zeros → div-by-zero guarded to 0."""
    t = _mk()
    t.push(_ts(1), Decimal("0"))
    t.push(_ts(2), Decimal("0"))
    assert t.monthly_drawdown_pct() == 0.0


# --- Snapshots, latest, reset -----------------------------------------


def test_snapshots_returns_a_copy() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("100000"))
    snaps = t.snapshots
    snaps.clear()
    assert t.latest is not None


def test_reset_clears_state() -> None:
    t = _mk()
    t.push(_ts(1), Decimal("100000"))
    t.push(_ts(2), Decimal("90000"))
    t.reset()
    assert t.latest is None
    assert t.daily_loss_pct() == 0.0


def test_latest_none_when_empty() -> None:
    assert _mk().latest is None
