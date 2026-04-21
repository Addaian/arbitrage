# Product Requirements Document
## Quant Trading System — Solo Developer Edition

**Version:** 1.0
**Date:** April 21, 2026
**Owner:** Solo developer
**Status:** Draft — pre-implementation

---

## 1. Executive Summary

### 1.1 Product vision

A production-grade automated systematic trading system that runs a diversified portfolio of rule-based and lightly-ML-enhanced strategies on liquid US ETFs and crypto, targeting consistent positive risk-adjusted returns (live Sharpe 0.7-1.0) with strict drawdown controls. The system is designed to scale from $1,500 to $500k+ of capital with no architectural changes.

### 1.2 Success criteria

The project is successful if, 12 months after starting live deployment:

- Live Sharpe ratio ≥ 0.5 over 12 months (backtest was 1.0-1.3, so 50% haircut is expected)
- Max drawdown stays under 20%
- Zero production incidents requiring manual intervention more than once per month
- Codebase is tested, documented, deployable by a fresh clone + one command
- Paper-to-live performance tracking is within 30% on Sharpe over 90-day rolling window

**Non-goals:** beating SPY, hitting a specific dollar amount, being market-neutral, running intraday strategies, trading single names.

### 1.3 Principles (non-negotiable)

1. **Boring beats clever.** Rule-based signals preferred over ML unless ML demonstrably outperforms rules in walk-forward.
2. **Same code path everywhere.** Backtest, paper, and live run identical logic behind a broker interface.
3. **Risk limits are law.** Kill switches cannot be bypassed in live code. Ever.
4. **Test the risk layer.** Signal code can have bugs; risk code cannot.
5. **Ship vertical slices.** End-to-end working pipeline on day one, add strategies weekly.
6. **Write for your future self at 2am on a Sunday when something broke.**

---

## 2. Scope

### 2.1 In scope (V1)

- Daily-bar ETF trend following and cross-sectional momentum
- Daily-bar mean reversion overlay
- HMM-based regime classification
- Volatility targeting at portfolio level
- Walk-forward backtesting and deflated Sharpe evaluation
- Alpaca paper and live execution
- Postgres state persistence
- Discord alerting
- Systemd deployment on a single VPS

### 2.2 Out of scope (V1)

- Intraday strategies (minute-bar or below)
- Options strategies (deferred to V2)
- Futures/commodities (deferred to V3)
- Single-stock portfolios
- Multi-broker execution
- Web dashboard/UI
- Multi-user support
- Reinforcement learning
- Deep learning models (LSTM, Transformer)
- Real-time tick data
- Co-location or latency optimization

### 2.3 V2 roadmap (month 7-12)

- SPX vertical credit spreads via Tastytrade API
- Crypto momentum sleeve via Coinbase
- IBKR integration for Roth IRA deployment
- Meta-labeling with XGBoost
- Web dashboard (FastAPI + HTMX)

---

## 3. Technical Stack

### 3.1 Language and runtime

**Python 3.12** (not 3.13 — some numba and scikit-learn wheels still lag as of April 2026, and 3.12 is what production libraries target).

**Why not Rust/Go:** the system processes daily bars. Latency is irrelevant. Python's data ecosystem is the moat. If a component hits performance limits, rewrite that component in Rust via PyO3, don't rewrite the system.

### 3.2 Core dependencies (encouraged)

**Package management:**
- **`uv`** (Astral) — the 2026 standard. 10-100x faster than pip/poetry. Lock files are reproducible. Use it.
- Avoid poetry (slow resolution), pipenv (unmaintained), bare pip (no lock).

**Data and computation:**
- **`pandas`** 2.2+ — ecosystem standard, non-negotiable
- **`numpy`** 2.x
- **`polars`** 1.x — use for data pipelines where pandas is slow (loading years of bars). Do NOT rewrite everything in polars; use it surgically.
- **`numba`** 0.60+ — for hot loops if profiling shows need
- **`pyarrow`** — Parquet I/O, required for caching layer

**Financial libraries:**
- **`vectorbt`** (open-source version) — primary backtesting engine. Fast, NumPy-native, well-suited to portfolio-level strategies.
- **`pandas-ta`** — technical indicators. Active, maintained, pandas-native.
- **`scipy.stats`** — distributions, statistical tests
- **`statsmodels`** — for cointegration tests, GARCH, regression diagnostics

