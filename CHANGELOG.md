# Changelog

All notable changes to the quant-system project, tracked wave-by-wave against the 20-week plan in `implementationplan.md`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
