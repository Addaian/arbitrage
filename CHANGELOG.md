# Changelog

All notable changes to the quant-system project, tracked wave-by-wave against the 20-week plan in `implementationplan.md`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [Wave 5 — Week 5: Trend strategy + backtest engine] — 2026-04-21

### Added
- `src/quant/signals/base.py` — `SignalStrategy` runtime-checkable Protocol. All strategies emit a `target_weights` matrix (dates x symbols; NaN rows = hold, numeric rows = rebalance to those percentages).
- `src/quant/signals/trend.py` — `TrendSignal` (Faber/Antonacci GEM variant). Monthly 10-month SMA rule, equal-weighted across the risk universe, cash remainder to the configured cash symbol (SGOV/SHY). Rebalance is emitted on the **first trading day of the following month** — no look-ahead.
- `src/quant/backtest/engine.py` — custom daily-bar multi-asset backtest. `run_backtest(closes, weights)` returns a `BacktestResult` with equity curve, daily returns, applied weights, and per-rebalance trade log. Models commission + slippage as `fees+slippage x |Δweight|`. Helpers: `closes_from_bars`, `align_on_common_dates`, `clip_to_range`. (Chose a flat dot-product path over vectorbt to pin execution order to the published Faber methodology — rationale in the module docstring.)
- `src/quant/backtest/reports.py` — `Tearsheet` with CAGR, Sharpe, Sortino, Calmar, max DD + duration, monthly hit rate, turnover, total cost. `monthly_returns_pivot()` for the year x month heatmap.
- `scripts/run_backtest.py` — Typer CLI: `--strategy trend --start 2003-01-01 [--end --universe --cash-symbol --lookback-months --initial-cash --fees --slippage --cache-dir]`. Loads bars from the Parquet cache (falls back to the widest-range cached file per symbol), runs the signal, prints a Rich tearsheet table.
- `src/quant/signals/__init__.py`, `src/quant/backtest/__init__.py` — re-exports.
- `tests/unit/test_signals_trend.py` (11 tests) — contract rejections (missing cash symbol, non-positive lookback, no risk symbols), weight-sum invariants (sum to 1 on rebalance), warmup row is all-cash, all-long in uptrend, all-cash in downtrend, mixed signal splitting, monthly rebalance cadence, first-trading-day-of-month timing.
- `tests/unit/test_backtest_engine.py` (12 tests) — buy-and-hold equivalence, cash-flat equity, rebalance cost charging, column-mismatch rejection, empty/no-rebalance guards, tearsheet on flat equity, monthly pivot shape, bar-to-frame helpers, alignment drops, range clipping.

### Verified
- 137/137 unit tests green. Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **Acceptance (Faber profile, SPY/EFA/IEF + SHY cash, 2003-04-21 → 2026-04-21, 10-month SMA):** CAGR +5.48%, Sharpe 0.72, Sortino 1.12, max DD **-16.87%** vs 1/3-equal buy-and-hold at CAGR +8.25%, Sharpe 0.71, max DD **-38.52%**. Sharpe parity with >50% DD reduction — the canonical Faber tradeoff.
- **Parameter sensitivity (lookback sweep 6/9/10/12 months):** CAGR 5.48–5.90%, Sharpe 0.72–0.78. No cliff across the published Faber range — strategy is parameter-robust, not overfit to 10.
- End-to-end CLI runtime 1.1s for 23 years x 4 symbols (budget: 30s).

### Notes
- Cash proxy: `SGOV` (default per config) only has data back to 2020. For the 2003-onward acceptance backtest we pass `--cash-symbol SHY`, which has full-history coverage and behaves identically for the rule.
- No notebook — parameter sweeps are one-liners via the CLI. Will revisit if research intensity justifies it.

## [Wave 4 — Week 4: Feature engineering] — 2026-04-20