**Machine learning:**
- **`scikit-learn`** 1.5+ — always the first reach
- **`hmmlearn`** — for regime classification
- **`xgboost`** 2.x — gradient boosting
- **`lightgbm`** — alternative to XGBoost, sometimes faster

**Broker and data APIs:**
- **`alpaca-py`** (official) — primary broker SDK, Level 3 options approved
- **`yfinance`** — research-only, EOD data, not for live
- **`ccxt`** — unified crypto exchange interface (V2)
- **`ib_async`** — IBKR integration (V2, for Roth IRA)

**Configuration and validation:**
- **`pydantic`** 2.9+ — config validation, data models, type safety
- **`pydantic-settings`** — environment variable management
- **`python-dotenv`** — local dev secrets
- **`PyYAML`** — config files

**Web and networking:**
- **`httpx`** — HTTP client, replaces requests
- **`websockets`** — if streaming data needed (V2)
- **`tenacity`** — retry logic for broker calls

**Database:**
- **`psycopg`** 3.x (not psycopg2) — Postgres driver
- **`sqlalchemy`** 2.0+ — ORM for complex queries; raw SQL for hot paths
- **`alembic`** — migrations

**Storage and caching:**
- **Parquet via pyarrow** — all historical bar data
- **Postgres + TimescaleDB extension** — state, trades, metrics
- Avoid Redis at V1 — unnecessary complexity

**Scheduling and orchestration:**
- **`APScheduler`** 3.x — in-process scheduling
- **systemd timers** — OS-level scheduling as backup/primary
- Avoid Airflow, Prefect, Dagster at V1 — overkill for a single VPS

**Logging and monitoring:**
- **`loguru`** — replaces stdlib logging, zero-config
- **`prometheus-client`** — metrics export
- **Prometheus + Grafana** — self-hosted monitoring
- **`sentry-sdk`** — exception tracking (free tier, 5k events/mo)

**Development tools:**
- **`ruff`** 0.8+ — replaces black, isort, flake8, pylint. One tool.
- **`mypy`** 1.11+ — type checking, strict mode on `risk/`, `execution/`, `portfolio/`
- **`pytest`** 8.x — testing
- **`hypothesis`** — property-based testing for risk logic
- **`pytest-cov`** — coverage reporting
- **`pre-commit`** — git hooks

**Notifications:**
- **`discord-webhook`** — simple Discord alerts
- **`python-telegram-bot`** — if you prefer Telegram (more features)

### 3.3 Packages to avoid (with reasons)

**Backtesting:**
- **`backtrader`** — abandoned by original author in 2020. Community fork "backtrader2" is bugfix-only. Use vectorbt.
- **`zipline`** (original) — Quantopian shut down 2020. Only **zipline-reloaded** (Stefan Jansen) is alive; acceptable but heavy for this project.
- **`bt`** — minimal, but vectorbt is strictly better.
- **`backtesting.py`** — fine for toy single-asset, doesn't scale to portfolios.

**"AI trading" packages (all of these):**
- **`FinRL`** — academic RL toy. Will not produce live profits.
- **`stable-baselines3` for trading** — same problem. RL needs state spaces trading doesn't cleanly provide.
- **Anything marketed as "AI trading bot"** on PyPI — scam vector or abandonware.

**Data libraries:**
- **`pandas-datareader`** — maintained but every backend is broken by API changes within months
- **`alpha-vantage`** — rate-limited into uselessness at free tier
- **`iexfinance`** — IEX Cloud shut down Aug 2024
- **`quandl`** — acquired by Nasdaq, now Nasdaq Data Link, data moved behind paywall

**Broker SDKs:**
- **`robin_stocks`**, **`pyrobinhood`** — unofficial Robinhood. Violates ToS, breaks regularly.
- **`webull`** (unofficial) — same issue.
- Any unofficial Schwab/Fidelity library.

**"Quant frameworks":**
- **QSTrader** — well-intentioned but unmaintained
- **PyAlgoTrade** — unmaintained since 2018
- **Catalyst** (Enigma) — dead

