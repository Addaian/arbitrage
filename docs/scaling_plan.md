# Capital scaling plan

Plan Week 20 deploys **10% of target capital**. Scaling from there
follows a deliberate, gated schedule — don't collapse the timeline just
because the first week looked fine. The only thing a 1-week sample
proves is that the infrastructure didn't blow up.

## Timeline

| Week | Capital % | Trigger to advance |
|---|---|---|
| 20  | 10%  | First live day; monitor daily for 5 days |
| 20 + 1 week (21) | 10% (hold) | Gate 4 review: zero manual interventions, all alerts valid |
| 20 + 2 weeks (22) | **50%**  | Gate 4 passed + Sharpe tracking within 50% of backtest over live window |
| 20 + 3-5 weeks (23-25) | 50% (hold) | Watch for sharp drawdown behaviour |
| 20 + 6 weeks (26) | **100%** | No new critical alerts, live 90-day tracking error < 50% |

## Triggers to advance

At each step-up, all of these must be true:

- [ ] No manual interventions in the prior window (no position
      reconciliation, no config override, no killswitch engage-disengage
      that wasn't a planned drill).
- [ ] All alerts fired were valid (no spurious criticals).
- [ ] Live vs backtest Sharpe within the window's tracking-error target:

    | Step-up | Target |
    |---|---|
    | 10% → 50% (week 22) | Sharpe within 50% of backtest over 2-week live window |
    | 50% → 100% (week 26) | Sharpe within 30% of backtest over 4-week live window |
    | PRD §1.2 long-run | Within 30% on 90-day rolling |

    Run `scripts/paper_vs_backtest.py --days 14` (or 28, or 90) to
    compute — the script works against live PnL snapshots too since the
    same `pnl_snapshots` table is populated by the live runner.

- [ ] `docs/journal.md` is current (entry per trading day, anomalies
      noted, nothing outstanding).

## Triggers to scale DOWN

Any ONE of these reverts to the prior allocation level (or to paper):

- Max monthly drawdown hits -15% (PRD §6.1 cap) — the killswitch will
  already have flattened; resume only after manual review.
- Live Sharpe falls below 0 over any 30-day window.
- Two critical alerts in the same 7-day window with root causes that
  aren't obviously benign.
- Tracking error vs backtest > 75% on any 14-day window.

Scaling down is not failure — it's the designed response to the
signal that live behaviour has diverged from the backtest. The 20-week
plan exists to *detect* that cheaply; this playbook says what to do
next.

## Mechanics of changing deployed capital

Alpaca supports partial withdrawals / deposits. To go from 10% → 50%:

1. Transfer additional funds to the Alpaca live account via ACH.
2. Wait until funds settle (1–2 business days on new-ish accounts,
   same-day on established ones).
3. Next cycle will automatically size positions against the higher
   equity figure — no config change needed. The strategy sleeves are
   percentage allocations, not dollar targets.
4. Verify on the day of the first post-increase cycle:
   - `scripts/review.py` shows the new equity
   - Position values are proportionally larger
   - `quant_equity_usd` in Grafana shows the step function

Scaling down is the inverse: withdraw, wait, let the cycle rebalance
to the new equity on its own.

## Why 10% / 50% / 100% and not other splits

Exponential doublings would go 10 → 20 → 40 → 80 → 160% which blows
past the target. Linear (10 / 25 / 50 / 75 / 100%) is slower than the
plan's 3 steps and doesn't add meaningful signal. Three steps is the
minimum needed to catch "works at small scale but fails at full
capital" kinds of bugs (slippage scaling non-linearly, market impact,
order-routing edge cases).

## Non-goals

- Beating the backtest. The backtest is a ceiling; live will be below it.
- Reaching target capital on any specific date. The schedule is a
  maximum pace, not a quota.
- Adding strategies mid-scaling. New strategies go through Wave 14's
  validation + `docs/strategies/<name>.md` flow and do NOT ride along
  with a capital step-up.
