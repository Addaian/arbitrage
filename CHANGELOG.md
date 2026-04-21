# Changelog

All notable changes to the quant-system project, tracked wave-by-wave against the 20-week plan in `implementationplan.md`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [Wave 2 — Week 2: Config, types, Postgres] — 2026-04-20

### Added
- `src/quant/types.py` — domain types: `Bar`, `Signal`, `Order`, `Fill`, `OrderResult`, `Position`, `Account` plus `OrderSide`/`OrderType`/`TimeInForce`/`OrderStatus`/`SignalDirection` enums. Frozen, extra=forbid, with OHLC-consistency and order-limit-price validators.
- `src/quant/config.py` — `Settings` (pydantic-settings, loads `.env` + env), plus YAML models `StrategyConfig`, `StrategiesConfig`, `UniverseConfig`, `RiskConfig`, `ConfigBundle`. Loader refuses malformed YAML, caps risk limits at PRD §6.1 ceilings, cross-validates that strategy universes are subsets of the master universe, and computes a stable SHA-256 `config_hash` for drift detection (PRD §6.1).
- `config/strategies.yaml`, `config/universe.yaml`, `config/risk.yaml` — starter values. Enabled strategy weights sum to 1.00.
- `src/quant/storage/models.py` — SQLAlchemy 2.0 ORM: `BarORM`, `OrderORM`, `FillORM`, `PositionORM`, `PnlSnapshotORM`, `SignalORM`, `BacktestRunORM`, with check constraints on OHLC ranges.
- `src/quant/storage/db.py` — async psycopg3 engine + `async_sessionmaker` + `session_scope()` context manager.
- `src/quant/storage/repos.py` — thin per-table repos (`BarRepo`, `OrderRepo`, `FillRepo`, `PositionRepo`, `PnlRepo`, `SignalRepo`, `BacktestRunRepo`) with Postgres `ON CONFLICT` upserts.
- `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial_schema.py` — first migration creates all seven tables with indexes, promotes `bars` to a TimescaleDB hypertable when the extension is present.
- `tests/unit/test_config.py` — 13 tests covering happy-path, weight-sum enforcement, duplicate rejection, PRD risk caps, malformed YAML, cross-universe validation.
- `tests/unit/test_types.py` — validates OHLC rules, limit-order constraints, symbol regex, signal weight bounds, immutability.
- `tests/integration/test_storage_roundtrip.py` — migrate-up → upsert Bar → read back → migrate-down, skips cleanly if Postgres unreachable.

### Changed
- `src/quant/storage/__init__.py` — re-exports ORM, repos, and session helpers.

## [Wave 1 — Week 1: Project scaffold] — 2026-04-20

### Added
- `CLAUDE.md` — project context and status doc for Claude Code sessions.
- `CHANGELOG.md` — this file.
- `pyproject.toml` — Python 3.12 project with V1 dependency set pinned per PRD §3.2.
- Full `src/quant/` package tree with empty modules per PRD §7.1 (data, features, signals, models, portfolio, execution, risk, backtest, live, monitoring, storage).
- `ruff.toml`, `mypy.ini`, `.pre-commit-config.yaml` — linting, strict typing on risk/execution/portfolio, git hooks.
- `.env.example` — documented required environment variables.
- `docker-compose.yml` — local Postgres 16 + TimescaleDB.
- `Makefile` — `test`, `lint`, `format`, `typecheck`, `up`, `down` targets.
- `tests/unit/test_smoke.py` — imports every module to guard the package tree.
- `.github/workflows/ci.yml` — runs ruff, mypy (strict on critical paths), pytest on every push/PR.
- `.gitignore` — Python + data + env + IDE exclusions.
- `Dockerfile` — production image skeleton.

### Notes
- No business logic yet; this wave is purely structural.
- `uv sync && make test` is the acceptance criterion.