### Added
- `src/quant/features/technical.py` — `returns`, `log_returns`, `sma`, `ema`, `rsi` (Wilder smoothing), `ibs`, `atr`, `rolling_vol` (annualized), `ewma_vol` (RiskMetrics, for vol-target overlay), and `compute_technical_features` aggregator with canonical column naming (`close_sma_20`, `rsi_14`, etc.).
- `src/quant/features/cross_sectional.py` — `rank_cross_sectional` (percentile or ordinal), `top_n_mask`, `zscore_cross_sectional`, `demean_cross_sectional`, `universe_momentum` (lookback + optional skip-days).
- `src/quant/features/regime.py` — `vix_log_level`, `vix_percentile` (rolling rank), `term_structure_ratio`, `compute_regime_features` aggregator feeding the HMM overlay.
- `src/quant/features/__init__.py` — re-exports of the whole feature surface.
- `src/quant/data/pipeline.py` — added `bars_to_frame(list[Bar]) -> pd.DataFrame` (float-valued, inverse of `bars_from_ohlcv_frame`).
- `tests/unit/test_features_technical.py` (15 tests) — per-indicator coverage + aggregator shape/index preservation.
- `tests/unit/test_features_cross_sectional.py` (9 tests) — ordering, ties, zero-variance rows, momentum windows.
- `tests/unit/test_features_regime.py` (10 tests) — VIX transforms, percentile warmup, term-structure sign convention.
- `tests/unit/test_no_lookahead.py` (13 tests) — **property-style look-ahead guard**: every feature function satisfies `fn(shift(x)) == shift(fn(x))`. Parameterized across returns, log_returns, sma, ema, rsi, rolling_vol, ewma_vol, vix_log, vix_percentile, plus ibs/atr/aggregator/cross-sectional/regime-bundle.
- `tests/unit/test_features_benchmark.py` — `slow`-marked performance guard; **0.6s** to compute the full Week-4 feature surface on 10 synthetic ETFs × 20 years (budget was 10s).

### Verified
- 113/113 unit tests green (including benchmark). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- No-look-ahead invariant holds across all 13 feature entry points.

## [Wave 3 — Week 3: Data loaders + Parquet cache] — 2026-04-20

### Added
- `src/quant/data/pipeline.py` — `validate_bars()` drops duplicates and zero/negative volume rows, returns `ValidationReport` with drop-rate metric. `require_adjusted()` refuses unadjusted bars. `bars_from_ohlcv_frame()` converts pandas OHLCV DataFrames to `list[Bar]` with NaN + OHLC-violation filtering.
- `src/quant/data/cache.py` — `ParquetBarCache` + `CacheKey(symbol, start, end)`. Layout: `<root>/<symbol>/<start>_<end>.parquet`. Zstd compression. Decimal prices stored as strings for exact round-trip.
- `src/quant/data/loaders.py` — `BarLoader` runtime-checkable Protocol, `YFinanceLoader` (auto-adjusted EOD), `AlpacaLoader` (`adjustment=ALL`, IEX feed by default). Shared tenacity retry policy (3 attempts, exponential 1s→10s, transient IO errors only).
- `scripts/backfill.py` — Typer CLI. `backfill SPY QQQ --years 20 --source yfinance [--write-db] [--force]`. Rich progress table, cache-hit reporting.
- `src/quant/data/__init__.py` — re-exports loaders, cache, and pipeline helpers.
- `tests/unit/test_data_pipeline.py` (9 tests) — duplicate handling, zero-volume drop, drop-rate calc, adjusted-guard, frame→bar conversion + edge cases (NaN, OHLC violations, missing columns).
- `tests/unit/test_data_cache.py` (5 tests) — put/get round-trip, missing-key returns None, invalidate idempotency, cache-hit short-circuits loader, deterministic file path.
- `tests/unit/test_data_loaders.py` (6 tests) — Protocol runtime check (positive + negative), retry retries on ConnectionError, retry gives up after budget, retry ignores non-retryable errors, Decimal precision preserved through frame conversion.

### Changed
- `pyproject.toml` — added `greenlet>=3.0` (required by SQLAlchemy async; wasn't pulled transitively on macOS).
- `Makefile` — added comment pointing users to `make sync` when tool binaries are missing.
- `migrations/env.py` — keep `postgresql+psycopg://` URL so Alembic uses psycopg3 instead of falling back to psycopg2.

### Verified
- `backfill SPY QQQ --years 2`: first run (network) 4.9s, second run (cache) 0.8s — acceptance criterion <5s.
- `backfill SPY --years 1 --write-db`: 256 bars round-tripped yfinance → validate → Parquet → Postgres.
- 61/61 unit tests green, ruff clean, ruff format clean, mypy strict clean on risk/execution/portfolio.
- 1/1 integration test green (Alembic up → upsert Bar → read back → downgrade).

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
