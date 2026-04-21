# quant-system

A solo-developer systematic trading system for US ETFs (V1) and crypto (V2). Built in Python, runs on a single VPS, targets consistent positive risk-adjusted returns rather than home runs.

> **Status:** pre-implementation. See [PRD.md](./PRD.md) for the product spec and [implementationplan.md](./implementationplan.md) for the 20-week build plan.

---

## What this is

A portfolio of mechanical trading strategies — trend following, cross-sectional momentum, mean reversion — combined under volatility targeting and regime overlays, executed automatically through Alpaca. The architecture is designed so the same code runs in backtest, paper, and live, and scales from $1,500 to $500k+ of capital without changes.

**What it aims for:**
- Live Sharpe 0.7-1.0 after realistic backtest haircut
- Max drawdown under 20%
- Monthly positive hit rate around 60%
- Fully automated with strict kill switches

**What it's not:**
- A get-rich-quick bot
- A high-frequency or intraday system
- A machine learning black box
- A multi-broker HFT platform

Read [PRD.md §1.2](./PRD.md) for formal success criteria and [§1.3](./PRD.md) for the non-negotiable principles.

---

## Quick links

| Doc | Purpose |
|---|---|
| [PRD.md](./PRD.md) | Full product requirements — scope, architecture, stack, risk framework |
| [implementationplan.md](./implementationplan.md) | Week-by-week build plan with tasks, deliverables, and acceptance criteria |
| `config/` | Strategy, universe, and risk configuration (YAML) |
| `src/quant/` | All production code |
| `notebooks/` | Research and exploration (not production) |
| `tests/` | Unit, integration, and property-based tests |
| `deploy/` | systemd units and Prometheus config |

---

## Tech stack at a glance

**Language:** Python 3.12
**Package manager:** uv
**Backtesting:** vectorbt
**Broker:** Alpaca (paper + live)
**Data:** yfinance (research), Alpaca IEX (live)
**Database:** Postgres 16 + TimescaleDB
**Monitoring:** Prometheus + Grafana (self-hosted)
**Alerting:** Discord webhooks + Sentry
**Deployment:** Hetzner CX22 VPS, Ubuntu 24.04, systemd

Full reasoning for every choice is in [PRD.md §3](./PRD.md).

---

## Getting started

### Prerequisites

- Python 3.12 (via `uv python install 3.12`)
- Docker Desktop (for local Postgres)
- `uv` installed globally: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- An Alpaca paper account (free) — get your keys at https://alpaca.markets
- A Discord server with a webhook (for alerts)

### Local setup

```bash
# Clone
git clone git@github.com:<you>/quant-system.git
cd quant-system

# Install dependencies (fast, reproducible)
uv sync

# Copy secrets template
cp .env.example .env
# Edit .env — add Alpaca keys, Discord webhook, Postgres password

# Start local Postgres + Prometheus + Grafana
docker compose up -d

# Run migrations
uv run alembic upgrade head

# Verify everything works
uv run pytest tests/unit -v
```

### Run your first backtest

```bash
uv run python -m quant.backtest.engine --strategy trend --start 2003-01-01
```

### Run a paper trade (after scaffold is in place)

```bash
# One-shot paper run (simulates today's EOD cycle)
uv run python -m quant.live.runner --mode paper --dry-run

# Scheduled paper run (systemd-style daily loop in foreground)
uv run python -m quant.live.scheduler --mode paper
```

---

## Repository layout

```
quant-system/
├── PRD.md                    # Product requirements
├── implementationplan.md     # 20-week build plan
├── README.md                 # You are here
│
├── pyproject.toml            # Python project + dependencies
├── uv.lock                   # Locked deps (commit this)
├── ruff.toml                 # Linter config
├── mypy.ini                  # Type-checker config
├── .pre-commit-config.yaml   # Git hooks
├── .env.example              # Secrets template
├── docker-compose.yml        # Local services
├── Dockerfile                # Production image
├── Makefile                  # Common tasks
│
├── config/
│   ├── strategies.yaml       # Per-strategy params
│   ├── universe.yaml         # ETF tickers and data sources
│   └── risk.yaml             # Vol targets, drawdown limits
│
├── src/quant/
│   ├── data/                 # Data pipeline: loaders, cache, validation
│   ├── features/             # Technical, cross-sectional, regime features
│   ├── signals/              # Trend, momentum, mean reversion
│   ├── models/               # HMM regime, volatility forecasts
│   ├── portfolio/            # Sizing, combination, rebalancing
│   ├── execution/            # Broker interface + Alpaca/paper adapters
│   ├── risk/                 # Limits, drawdown tracking, kill switch
│   ├── backtest/             # vectorbt engine, walk-forward, DSR
│   ├── live/                 # Scheduler and daily runner
│   └── monitoring/           # Prometheus metrics, Discord alerts
│
├── notebooks/                # Research only — not tested, not in prod
├── scripts/                  # One-shot CLIs: train, backtest, reconcile
├── tests/
│   ├── unit/                 # Per-module tests
│   ├── integration/          # End-to-end paper runs
│   └── fixtures/             # Historical data snapshots for tests
│
├── deploy/
│   ├── systemd/              # .service and .timer units for the VPS
│   └── prometheus/           # Prometheus + Grafana dashboards
│
└── .github/workflows/
    ├── ci.yml                # Lint + type check + tests on every push
    └── deploy.yml            # Manual-trigger deploy to VPS
```

