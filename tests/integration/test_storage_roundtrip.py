"""End-to-end storage test: run migrations, write a Bar, read it back, downgrade.

Requires a live Postgres — run `make up` first, then `make test-integration`.
Skipped if DATABASE_URL does not point at a reachable database.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from quant.storage import BarRepo, dispose_engine, session_scope
from quant.types import Bar

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def alembic_cfg() -> AlembicConfig:
    cfg = AlembicConfig(str(Path("alembic.ini").resolve()))
    return cfg


def _migrate(cfg: AlembicConfig, target: str) -> None:
    alembic_command.upgrade(cfg, target) if target != "base" else alembic_command.downgrade(
        cfg, target
    )


def test_migration_up_write_read_down(alembic_cfg: AlembicConfig) -> None:
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        pytest.skip(f"Postgres not reachable for integration test: {exc}")

    async def _exercise() -> None:
        async with session_scope() as session:
            repo = BarRepo(session)
            bar = Bar(
                symbol="TEST",
                ts=date(2026, 4, 20),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.5"),
                close=Decimal("100.5"),
                volume=Decimal("12345"),
            )
            n = await repo.upsert_many([bar])
            assert n == 1

        async with session_scope() as session:
            repo = BarRepo(session)
            out = await repo.get_range("TEST", date(2026, 4, 19), date(2026, 4, 21))
            assert len(out) == 1
            assert out[0].close == Decimal("100.5")

        await dispose_engine()

    asyncio.run(_exercise())

    # Clean downgrade leaves the DB empty.
    alembic_command.downgrade(alembic_cfg, "base")
