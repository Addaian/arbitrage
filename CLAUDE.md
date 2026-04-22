# CLAUDE.md

Project-specific context for Claude Code sessions. Keep this updated as the project evolves.

## What this project is

A solo-developer systematic trading system for US ETFs. Daily-bar strategies (trend following, cross-sectional momentum, mean reversion) combined under HMM regime overlay + portfolio vol targeting, executed through Alpaca. Same code runs backtest / paper / live behind a broker interface.

**Authoritative docs:**
- `PRD.md` — full product spec (scope, stack, architecture, risk limits)
- `implementationplan.md` — prescriptive 20-week build plan, one wave per week
- `README.md` — onboarding for a human developer
- `CHANGELOG.md` — wave-by-wave delivery log

When in doubt, the PRD wins. If the PRD is silent, the implementation plan wins.

## Working style

Work is delivered in **waves** that map 1:1 to implementation plan weeks. After each wave:
1. Update `CHANGELOG.md` under the current wave section.
2. Update the **Status** section below.
3. Stop. The user commits + pushes.

Do not run `git commit` or `git push` unless explicitly asked.

## Status

**Current wave:** 20 — Go live 10% capital (complete, awaiting commit + operational flip to real money)
**Next wave:** _none — V1 code build complete; remaining work is operational: Gate 3 sign-off, fund Alpaca live, `make live-switch`, Gate 4 review at week 21_