**Task queues and orchestration:**
- **Celery** — massive overkill for single-VPS daily system
- **Airflow** — operationally heavy, not designed for this
- **Dask** — if you need it, your design is wrong

**Deep learning for price prediction:**
- **TensorFlow/PyTorch for direct price prediction** — published academic results don't replicate. If you want to use NN, use it for feature learning feeding a downstream linear model.
- **Prophet** — not designed for financial time series, produces misleading confidence intervals

**Random ML libraries:**
- **`sktime`** — ambitious but young; stick with sklearn for now
- **`tsai`** — same
- **`darts`** — same

### 3.4 Infrastructure stack

**Development machine:**
- macOS or Linux (Windows works but WSL2 required)
- Python 3.12 via `uv python install 3.12`
- Docker Desktop for local Postgres

**Production VPS:**
- **Hetzner CX22** (€4.51/mo) — 2 vCPU ARM, 4 GB RAM, 40 GB SSD, EU
  - Alternative: **Hetzner CPX21** ($8.09/mo) — 3 vCPU AMD x86, 4 GB RAM, 80 GB SSD, US
  - Alternative: **Oracle Cloud Free Tier** — 4 vCPU ARM, 24 GB RAM, free forever (but occasionally reclaimed)
- Ubuntu 24.04 LTS
- UFW firewall, SSH key-only, fail2ban
- Deployment: `git pull && systemctl restart quant.service`

**Avoid:**
- AWS/GCP for V1 — 5-10x the cost for no benefit
- Kubernetes — absurd at this scale
- Docker Swarm — same
- Managed Postgres (RDS) — expensive vs self-hosted

### 3.5 External services

**Required (free tier):**
- GitHub — private repo
- Alpaca — paper + live broker
- Sentry — 5k errors/month free
- Discord — webhooks free

**Optional:**
- Polygon.io — $29/mo Starter if you need more than 5 years of daily data
- UptimeRobot — free uptime monitoring

**Avoid at V1:**
- Grafana Cloud (self-host instead)
- DataDog (expensive)
- Any "algo trading signal" subscription

---

## 4. Architecture

### 4.1 Component overview

```
┌─────────────────────────────────────────────────────┐
│                    SCHEDULER                        │
│              (APScheduler + systemd)                │
└──────────────────────┬──────────────────────────────┘
                       │
         ┌─────────────┴──────────────┐
         ↓                            ↓
┌──────────────────┐         ┌────────────────┐
│  DATA PIPELINE   │         │  LIVE RUNNER   │
│  (nightly)       │         │  (daily, EOD)  │
└─────────┬────────┘         └────────┬───────┘
          │                           │
          ↓                           ↓
┌──────────────────┐         ┌────────────────┐
│  PARQUET CACHE   │←────────│  SIGNAL ENGINE │
│  (historical)    │         └────────┬───────┘
└──────────────────┘                  │
                                      ↓
                            ┌────────────────┐
                            │  RISK LAYER    │
                            │  (validators)  │
                            └────────┬───────┘
                                     │
                                     ↓
                            ┌────────────────┐
                            │ BROKER ADAPTER │ ──→ Alpaca API
                            └────────┬───────┘
                                     │
                                     ↓
                            ┌────────────────┐
                            │   POSTGRES     │
                            │  (state/log)   │
                            └────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────┐
         ↓                           ↓                   ↓
    ┌─────────┐              ┌────────────┐      ┌──────────────┐
    │ METRICS │ ──→ Grafana  │  DISCORD   │      │    SENTRY    │
    └─────────┘              └────────────┘      └──────────────┘
```

### 4.2 Data flow (daily cycle)

**T-1 evening (nightly batch, 2am UTC):**
1. Fetch fresh EOD bars for universe via yfinance + Alpaca
2. Validate, adjust for splits/dividends, store to Parquet cache
3. Run feature engineering
4. Retrain HMM regime model (weekly; skip other days)
5. Generate signals for each strategy
6. Compute target positions under vol targeting
7. Store target positions in Postgres
8. Health check, Discord "ready" notification