Full component diagram and data flow in [PRD.md §4](./PRD.md).

---

## Development workflow

```bash
# Format + lint
uv run ruff check . --fix
uv run ruff format .

# Type check (strict on risk/execution/portfolio)
uv run mypy src/quant/risk src/quant/execution src/quant/portfolio --strict

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/quant --cov-report=html

# Run a single test file
uv run pytest tests/unit/test_risk_limits.py -v
```

Branching, commit conventions, and CI rules are in [PRD.md §7](./PRD.md).

---

## Running strategies

### Backtest

```bash
# Single strategy, full history
uv run python scripts/run_backtest.py --strategy trend --start 2003-01-01

# Full portfolio (all enabled strategies combined)
uv run python scripts/run_backtest.py --portfolio --start 2003-01-01

# Walk-forward validation
uv run python scripts/run_backtest.py --portfolio --walk-forward \
    --train-years 10 --test-years 2
```

### Paper trading

Paper runs use the same `LiveRunner` as live but route orders through `PaperBroker`. To enable:

```bash
# Set BROKER_PROVIDER=paper in .env
uv run python -m quant.live.scheduler
```

### Live trading (only after 30+ days clean paper)

```bash
# Set BROKER_PROVIDER=alpaca and PAPER_MODE=false in .env
# Scale capital gradually: 10% at week 19, 50% at week 22, 100% at week 26
uv run python -m quant.live.scheduler
```

See [implementationplan.md Week 19-20](./implementationplan.md) for the go-live checklist.

---

## Risk management

**Hard limits** (enforced in code, cannot be disabled):

| Limit | Threshold | Action |
|---|---|---|
| Single position size | 30% of equity | Reject order |
| Daily portfolio loss | -5% | Flatten all, halt 24h |
| Monthly drawdown | -15% | Flatten all, halt until manual restart |
| Order size sanity | 20% of equity | Reject, require override |
| Price deviation | >1% from last quote | Reject, re-price |
| Config drift | Hash mismatch | Refuse to start |

**Kill switches:**

```bash
# Halt immediately via file sentinel (SSH one-liner)
ssh vps "touch /var/run/quant/HALT"

# Halt via Discord (if the killswitch bot is running)
# Type /halt in the configured channel

# Resume after halt
ssh vps "rm /var/run/quant/HALT"
# Then manually restart:
ssh vps "sudo systemctl restart quant-runner.service"
```

Full risk framework in [PRD.md §6](./PRD.md).

---

## Deployment

Production runs on a single Hetzner CX22 VPS (€4.51/mo). Deployment is git-based:

```bash
# First-time setup on VPS
ssh root@<vps>
cd /opt
git clone <repo>
cd quant-system
./deploy/bootstrap.sh   # installs uv, creates systemd units, configures firewall

# Ongoing deploys
ssh root@<vps>
cd /opt/quant-system
git pull origin main
uv sync
sudo systemctl restart quant-scheduler.service
sudo systemctl restart quant-runner.service
```

Full deployment guide in [implementationplan.md Weeks 17-18](./implementationplan.md).

---

## Monitoring

- **Grafana:** `http://<vps>:3000` — equity curve, position dashboard, latency, Sharpe rolling window
- **Prometheus:** `http://<vps>:9090` — raw metrics
- **Discord:** daily EOD summary + real-time alerts for warnings/criticals
- **Sentry:** exception capture (free tier, 5k events/mo)

---

## FAQ

**Q: Will this make me money?**
Probably not in year one, statistically. See [PRD.md §1.2 and §10](./PRD.md) for honest expectations. The architecture is designed so that when it *does* work, it compounds reliably for years. If your goal is fast income, this is the wrong project.

**Q: Why not deep learning / transformers / RL?**
Because published academic results don't replicate in live trading on daily bars. See [PRD.md §3.3](./PRD.md) for the full "packages to avoid" list and reasoning.

**Q: Why Alpaca and not Interactive Brokers?**
Alpaca is the simplest API-first broker with fractional shares exposed through the API and Level 3 options available to small accounts. IBKR is better for Roth IRAs and is planned for V2. See [PRD.md §3.2](./PRD.md).

**Q: Can I fork this and add my own strategies?**
Yes — the strategy interface is clean and documented. Add a module under `src/quant/signals/`, wire it into `config/strategies.yaml`, and run walk-forward validation. See [implementationplan.md Weeks 10-11](./implementationplan.md) for the template.

**Q: Can this run in the cloud (AWS/GCP)?**
Yes, but don't. A $5/mo VPS does exactly the same job as a $50/mo EC2 instance for daily-bar trading. See [PRD.md §3.4](./PRD.md).

---

## License

Private / unlicensed during build. Open-source decision deferred 12 months post-live. See [PRD.md §11](./PRD.md).

---

## Contact

Solo-dev project. No support. No signals. No discord group.
If you find something genuinely broken in the public code, open an issue.