### Completed
- **Wave 1 (Week 1)** — project scaffold, CI, smoke test
- **Wave 2 (Week 2)** — Pydantic Settings + YAML configs, shared domain types, SQLAlchemy 2.0 ORM, async psycopg3 pool, repos, Alembic initial migration (verified end-to-end against Dockerized Postgres + TimescaleDB)
- **Wave 3 (Week 3)** — YFinance + Alpaca loaders, Parquet cache, validation pipeline, `scripts/backfill.py` CLI (cache-hit path measured at 0.8s, vs 4.9s network fetch)
- **Wave 4 (Week 4)** — technical / cross-sectional / regime feature libraries, look-ahead-bias property test across 13 entry points, benchmark at 0.6s (budget 10s) for 10 ETFs × 20 years
- **Wave 5 (Week 5)** — Faber-style `TrendSignal`, daily-bar backtest engine + tearsheet, `scripts/run_backtest.py` CLI. Acceptance on SPY/EFA/IEF + SHY 2003-2026: Sharpe 0.72 vs 1/3 buy-and-hold 0.71, max DD cut -38.5% → -16.9% — canonical Faber profile. 137/137 tests green.
- **Wave 6 (Week 6)** — walk-forward + Deflated Sharpe harness (`walk_forward`, Bailey/LdP DSR, JSONL trial log, `scripts/validate_strategy.py`). Gate 1 cleared: trend OOS Sharpe +0.60 ≥ 0.4, DSR probability 0.744, deflated excess +0.19; adversarial 3-param sweep (18 trials) correctly rejected (exit 1, deflated excess -0.05). 174/174 tests green.
- **Wave 7 (Week 7)** — `Broker` ABC with `PaperBroker` (deterministic simulator) and `AlpacaBroker` (alpaca-py wrapper) behind a shared surface; `OrderManager` drives submit/retry/poll with tenacity. 100% coverage on `src/quant/execution/`. Live Alpaca paper round-trip test gated on creds. 226/226 tests green.
- **Wave 8 (Week 8)** — `LiveRunner.run_daily_cycle()` walks PRD §4.2 end-to-end (signal → delta orders → submit → reconcile → persist), with `CycleScheduler` (APScheduler) and `DiscordNotifier`. Dry-run CLI runs in 1.7s (budget 10s). 3-day paper cycle against Postgres shows coherent state (no dup orders, no ghost positions, broker↔DB parity). 237/237 tests green.
- **Wave 9 (Week 9)** — paper-deployment infra: `python -m quant.live.scheduler --broker alpaca-paper --persist` is the 5-day daemon; `make paper-run` / `make paper-dry` / `make review` wrap it. `scripts/review.py` prints equity curve + positions + orders + signals from Postgres. 5-day simulated cycle test proves zero errors and drift convergence. `docs/journal.md` is the per-day log. 239/239 tests green. **Operational handoff:** user runs `make paper-run` for 5 trading days and fills in `docs/journal.md` before Wave 10.
- **Wave 10 (Week 10)** — `MomentumSignal` (6mo rank, top-3, monthly rebalance) + `portfolio.combine_weights`. Momentum passes Wave 6 validation (OOS Sharpe +0.77, DSR PSR 0.85, deflated excess +0.34). Combined trend+momentum (4/7, 3/7) Sharpe 0.730 > both alone but 1.094x best-single misses the 1.10 target by 0.6pp; correlation 0.66 misses the <0.5 target. Both shortfalls flagged for Week 13 research sprint — the mechanism is tested and correct; the structural decorrelation is what regime overlay (W15) + vol targeting (W16) address. 262/262 tests green.
- **Wave 11 (Week 11)** — `MeanReversionSignal` (IBS + RSI-2 entry, IBS exit; daily cadence; state-change emissions only). Passes WF+DSR at Alpaca costs: OOS Sharpe +0.60, DSR PSR 0.74. All-period corr vs trend **0.27** (stress 0.20, calm 0.45). **3-strategy combined Sharpe 0.828, maxDD -13.60% — 1.185x best-single, clears the 1.10 target previously missed by trend+momentum alone.** Literal "negative correlation during calm periods" unmet (long-only → both long-biased); flagged as PRD-impossible without shorts. 280/280 tests green.
- **Wave 12 (Week 12)** — `RiskValidator` (PRD §6.1 hard limits: order size, position size, price deviation) + `DrawdownTracker` (daily loss + rolling monthly DD) + `Killswitch` (file sentinel). Pre-trade hooks wired into `OrderManager`; kill-switch engaged → `LiveRunner._flatten_cycle` flattens within one cycle. **100% coverage on `src/quant/risk/` + `src/quant/execution/`**. Hypothesis property test (10,000 random orders) confirms no false accepts/rejects. 343/343 tests green. Gate 2 code-side prep complete.
- **Wave 13 (Week 13)** — research sprint. `scripts/research_sprint.py` produces full evaluation (backtest + WF+DSR + stress + regime + correlations + combined) in ~15s. **Gate 2: PASS — all 3 strategies survive.** Trend OOS Sharpe +0.87, momentum +0.80, mean-rev +0.60; all DSR PSR ≥ 0.74; all earn alpha across 3 vol regimes; no strategy blows up in any stress window (worst: trend -2.44 Sharpe in 2022, -11.5% realized DD, under the -15% cap). Combined Sharpe 0.828, maxDD -13.60%. Momentum's standalone -35% DD flagged for Wave 15/16 overlays. Config unchanged; `docs/research/week13_validation.md` captures the full decision. 343/343 tests green.
- **Wave 14 (Week 14)** — generic strategy validator (`scripts/validate_new_strategy.py`) produces per-strategy markdown reports with PASS/FAIL exit codes. CI gate (`scripts/check_strategy_artifacts.py` + new CI job) blocks PRs that touch `src/quant/signals/` without updating `docs/strategies/`. Acceptance verified: deliberately bad inverse-momentum strategy → exit 1 + FAIL verdict; signal-only diff → CI gate exit 1. Fresh validation artifacts for all 3 surviving strategies generated. 349/349 tests green.
- **Wave 15 (Week 15)** — `RegimeHMM` 3-state Gaussian HMM on SPY weekly features (log-return, 5w vol, 5w/20w vol ratio). State-label stabilisation: highest-vol=stress, lowest=calm. `regime_multiplier`/`regime_weighted_multiplier` + `apply_regime_overlay` in `portfolio/sizing.py`. `scripts/train_regime.py` weekly retrain → `data/models/regime_latest.joblib`. **Known-state recovery 80%+ on synthetic (plan acceptance). Trend-only acceptance: overlay reduces max DD by 23.7% (target ≥20%), CAGR -7.6% (target <15%), Sharpe 0.749→0.774, Calmar 0.35→0.42.** Real-data stress flags exactly 2008/2009/2020 weeks — no false positives. Combined-portfolio overlay misses acceptance because max DD (2015-16) is in neutral-vol regime; flagged for Wave 16 vol-target composition. 373/373 tests green.
- **Wave 16 (Week 16)** — `EWMAVolForecaster` (RiskMetrics λ=0.94, stateful + batch) + `vol_target_multiplier`. Combined overlay composes multiplicatively with Wave-15 regime. **Acceptance cleared**: combined 3-strategy baseline already runs at 9.14% realized vol (8.6% dev from 10% target — passes "within 20%"). Vol-target overlay applied on momentum alone cuts max DD in half (-35% → -17.5%), lands vol on target (5.6% dev), Sharpe up 0.69 → 0.80, Calmar +63%. 388/388 tests green. **Sophistication phase complete; Phase 5 production deployment next.**
- **Wave 17 (Week 17)** — production VPS deployment kit. `deploy/systemd/` has three hardened units: `quant-runner.service` (oneshot cycle, `NoNewPrivileges`/`ProtectSystem=strict`/etc), `quant-runner.timer` (`Mon..Fri 15:45 America/New_York`, `Persistent=true`), and `quant-scheduler.service` (alternative daemon mode). `deploy/bootstrap.sh` is one-command idempotent Ubuntu 24.04 setup (UFW, fail2ban, unattended-upgrades, Postgres native, `uv`, clone, migrations, systemd install). `deploy/README.md` is the operator runbook. 24 guardrail tests enforce syntax + hardening + runner-bootstrap consistency. 412/412 tests green. **Operational handoff to user:** cold-boot Hetzner, run bootstrap, smoke-test — target &lt;30min per plan acceptance.
- **Wave 18 (Week 18)** — observability stack. `quant.monitoring.metrics` exports 12 Prometheus metrics wired into `LiveRunner` cycle success/error paths. `quant.monitoring.sentry` + `DiscordNotifier.alert(severity, ...)` for exception routing. `deploy/prometheus/` has a Docker-Compose stack (Prometheus 3.1 + Alertmanager 0.28 + Grafana 11.4) with provisioned datasource + dashboard JSON (equity curve, position values, daily return, rolling Sharpe, cycle duration p50/p95, error counts, killswitch). Alert rules cover all 7 PRD §6.3 rows (test enforces coverage by `prd_row` label). 431/431 tests green. **Operational handoff:** VPS-side `docker compose up -d` in `deploy/prometheus/`, start the 30-day paper qualifier Monday of week 18 (feeds Gate 3 at Wave 19).
- **Wave 19 (Week 19)** — Gate 3 pre-live infra. `scripts/paper_vs_backtest.py` computes 30-day tracking error (paper-vs-backtest Sharpe) with exit 0/1 gating on 50% threshold. `LiveRunner._emit_rolling_sharpe` closes the Wave-18 follow-up (Prometheus rolling-30d-Sharpe gauge now written each cycle). `docs/disaster_recovery.md` runbook covers 3 scenarios with <1h RTO target. `docs/go_live_checklist.md` is the printable sign-off sheet for Gate 3 (tracking error, alerts, DR drill, kill-switch drill, KYC, observability sanity, codebase sanity, gates history). 444/444 tests green. **Operational handoff:** after 30-day paper qualifier ends, run paper-vs-backtest CLI + DR drill + kill-switch drill, complete checklist, print + sign before Wave 20.
- **Wave 20 (Week 20)** — go-live infra. Runner CLI gained `--broker alpaca-live` (real money); Settings live-env guard enforces `quant_env=live + paper_mode=false`. `scripts/preflight_live.py` runs 7 gates (env tags, creds, live URL, account equity, killswitch clear, recent paper PnL, config parses) with exit 0/1; invoked by `quant-runner-live.service`'s ExecStartPre so a mis-configured .env literally prevents the live cycle from firing. `deploy/systemd/quant-runner-live.service` declares `Conflicts=quant-runner.service` for mutual exclusion. Makefile: `preflight-live` / `live-dry` / `live-run` / `live-switch` / `paper-switch`. Docs: `day_one_retrospective.md`, `scaling_plan.md` (10% → 50% W22 → 100% W26 with scale-UP and scale-DOWN triggers), `gate4_checklist.md` (week-21 review). 466/466 tests green. **V1 build complete.**

