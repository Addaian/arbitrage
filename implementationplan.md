# Implementation Plan — Quant Trading System

**Version:** 1.0
**Date:** April 21, 2026
**Duration:** 20 weeks (1 solo developer, ~20-30 hrs/week)
**Companion docs:** [PRD.md](./PRD.md), [README.md](./README.md)

---

## How to use this document

Each week has:
- **Goal:** the single vertical slice shipped
- **Tasks:** concrete, checkboxable work
- **Deliverables:** what exists at end of week
- **Acceptance criteria:** how you know it's actually done

If a week slips, **do not add weeks at the end.** Cut scope from the current week and keep the calendar. Burnout is the top risk; see [PRD.md §10](./PRD.md).

Rule: **no week ends without passing tests and a green CI build on `main`.**

---

## Phase 0: Setup (pre-week 1)

Before the 20-week clock starts, have these in place. Should take a weekend.

- [ ] GitHub private repo created
- [ ] Alpaca paper account created, API keys in password manager
- [ ] Discord server created, webhook URL captured
- [ ] Sentry account, free tier, DSN captured
- [ ] Hetzner account registered (don't provision VPS yet — V1 runs locally first)
- [ ] Local dev machine: Python 3.12 via `uv`, Docker Desktop, VS Code or similar
- [ ] `gh` CLI configured, SSH keys pushed
- [ ] Read [PRD.md](./PRD.md) in full, once, cover to cover

---

## Phase 1: Foundation (Weeks 1-4)

### Week 1 — Project scaffold

**Goal:** Empty-but-correct project skeleton, CI green, one trivial test passing.

**Tasks:**
- [ ] `uv init quant-system`, set Python 3.12
- [ ] Commit `pyproject.toml` with all V1 dependencies pinned (see [PRD.md §3.2](./PRD.md))
- [ ] Create full directory structure per [PRD.md §7.1](./PRD.md)
- [ ] Add `ruff.toml`, `mypy.ini`, `.pre-commit-config.yaml`
- [ ] Write one trivial test in `tests/unit/test_smoke.py` that imports every module
- [ ] Set up GitHub Actions CI: lint + mypy + pytest on push
- [ ] Copy [PRD.md](./PRD.md), [README.md](./README.md), [implementationplan.md](./implementationplan.md) into repo root
- [ ] Create `.env.example` with every required variable documented
- [ ] Create `docker-compose.yml` with Postgres 16 + TimescaleDB extension
- [ ] Create `Makefile` with `make test`, `make lint`, `make format`, `make up`, `make down`

**Deliverables:** A clean repo on GitHub, CI green on first commit, `make test` runs locally.

**Acceptance:** Fresh clone + `uv sync && make test` succeeds in <2 minutes on your machine.

---

### Week 2 — Config, types, and Postgres

**Goal:** Pydantic config loading, shared types, and a database schema.

**Tasks:**
- [ ] `src/quant/config.py`: `Settings` class using `pydantic-settings`, loads `.env` + YAML
- [ ] `src/quant/types.py`: dataclasses/Pydantic models for `Bar`, `Order`, `Fill`, `Position`, `Account`, `Signal`
- [ ] `config/strategies.yaml`, `config/universe.yaml`, `config/risk.yaml` — starter values, validated by `StrategyConfig`, `UniverseConfig`, `RiskConfig`
- [ ] Alembic init, first migration: tables for `bars`, `orders`, `fills`, `positions`, `pnl_snapshots`, `signals`
- [ ] `src/quant/storage/db.py`: async psycopg3 connection pool, session helper
- [ ] `src/quant/storage/repos.py`: one repo per table, minimal CRUD
- [ ] Unit tests for config loading (including invalid configs must raise)
- [ ] Integration test: migrate up, write a Bar, read it back, migrate down

**Deliverables:** Configs load with validation, database schema in place, repos tested.

**Acceptance:** `alembic upgrade head` + `alembic downgrade base` works cleanly. Malformed YAML refuses to load.

---

### Week 3 — Data loaders and Parquet cache

**Goal:** Pull EOD bars, validate, cache to Parquet, write to Postgres.

**Tasks:**
- [ ] `src/quant/data/loaders.py`: `YFinanceLoader` and `AlpacaLoader`, shared interface
- [ ] `src/quant/data/cache.py`: Parquet cache keyed by `(symbol, start, end)`, stored under `data/parquet/`
- [ ] `src/quant/data/pipeline.py`: validation (no nulls, OHLC consistency, volume > 0), split/dividend adjustment
- [ ] `scripts/backfill.py`: CLI to pull 20 years of daily bars for the V1 universe and cache them
- [ ] Retry logic with `tenacity` on loader failures
- [ ] Tests: loader returns correctly shaped DataFrame, cache hit avoids API call, malformed bars get filtered

**Deliverables:** `scripts/backfill.py SPY QQQ EFA EEM GLD IEF TLT VNQ DBC XLE --years 20` completes and caches ~70MB of Parquet.

**Acceptance:** Second run of the same backfill finishes in <5 seconds (all from cache). Validation catches injected bad bars.

---

### Week 4 — Feature engineering

**Goal:** Reusable feature library — technical, cross-sectional, regime.

**Tasks:**
- [ ] `src/quant/features/technical.py`: returns, log returns, SMA, EMA, RSI, ATR, IBS, rolling vol
- [ ] `src/quant/features/cross_sectional.py`: rank, z-score across universe at each date
- [ ] `src/quant/features/regime.py`: VIX-based features (level, percentile, term-structure ratio)
- [ ] All feature functions take DataFrame, return DataFrame with named columns
- [ ] No look-ahead bias anywhere — enforced by test that shifts inputs and checks outputs shift by same amount
- [ ] `notebooks/01_data_exploration.ipynb` — visual sanity check on features

**Deliverables:** Feature library with full unit tests, notebook showing features computed on SPY 2003-2026.

**Acceptance:** Running full feature engineering on 10 ETFs × 20 years takes <10 seconds. Look-ahead tests all pass.

---

## Phase 2: First Strategy Vertical Slice (Weeks 5-9)

### Week 5 — Trend strategy, vectorbt backtest

**Goal:** Antonacci GEM-style trend strategy running in vectorbt, reporting tearsheet.

**Tasks:**
- [ ] `src/quant/signals/trend.py`: `TrendSignal` class, computes target weights per rebalance date
- [ ] `src/quant/backtest/engine.py`: `BacktestEngine` wrapping vectorbt, takes signals + bars → returns Portfolio
- [ ] `src/quant/backtest/reports.py`: tearsheet with CAGR, Sharpe, Sortino, Calmar, max DD, monthly returns heatmap
- [ ] `scripts/run_backtest.py`: CLI entry point
- [ ] Unit tests: signal shape is correct, weights sum to 1 on active dates, cash when all signals negative
- [ ] `notebooks/02_trend_research.ipynb` — parameter sweep of lookback period (6, 9, 10, 12 months)

**Deliverables:** `scripts/run_backtest.py --strategy trend --start 2003-01-01` produces a full tearsheet in <30 seconds.

**Acceptance:** Backtest result for 10-month SMA on SPY/EFA/IEF 2003-2026 matches published Faber numbers within 1% CAGR.

---

### Week 6 — Walk-forward + Deflated Sharpe

**Goal:** Validation harness that kills overfit strategies before they go live.

**Tasks:**
- [ ] `src/quant/backtest/walk_forward.py`: rolling 10yr train / 2yr test, expandable window option
- [ ] `src/quant/backtest/deflated_sharpe.py`: Bailey & Lopez de Prado DSR formula
- [ ] `scripts/validate_strategy.py`: runs WF, computes DSR, reports pass/fail vs configured thresholds
- [ ] Parameter tracking: log every backtest run (params + Sharpe) to Postgres so DSR uses accurate trial count
- [ ] Tests: WF windows don't overlap incorrectly, DSR with known inputs matches published examples

**Deliverables:** The trend strategy from week 5 passes validation with OOS Sharpe ≥ 0.4 and DSR > 0.

**Acceptance:** Injecting a deliberately overfit 3-parameter variant causes `validate_strategy.py` to **fail** it correctly.

---

### Week 7 — Broker abstraction

**Goal:** `Broker` interface with `AlpacaBroker` and `PaperBroker` implementations, same API.

**Tasks:**
- [ ] `src/quant/execution/broker_base.py`: abstract `Broker` class per [PRD.md §4.3](./PRD.md)
- [ ] `src/quant/execution/alpaca_broker.py`: using `alpaca-py`, wraps `TradingClient`
- [ ] `src/quant/execution/paper_broker.py`: in-memory simulator — accepts orders, fills at next bar's open with configurable slippage
- [ ] `src/quant/execution/order_manager.py`: order lifecycle (submitted → accepted → filled/rejected), retries on transient failures
- [ ] Unit tests: paper broker fills correctly, reconciliation logic, partial fills, rejections
- [ ] Integration test: place an Alpaca paper order, poll until filled, reconcile

**Deliverables:** Both brokers implement the full interface. `pytest tests/integration/test_alpaca_broker.py` passes end-to-end against Alpaca paper API.

**Acceptance:** Swapping `AlpacaBroker` for `PaperBroker` in a test fixture changes nothing about the calling code.

---

### Week 8 — LiveRunner (paper mode)

**Goal:** End-to-end daily cycle running in paper mode — data → signal → order → fill.

**Tasks:**
- [ ] `src/quant/live/runner.py`: `LiveRunner.run_daily_cycle()` orchestrates the full pipeline per [PRD.md §4.2](./PRD.md)
- [ ] `src/quant/live/scheduler.py`: APScheduler-driven loop, calls runner at configured times
- [ ] Reconciliation: compare broker positions vs our expected state, log any drift
- [ ] State persistence: every cycle writes positions, orders, fills, pnl to Postgres
- [ ] Discord webhook alerts: cycle start, cycle complete, errors
- [ ] Integration test: simulate a full day cycle against `PaperBroker`

**Deliverables:** `uv run python -m quant.live.runner --mode paper --dry-run` simulates today's cycle in <10 seconds and logs to Postgres.

**Acceptance:** Running the runner in paper mode 3 days in a row shows coherent state in Postgres (no duplicate orders, no ghost positions).

---

### Week 9 — First paper deployment (local)

**Goal:** Trend strategy running live paper on your local machine for 5 trading days.

**Tasks:**
- [ ] Enable scheduler, run locally from Mon-Fri at 3:45pm ET
- [ ] Each evening, review Discord summary: positions, P&L, any warnings
- [ ] Track discrepancies between backtest-expected behavior and paper-observed behavior
- [ ] Fix any bugs that surface (there will be bugs)
- [ ] Document every oddity in `docs/journal.md`

**Deliverables:** 5 trading days of paper P&L logged, Discord alerts working, zero unhandled exceptions.

**Acceptance:** You can look at Grafana (or a simple notebook) and see daily equity for the paper account tracking sensibly.

---

## Phase 3: Portfolio of Strategies (Weeks 10-12)

### Week 10 — Cross-sectional momentum

**Goal:** Add a second strategy with demonstrably low correlation to trend.

**Tasks:**
- [ ] `src/quant/signals/momentum.py`: rank 10 ETFs by 6mo return, hold top 3 equal-weight
- [ ] Backtest 2003-2026 with tearsheet
- [ ] Walk-forward validation, must pass thresholds
- [ ] Correlation analysis: daily returns of momentum strategy vs trend strategy — target <0.5
- [ ] Wire into config with `weight: 0.3`

**Deliverables:** Both strategies run concurrently in backtest; combined portfolio has higher Sharpe than either alone.

**Acceptance:** Combined portfolio Sharpe is ≥ 110% of the best single-strategy Sharpe.

---

### Week 11 — Mean reversion overlay

**Goal:** Third strategy, short-term contrarian, different return profile.

**Tasks:**
- [ ] `src/quant/signals/mean_reversion.py`: IBS < 0.2 AND RSI-2 < 10 → buy, exit when IBS > 0.7
- [ ] Handle daily rebalance cadence (different from monthly)
- [ ] Backtest + WF validation
- [ ] Correlation vs trend and momentum
- [ ] Wire into config with `weight: 0.15`

**Deliverables:** 3-strategy portfolio backtested and validated.

**Acceptance:** Mean reversion returns correlate negatively with trend during calm periods (as expected).

---

### Week 12 — Risk layer (hard limits)

**Goal:** Every risk rule from [PRD.md §6](./PRD.md) enforced in code with property tests.

**Tasks:**
- [ ] `src/quant/risk/limits.py`: `RiskValidator` class with one method per limit
- [ ] `src/quant/risk/drawdown.py`: rolling drawdown tracker (daily, monthly)
- [ ] `src/quant/risk/killswitch.py`: file sentinel check + Discord command handler
- [ ] Pre-trade hook in `OrderManager`: every order runs through `RiskValidator.validate()` before submission
- [ ] Property tests (hypothesis): 10,000 random orders — no valid order rejected, no invalid order accepted
- [ ] Chaos test: force daily loss > -5% in paper, verify auto-flatten and halt

**Deliverables:** Risk layer with 100% test coverage. Property tests pass.

**Acceptance:** Manually creating `/var/run/quant/HALT` mid-cycle flattens the paper account within one cycle.

---

## Phase 4: Sophistication (Weeks 13-16)

### Week 13 — Research sprint: which strategies survive?

**Goal:** Honest evaluation. Some strategies may not pass WF. Cut them.

**Tasks:**
- [ ] Run full WF on all 3 strategies across 2003-2026
- [ ] Compute Deflated Sharpe using actual trial count from Postgres log
- [ ] Stress tests: 2008, 2020, 2022, April 2025 — any strategy that blows up in any of these gets cut or repaired
- [ ] Regime-conditioned performance: does each strategy earn alpha in multiple regimes or just one?
- [ ] Document conclusions in `docs/research/week13_validation.md`

**Deliverables:** A go/no-go decision for each strategy. Config updated to reflect surviving set.

**Acceptance:** At least 2 of 3 strategies pass all validation gates. If only 1 survives, you ship with 1 and add more in V2 — **do not** weaken the thresholds.

---

### Week 14 — Walk-forward harness refinement

**Goal:** Automation of the validation process so future strategies can be added cheaply.

**Tasks:**
- [ ] `scripts/validate_new_strategy.py`: one command, full validation report in markdown
- [ ] CI check: PRs touching `src/quant/signals/` require a validation artifact attached
- [ ] Template: `docs/strategy_template.md` — every new strategy gets a doc with hypothesis, feature list, WF results

**Deliverables:** Adding a new strategy is a well-defined, hours-not-days process.

**Acceptance:** You can add a deliberately bad strategy and have CI + validation block it.

---

### Week 15 — HMM regime classifier

**Goal:** Regime-conditioned sizing. When in stress regime, dial down risk.

**Tasks:**
- [ ] `src/quant/models/hmm_regime.py`: `RegimeHMM` using `hmmlearn`, trains on weekly returns + VIX + term structure, outputs 3-state probabilities
- [ ] `scripts/train_regime.py`: weekly retrain, write model artifact to `data/models/`
- [ ] `src/quant/portfolio/sizing.py`: position multiplier = `1 - p(stress)`
- [ ] Unit tests: known-state synthetic data recovers correct regime with >80% accuracy
- [ ] Backtest with regime overlay vs without — document Sharpe delta

**Deliverables:** Regime overlay live in the portfolio. Sharpe improvement documented.

**Acceptance:** Regime overlay reduces max DD by ≥20% in backtest with minimal CAGR cost (<15% reduction).

---

### Week 16 — Volatility targeting

**Goal:** Portfolio-level vol scaling to ~10% annualized.

**Tasks:**
- [ ] `src/quant/models/volatility.py`: EWMA vol forecaster with λ=0.94
- [ ] `src/quant/portfolio/sizing.py`: global exposure scalar = `target_vol / forecast_vol`, capped at 100%
- [ ] Full-portfolio backtest with vol targeting enabled
- [ ] Document pre/post metrics: Sharpe up or equal, vol flat, max DD down

**Deliverables:** Portfolio targets 10% annualized vol. Realized vol in backtest within 20% of target.

**Acceptance:** Portfolio CAGR/vol profile looks like a disciplined systematic fund, not a roulette wheel.

---

## Phase 5: Production Deployment (Weeks 17-20)

### Week 17 — VPS provisioning + systemd

**Goal:** Production VPS set up, system runs from there in paper mode.

**Tasks:**
- [ ] Provision Hetzner CX22 (ARM) or CPX21 (x86), Ubuntu 24.04
- [ ] Harden: UFW firewall (deny all, allow SSH + Grafana), SSH key-only, fail2ban, unattended-upgrades
- [ ] Install Python 3.12, uv, Docker, Postgres native (not Docker — more stable for prod)
- [ ] Create `quant` user, deploy directory `/opt/quant-system`
- [ ] Clone repo, `uv sync`, run migrations
- [ ] `deploy/systemd/quant-scheduler.service` + `.timer`
- [ ] `deploy/systemd/quant-runner.service`
- [ ] `deploy/bootstrap.sh`: one-command VPS bootstrap (should be idempotent)
- [ ] Verify: system runs paper cycle successfully from VPS

**Deliverables:** Fully functional VPS deployment. `ssh vps systemctl status quant-scheduler` shows active.

**Acceptance:** A cold-booted fresh VPS can be brought to running state in <30 minutes via `bootstrap.sh`.

---

### Week 18 — Monitoring + 30-day paper run

**Goal:** Prometheus + Grafana + Sentry + Discord. Begin the 30-day paper qualifier.

**Tasks:**
- [ ] Prometheus + Grafana running in Docker on the VPS (`deploy/prometheus/docker-compose.yml`)
- [ ] Custom Prometheus metrics exported from `LiveRunner`: equity, positions count, order latency, P&L, Sharpe (rolling 30d)
- [ ] Grafana dashboard: equity curve, per-strategy contribution, risk metrics, alert firing history
- [ ] Sentry integration: unhandled exceptions → Sentry + Discord critical
- [ ] Alertmanager → Discord: all alerts from [PRD.md §6.3](./PRD.md)
- [ ] **Start the 30-day paper qualifier window on Monday of week 18**
- [ ] Daily: review dashboard, note anomalies in `docs/journal.md`

**Deliverables:** Full observability stack, 30-day clock ticking.

**Acceptance:** You can look at your phone at 4pm ET and know whether today's cycle went well from the Discord summary alone.

---

### Week 19 — Final pre-live checks

**Goal:** Paper qualifier analysis + go-live readiness.

**Tasks:**
- [ ] Compute 30-day paper Sharpe vs expected backtest Sharpe on same period — tracking error must be <50%
- [ ] Review every alert fired during paper run: was each one valid?
- [ ] Run a full **disaster recovery drill**: kill the VPS, restore from scratch in <1 hour
- [ ] Run a **kill-switch drill**: touch HALT file mid-cycle, verify flattening
- [ ] Tax setup: confirm Alpaca live account KYC complete, beneficiary set
- [ ] Checklist the go-live gate criteria from [PRD.md §1.2](./PRD.md)

**Deliverables:** Go-live decision made. If any criterion fails, extend paper by 30 days — **do not** go live with unresolved issues.

**Acceptance:** A printed go-live checklist with every item checked, signed/dated by you.

---

### Week 20 — Go live (10% capital)

**Goal:** First live capital deployed. Small.

**Tasks:**
- [ ] Fund Alpaca live account with 10% of intended target (e.g., if target is $1,500, deploy $150)
- [ ] Flip `BROKER_PROVIDER=alpaca` and `PAPER_MODE=false` on the VPS
- [ ] Restart services, verify positions correctly reflect target weights on first cycle
- [ ] **Monitor daily for entire week** — 10 min/day minimum
- [ ] After 1 week clean live, scale plan:
  - Week +2 (week 22): 50% of target if tracking is within expectations
  - Week +6 (week 26): 100% of target capital
- [ ] Write a "Day 1 live" retrospective

**Deliverables:** Live trading account with 10% capital, running autonomously.

**Acceptance:** End of week 20, live account has been trading for 5 days with no manual interventions required.

---

## Post-launch (week 21+)

### Ongoing operations

- **Daily (5-10 min):** glance at Discord EOD summary, Grafana dashboard
- **Weekly (30 min):** compute live vs backtest tracking error, check for regime shifts
- **Monthly (2-3 hrs):** full portfolio review, research journal update, any config tuning documented in PR
- **Quarterly:** dependency updates, security review, backup restoration test
- **Annually:** full re-validation of all strategies, retire anything that's decayed

### V2 planning (months 7-12)

Once V1 has 3 months of clean live history, start planning V2 features. See [PRD.md §2.3](./PRD.md) for the V2 scope.

**Do not** start V2 work while V1 is still proving itself. This is the single most common failure mode for solo builders.

---

## Cumulative time estimate

| Phase | Weeks | Total hours (25 hrs/wk avg) |
|---|---|---|
| Foundation | 1-4 | ~100 hours |
| First Strategy | 5-9 | ~125 hours |
| Portfolio | 10-12 | ~75 hours |
| Sophistication | 13-16 | ~100 hours |
| Production | 17-20 | ~100 hours |
| **Total to live** | **20 weeks** | **~500 hours** |

If working 10-15 hrs/week instead of 25-30, double the calendar to 40 weeks — but keep the same sequence. Do not skip phases.

---

## Go/no-go gates (cannot be skipped)

These are the moments where the plan says "stop and evaluate, possibly retreat":

**Gate 1 — End of Week 6:** Does the trend strategy pass Deflated Sharpe > 0 and walk-forward OOS Sharpe ≥ 0.4? If no, spend week 7 fixing or replacing before continuing. If still no after that, reconsider the project.

**Gate 2 — End of Week 13:** Do at least 2 of 3 strategies survive full validation? If only 1 survives, continue with 1-strategy portfolio. If zero survive, **stop** — you've learned the problem is harder than planned and should reassess.

**Gate 3 — End of Week 19 (paper qualifier):** Is 30-day paper Sharpe within 50% of backtest Sharpe? If no, extend paper 30 days and debug tracking error before going live.

**Gate 4 — Week 20 + 1 week live:** Is the system running autonomously with no critical alerts? If no, revert to paper, debug, re-qualify.

---

## Things that will go wrong (plan for them)

1. **Data provider rate-limits or schema changes you** — have Polygon.io as a fallback, budget to switch.
2. **Alpaca goes down on a rebalance day** — the runner should retry; if it still fails, skip the cycle, don't panic-order the next day.
3. **A strategy that backtests beautifully lives poorly** — this is the rule, not the exception. Gate 3 catches this.
4. **You find a bug in your risk code at week 15** — stop, fix, add a property test that would have caught it. Never skip Gate 4 because "it was a small bug."
5. **You get bored around week 10-12** — the sophistication phase is where solo projects die. The milestone structure is designed to keep you shipping weekly. Follow it.
6. **You discover a "great new strategy" at week 14** — write it in `docs/ideas.md`. Do not touch V1 scope. It's a V2 feature.

---

## Final word

This plan is **prescriptive**, not **optional**. The path to a working quant system isn't "figure it out as you go" — it's "follow the path that working solo quants have retroactively documented, which is exactly this." The interesting variable is your discipline, not your strategies.

Build the system. Ship weekly. Validate rigorously. Go live small.
