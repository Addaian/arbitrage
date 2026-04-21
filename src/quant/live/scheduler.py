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

import asyncio
import signal
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