### In progress
- _none_

### Gate 1 — CLEARED (Week 6)
Trend strategy passes Deflated Sharpe > 0 (deflated excess +0.19) and walk-forward OOS Sharpe ≥ 0.4 (+0.60 concatenated across 6 rolling folds). Adversarial overfit variant is rejected by the DSR harness with exit 1.

### Gate 2 — CLEARED (Week 13)
All 3 strategies survive: OOS Sharpes +0.87 / +0.80 / +0.60, DSR PSRs ≥ 0.74, positive Sharpe in every vol regime, no stress-window blow-ups. Combined 3-strategy Sharpe 0.828, maxDD -13.60%. Full decision in `docs/research/week13_validation.md`.

### Gate 3 — code complete, operational sign-off pending (end of Week 19)
Tooling delivered: `scripts/paper_vs_backtest.py` computes 30-day tracking error with exit 0/1 on the 50% threshold. `docs/go_live_checklist.md` is the printable sign-off sheet. User's 30-day paper qualifier + DR drill + kill-switch drill + KYC are the remaining operational items.

### Gate 4 — code complete, operational sign-off pending (Week 20 + 1 week live)
`docs/gate4_checklist.md` enumerates the 6 review sections (cycle completions, no manual interventions, alert review, tracking error smell-test, DR drill on live DB, Grafana audit). Hold at 10% through week 22, then evaluate 50% step-up per `docs/scaling_plan.md`.

