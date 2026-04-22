"""APScheduler wrapper — triggers `LiveRunner.run_daily_cycle()` at the
configured time on trading weekdays.

For V1 we schedule one job: 3:45pm America/New_York, Mon-Fri. Holidays
are not filtered here — the broker will reject orders outside market
hours, which surfaces as an `OrderRejectedError` in the runner and gets
reported to Discord. Keeping this layer stupid avoids maintaining a
holiday calendar inside the scheduler.

Production invocation is `systemd`: the service file calls
`python -m quant.live.scheduler`, which creates the scheduler, adds the
job, and blocks forever. Signals (SIGTERM) shut it down gracefully.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import time as clock_time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

CycleCallable = Callable[[], Awaitable[object]]


@dataclass
class ScheduleSpec:
    """One recurring cycle trigger. Defaults match PRD §4.2 (3:45pm ET)."""

    hour: int = 15
    minute: int = 45
    day_of_week: str = "mon-fri"
    timezone: str = "America/New_York"


class CycleScheduler:
    def __init__(self, cycle: CycleCallable, *, spec: ScheduleSpec | None = None) -> None:
        self._cycle = cycle
        self._spec = spec or ScheduleSpec()
        self._scheduler = AsyncIOScheduler()

    def add_daily_cycle(self) -> None:
        trigger = CronTrigger(
            day_of_week=self._spec.day_of_week,
            hour=self._spec.hour,
            minute=self._spec.minute,
            timezone=self._spec.timezone,
        )
        self._scheduler.add_job(
            self._run_cycle_safely,
            trigger=trigger,
            id="daily-cycle",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    async def _run_cycle_safely(self) -> None:
        try:
            await self._cycle()
        except Exception as exc:
            # Never let a bad cycle kill the scheduler.
            logger.exception("daily cycle raised: {}", exc)

    def start(self) -> None:
        self._scheduler.start()
        logger.info(
            "scheduler started: {} daily at {:02d}:{:02d} {}",
            self._spec.day_of_week,
            self._spec.hour,
            self._spec.minute,
            self._spec.timezone,
        )

    async def run_forever(self) -> None:
        """Start the scheduler and block until SIGINT/SIGTERM."""
        self.add_daily_cycle()
        self.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        self._scheduler.shutdown()

    @property
    def next_fire_time(self) -> clock_time | None:
        job = self._scheduler.get_job("daily-cycle")
        return job.next_run_time if job else None


# --- CLI entry point ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """`python -m quant.live.scheduler` — block on the daily-cycle loop.

    Deliberately imports `LiveRunner` lazily so `--help` is instant and
    doesn't pay for pandas/alpaca-py/etc just to print usage.
    """
    parser = argparse.ArgumentParser(prog="quant.live.scheduler")
    parser.add_argument(
        "--broker",
        choices=["paper", "alpaca-paper", "alpaca-live"],
        default="alpaca-paper",
        help=(
            "paper = local simulator; "
            "alpaca-paper = Alpaca paper API (default); "
            "alpaca-live = REAL MONEY (requires QUANT_ENV=live in .env)"
        ),
    )
    parser.add_argument(
        "--persist", action="store_true", default=True, help="write cycle state to Postgres"
    )
    parser.add_argument(
        "--no-persist",
        action="store_false",
        dest="persist",
        help="skip DB writes (useful for smoke tests)",
    )
    parser.add_argument("--hour", type=int, default=15, help="cron hour (ET)")
    parser.add_argument("--minute", type=int, default=45, help="cron minute")
    parser.add_argument(
        "--day-of-week",
        default="mon-fri",
        help="cron day-of-week spec (default: mon-fri)",
    )
    args = parser.parse_args(argv)

    # Deferred import: keeps `--help` snappy and avoids a circular import
    # (runner imports from quant.live, which re-exports this module).
    from quant.live.runner import _build_default_runner  # noqa: PLC0415

    runner = _build_default_runner(broker_kind=args.broker, dry_run=False, persist=args.persist)

    async def _cycle() -> object:
        return await runner.run_daily_cycle()

    scheduler = CycleScheduler(
        _cycle,
        spec=ScheduleSpec(hour=args.hour, minute=args.minute, day_of_week=args.day_of_week),
    )
    logger.info(
        "starting scheduler: broker={} persist={} schedule={} {:02d}:{:02d} ET",
        args.broker,
        args.persist,
        args.day_of_week,
        args.hour,
        args.minute,
    )
    try:
        asyncio.run(scheduler.run_forever())
    except KeyboardInterrupt:  # pragma: no cover — Ctrl-C path
        logger.info("scheduler stopped by SIGINT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