**T market-close + 15 min (3:45pm ET on trading days):**
1. Read target positions from Postgres
2. Fetch current account state from broker
3. Compute delta orders (target - current)
4. Run pre-trade risk validation (kill switch, limits, sanity)
5. If validation passes, submit orders to broker (MOC or limit)
6. Poll order status until filled or timeout (5 min)
7. Reconcile fills with targets
8. Update Postgres with trade log
9. Compute P&L, update metrics, Discord summary

**Continuous:**
- Prometheus scrape every 15 seconds
- Sentry captures any unhandled exception
- Heartbeat to Discord every 6 hours

### 4.3 Broker abstraction

```python
# src/quant/execution/broker_base.py
from abc import ABC, abstractmethod

class Broker(ABC):
    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def submit_order(self, order: Order) -> OrderResult: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...
```

Three implementations: `AlpacaBroker`, `PaperBroker` (simulator matching Alpaca API), `BacktestBroker` (vectorbt integration). The same `LiveRunner` calls `broker.submit_order()` regardless — this is how paper and live stay truthful.

### 4.4 Config schema (Pydantic)

All configuration in YAML, validated on load through Pydantic models. If a config file is malformed, the system refuses to start rather than running with defaults.

```python
class StrategyConfig(BaseModel):
    name: str
    enabled: bool
    universe: list[str]
    weight: float  # 0-1, sums to 1 across all strategies
    params: dict[str, Any]

class RiskConfig(BaseModel):
    max_position_pct: float = Field(le=0.30)
    max_daily_loss_pct: float = Field(le=0.05)
    max_monthly_drawdown_pct: float = Field(le=0.15)
    target_annual_vol: float = Field(ge=0.05, le=0.25)
    killswitch_file: Path = Path("/var/run/quant/HALT")

class BrokerConfig(BaseModel):
    provider: Literal["alpaca", "paper", "backtest"]
    paper_mode: bool
    api_key: SecretStr
    api_secret: SecretStr
    base_url: HttpUrl
```

---

## 5. Strategy Specifications

### 5.1 Strategy 1: Equity Trend Following

**Signal:** Long SPY/QQQ/EFA when 10-month SMA is positive, cash (SGOV) otherwise.
**Rebalance:** Monthly, first trading day.
**Weight:** 40% of portfolio.
**Expected Sharpe:** 0.4-0.6 live.

### 5.2 Strategy 2: Cross-Sectional ETF Momentum

**Signal:** Rank a universe of 10 ETFs (SPY, QQQ, EFA, EEM, GLD, IEF, TLT, VNQ, DBC, XLE) by 6-month total return, hold top 3 equal-weighted.
**Rebalance:** Monthly.
**Weight:** 30% of portfolio.
**Expected Sharpe:** 0.3-0.5 live.

### 5.3 Strategy 3: Mean Reversion Overlay

**Signal:** On the same 10-ETF universe, buy at close any ETF with IBS < 0.2 and RSI-2 < 10, exit next day or when IBS > 0.7.
**Rebalance:** Daily.
**Weight:** 15% of portfolio.
**Expected Sharpe:** 0.2-0.4 live, low correlation to #1 and #2.

### 5.4 Strategy 4: HMM Regime Overlay

**Not a standalone strategy** — a multiplier on the others. HMM trained on weekly returns + VIX + term structure, outputs probability of "stress" regime. Position sizes scaled by (1 - stress_prob).

### 5.5 Strategy 5: Volatility Targeting

**Not a standalone strategy** — portfolio-level. Forecast 21-day realized vol via EWMA (λ=0.94), scale overall portfolio exposure to hit 10% annual vol target, capped at 100% gross exposure.

### 5.6 Validation requirements before going live

Each strategy must pass:
- Full historical backtest (2003-present) with vectorbt
- Walk-forward validation: 10-year train / 2-year test, rolling
- Deflated Sharpe Ratio > 0 after accounting for parameter trials
- Stress test equity curve through 2008, 2020, 2022
- 30 days paper trading with tracking error to backtest < 30%

---

## 6. Risk Management Requirements

### 6.1 Hard limits (enforced in code, cannot be disabled)

| Limit | Threshold | Action |
|---|---|---|
| Single position size | 30% of equity | Reject order |
| Daily portfolio loss | -5% | Flatten all, halt 24h |
| Monthly drawdown | -15% | Flatten all, halt until manual restart |
| Order size sanity | 20% of equity in single order | Reject, require override |
| Price deviation | Order >1% from last quote | Reject, re-price |
| Config drift | Live config != last-deployed hash | Refuse to start |

