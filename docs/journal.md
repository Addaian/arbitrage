# Ops journal

Free-form log for anything the system does in paper/live that deviates
from the backtest, or anything that surprises us. Kept per [implementation
plan Week 9](../implementationplan.md): "Document every oddity."

**Write in here any time** a cycle runs and something's off — even if
you're not sure it's a bug. Future-you will thank present-you.

## How to use

- One section per day you observe the system.
- Note: date (ISO), what ran, what was expected, what happened, any
  suspicion of cause.
- If it turns out to be a bug, cross-link the commit/PR that fixed it.
- Keep entries short — bullets beat paragraphs.

## Daily checklist (Wave 9 — first 5 days)

- [ ] Cycle fired at 3:45pm ET (check Discord or `scripts/review.py`)
- [ ] No `cycle error` messages
- [ ] Positions in Alpaca paper match `scripts/review.py` output
- [ ] Equity change == sum of fills × price deltas (within fees/slippage)
- [ ] Target weights from CLI match what the backtest would emit

## Commands

```bash
# Start the daemon (foreground; wrap in nohup/tmux for 5-day run):
make paper-run

# One-shot cycle now (for debugging — still persists to Postgres if --persist):
uv run python -m quant.live.runner --broker alpaca-paper --persist

# Dashboard (read-only):
uv run python scripts/review.py --days 5
```

## Day logs

### Day 0 — pre-deployment

- [ ] `.env` populated with ALPACA_API_KEY, ALPACA_API_SECRET, DISCORD_WEBHOOK_URL
- [ ] `make up` brings up Dockerized Postgres
- [ ] `alembic upgrade head` applies migrations
- [ ] `scripts/backfill.py SPY EFA IEF SHY --years 20` caches bars
- [ ] `uv run python -m quant.live.runner --broker alpaca-paper --dry-run` prints planned orders
- [ ] Discord test ping received

### Day 1 — YYYY-MM-DD

Expected:

Observed:

Anomalies:

### Day 2 — YYYY-MM-DD

Expected:

Observed:

Anomalies:

### Day 3 — YYYY-MM-DD

Expected:

Observed:

Anomalies:

### Day 4 — YYYY-MM-DD

Expected:

Observed:

Anomalies:

### Day 5 — YYYY-MM-DD

Expected:

Observed:

Anomalies:

## Wave-9 exit criteria

- [ ] 5 consecutive trading days completed with zero unhandled exceptions
- [ ] Daily Discord summary received each day
- [ ] Equity curve in `scripts/review.py` looks sensible
- [ ] No coherence bugs (duplicate orders, ghost positions) found
- [ ] Any discrepancies between backtest and paper are either benign
  (fee/slippage deltas within tolerance) or filed as fix tasks

## Post-wave-9 observations

<!-- Summary of the week's run, anything that informs Wave 10+. -->