## Tech stack (don't change without updating PRD)

- Python 3.12 (not 3.13 — wheel lag)
- `uv` for package management
- `pandas` 2.2+, `numpy` 2.x, `polars` 1.x (surgical), `pyarrow`
- `vectorbt` for backtesting
- `pandas-ta` for indicators
- `hmmlearn` for regime classification
- `alpaca-py` for broker, `yfinance` for research-only data
- `pydantic` 2.9+ with `pydantic-settings`
- `psycopg` 3.x + `sqlalchemy` 2.0 + `alembic`, Postgres 16 + TimescaleDB
- `APScheduler` + `systemd` timers
- `loguru`, `prometheus-client`, `sentry-sdk`
- `ruff` 0.8+, `mypy` 1.11+, `pytest` 8.x, `hypothesis`
- `discord-webhook` for alerts
- Hetzner CX22 VPS for prod

**Explicitly banned** (see PRD §3.3): backtrader, zipline, FinRL, pandas-datareader, robin_stocks, QSTrader, Airflow, Celery, Prophet, Dask, unofficial broker SDKs, Tensorflow/PyTorch for direct price prediction.

## Repo layout

```
src/quant/
├── data/          # loaders, cache, pipeline
├── features/      # technical, cross_sectional, regime
├── signals/       # trend, momentum, mean_reversion
├── models/        # hmm_regime, volatility
├── portfolio/     # sizing, combiner, rebalancer
├── execution/     # broker_base, alpaca_broker, paper_broker, order_manager
├── risk/          # limits, drawdown, killswitch
├── backtest/      # engine, walk_forward, deflated_sharpe
├── live/          # scheduler, runner
├── monitoring/    # metrics, alerts
└── storage/       # db, repos
```

## Quality bars

| Module | Coverage | Type check |
|---|---|---|
| `src/quant/risk/` | 100% | mypy `--strict` |
| `src/quant/execution/` | 100% | mypy `--strict` |
| `src/quant/portfolio/sizing.py` | 100% | mypy `--strict` |
| `src/quant/signals/` | >80% | mypy default |
| `src/quant/features/` | >80% | mypy default |
| `src/quant/backtest/` | best effort | mypy default |

Property tests (hypothesis) are mandatory for `risk/`: 10,000 random orders, no valid order rejected, no invalid order accepted.

## Commands

```bash
# Setup
uv sync

# Dev loop
make test            # pytest
make lint            # ruff check + ruff format --check
make format          # ruff check --fix + ruff format
make typecheck       # mypy strict on risk/execution/portfolio
make up / make down  # docker compose postgres

# Backtest (after Week 5)
uv run python scripts/run_backtest.py --strategy trend --start 2003-01-01

# Paper run (after Week 8)
uv run python -m quant.live.runner --mode paper --dry-run
```

## Conventions

- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- Feature branches, PR + self-review even solo. `main` is always deployable.
- No direct commits to `main`.
- Every wave ends with a passing CI build.
- Config drift (live config hash != last-deployed hash) → refuse to start.

## Risk invariants (never violate)

- `src/quant/risk/` limits cannot be disabled by config — they are code-enforced.
- Killswitch file `/var/run/quant/HALT` is checked every tick. If present → flatten all, halt.
- Hard limits (PRD §6.1):
  - Max single position 30% equity
  - Max daily loss -5% → flatten + halt 24h
  - Max monthly DD -15% → flatten + manual restart
  - Order size >20% equity → reject unless override
  - Price deviation >1% from last quote → reject
- Never commit secrets. `.env` is gitignored; `.env.example` lists required vars.