### 6.2 Killswitch

A file sentinel at `/var/run/quant/HALT` checked at every tick. If present, all open orders cancelled, all positions flattened at market, system halts until file removed. Accessible via SSH one-liner: `touch /var/run/quant/HALT`.

Also triggered by Discord `/halt` command via a separate always-on bot process.

### 6.3 Monitoring alerts (Discord)

| Condition | Severity |
|---|---|
| Order rejected by broker | Warning |
| Daily loss > -3% | Warning |
| Daily loss > -5% (killswitch) | Critical |
| Paper vs live tracking error > 50% over 30 days | Warning |
| No heartbeat for 30 minutes | Critical |
| Unhandled exception | Critical (via Sentry) |
| NTP clock drift > 500ms | Critical (signed orders will fail) |

---

## 7. Development Workflow

### 7.1 Repo structure

```
quant-system/
├── pyproject.toml
├── uv.lock
├── ruff.toml
├── mypy.ini
├── .pre-commit-config.yaml
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── README.md
├── PRD.md
├── implementationplan.md
│
├── config/
│   ├── strategies.yaml
│   ├── universe.yaml
│   └── risk.yaml
│
├── src/quant/
│   ├── __init__.py
│   ├── config.py                # Pydantic settings
│   ├── types.py                 # shared dataclasses
│   ├── data/
│   │   ├── loaders.py
│   │   ├── cache.py
│   │   └── pipeline.py
│   ├── features/
│   │   ├── technical.py
│   │   ├── cross_sectional.py
│   │   └── regime.py
│   ├── signals/
│   │   ├── trend.py
│   │   ├── momentum.py
│   │   └── mean_reversion.py
│   ├── models/
│   │   ├── base.py
│   │   ├── hmm_regime.py
│   │   └── volatility.py
│   ├── portfolio/
│   │   ├── sizing.py
│   │   ├── combiner.py
│   │   └── rebalancer.py
│   ├── execution/
│   │   ├── broker_base.py
│   │   ├── alpaca_broker.py
│   │   ├── paper_broker.py
│   │   └── order_manager.py
│   ├── risk/
│   │   ├── limits.py
│   │   ├── drawdown.py
│   │   └── killswitch.py
│   ├── backtest/
│   │   ├── engine.py
│   │   ├── walk_forward.py
│   │   └── deflated_sharpe.py
│   ├── live/
│   │   ├── scheduler.py
│   │   └── runner.py
│   └── monitoring/
│       ├── metrics.py
│       └── alerts.py
│
├── notebooks/
├── scripts/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── deploy/
│   ├── systemd/
│   │   ├── quant-scheduler.service
│   │   └── quant-runner.service
│   └── prometheus/
│
└── .github/workflows/
    ├── ci.yml
    └── deploy.yml
```

### 7.2 Branching and commits

- `main` is deployable, always. Live production pulls from `main`.
- Feature branches: `feat/hmm-regime`, `fix/order-reconciliation`
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- No direct commits to `main`; PR + self-review even as solo dev

### 7.3 Testing requirements

**Must have 100% test coverage:**
- `src/quant/risk/` — every limit, every edge case
- `src/quant/execution/` — every order path, every failure mode
- `src/quant/portfolio/sizing.py` — math must be exact

**Must have >80% coverage:**
- `src/quant/signals/`
- `src/quant/features/`

**Can have lower coverage:**
- `src/quant/backtest/` — complex, hard to unit test
- Notebooks — not tested, not in `src/`

**Property tests (hypothesis):** risk logic must never reject a valid order or accept an invalid one across 10,000 generated inputs.

### 7.4 CI pipeline (GitHub Actions)

On every push:
1. `uv sync`
2. `ruff check .`
3. `mypy src/quant/risk src/quant/execution src/quant/portfolio --strict`
4. `pytest tests/ -v --cov=src/quant --cov-fail-under=80`
5. `pytest tests/integration/ --slow` (nightly only)

### 7.5 Deployment

```bash
# On VPS
cd /opt/quant
git pull origin main
uv sync
sudo systemctl restart quant-scheduler.service
sudo systemctl restart quant-runner.service
```

Systemd unit files handle restarts, logging, resource limits.

---

## 8. Milestone Plan (20 weeks)

| Weeks | Milestone | Deliverable |
|---|---|---|
| 1-2 | Project scaffold | uv project, Alpaca paper account, Postgres Docker, CI running |
| 3-4 | Data pipeline | Nightly EOD ingest, Parquet cache, 20-year backfill |
| 5-6 | First strategy (trend) | Antonacci GEM in vectorbt, backtest report |
| 7 | Broker abstraction | AlpacaBroker + PaperBroker, same interface |
| 8-9 | Live runner (paper) | Strategy 1 running paper, Discord alerts working |
| 10-11 | Strategies 2 & 3 | CS momentum + mean reversion added |
| 12 | Risk layer | All hard limits implemented, property tests passing |
| 13-14 | Walk-forward + DSR | Validation harness, eliminates overfit strategies |
| 15 | HMM regime | Trained model, position size multiplier |
| 16 | Vol targeting | Portfolio-level vol scaling |
| 17-18 | Production deployment | VPS, systemd, Prometheus+Grafana, 30-day paper run |
| 19 | Go-live prep | Small capital live, 10% of target, monitor daily |
| 20 | Go-live | Scale to target capital conditional on tracking |

---

## 9. Definition of Done

A feature is "done" when:

1. Code reviewed (even if self-review)
2. Unit tests written and passing
3. Integration test added if it touches the risk or broker layer
4. Type-checked with mypy in strict mode (for risk/exec/portfolio)
5. Ruff clean
6. Documented in the relevant `docs/` markdown file
7. Config schema updated if params changed
8. Runbook updated if operational behavior changed
9. Changelog entry added
10. Deployed to paper and observed for at least 5 trading days before promotion to live

---

## 10. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Backtest overfits, live underperforms | High | High | Deflated Sharpe, walk-forward, 50% haircut budget |
| VPS outage during rebalance | Low | Medium | APScheduler + systemd redundancy, next-day catch-up logic |
| Alpaca API degradation | Medium | High | Retry + exponential backoff, halt on persistent failure |
| Bug in risk layer causes oversized position | Low | Catastrophic | Property tests, hard limits at broker level, manual daily check |
| Config drift between envs | Medium | Medium | Config hash validation, refuse to start on mismatch |
| Data provider changes schema | Medium | Medium | Validation layer, fail loud, no silent fallback |
| Scope creep (adding strategies mid-build) | High | Medium | This PRD is immutable until V1 ships |
| Solo burnout at week 12 | Medium | High | 20-week plan assumes sustainable pace; skip features, not testing |

---

## 11. Open questions (to resolve before starting)

1. **Tax account**: taxable Alpaca or Roth IRA via IBKR? (Roth requires IBKR integration which is V2, so V1 starts taxable.)
2. **Capital deployment schedule**: 10% at week 19, 50% at week 22, 100% at week 26 — conditional on tracking error.
3. **Content/public repo**: keep private, or blog the build? (Public helps career, private removes pressure. Default: private during build, open-source-friendly after 12 months live.)

---

## 12. Appendix: Why this stack, one line each

- **Python 3.12**: ecosystem moat, 3.13 still lagging some wheels
- **uv**: 10-100x faster than poetry, lock files work
- **pandas + polars**: industry standard + speed where needed
- **vectorbt**: fastest Python backtester, numpy-native, portfolio-ready
- **Alpaca**: free, API-first, fractional shares via API, Level 3 options
- **Postgres + TimescaleDB**: battle-tested, cheap to self-host, time-series friendly
- **Pydantic**: validation at boundaries prevents 80% of production bugs
- **loguru**: zero-config logging that just works
- **Hetzner**: cheapest reliable VPS provider in 2026
- **Prometheus + Grafana**: self-hosted, free, the observability standard
- **Discord webhooks**: free, phone-push, no infra required
- **systemd**: comes with Ubuntu, no daemon manager needed
- **ruff + mypy**: one linter, one type checker, both fast
- **pytest + hypothesis**: testing + fuzz testing for the risk layer
