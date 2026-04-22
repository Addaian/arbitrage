# Day-1 live retrospective

Written the day **after** the first live cycle. Not a debug log —
compressed, honest, actionable. A reader a year from now (you, on a
Sunday at 2am) should get the full picture in under five minutes.

## Pre-flight

- Date/time of paper→live flip (ISO):
- Preflight output saved: `journalctl -u quant-runner-live.service -b > artefacts/preflight.log`
- Pre-flight script exit code:  ☐ 0 (GO)   ☐ 1 (NO-GO)
- Any gate failed on first run? What was it?

## First cycle

- Scheduled fire time (should be 15:45 ET on a Mon–Fri):
- Actual fire time:
- Cycle duration (from Grafana):
- Orders submitted:
- Orders filled:
- Fills matched planned qty within fractional-share tolerance?  ☐ yes ☐ no
- Discord "cycle complete" message received?  ☐ yes ☐ no

## What the portfolio does NOW

Snapshot right after cycle 1 settles:

- Account equity (live):
- Per-position table (copy from `make review`):

| symbol | qty | avg entry | market value | unrealized P&L |
|---|---|---|---|---|
|  |  |  |  |  |

- Killswitch state:  ☐ not engaged  ☐ engaged (why?):

## Deltas from paper

What's different between live and paper's last known state. This is
where you'll catch sizing bugs, slippage surprises, "oh right, Alpaca
rounds differently" gotchas.

- Target weights (from the cycle log) vs actual post-fill weights:
- Per-symbol slippage (`fill_price - reference_price`) — any surprising?
- Commission charged (should be $0 for ETFs on Alpaca):
- Any position that didn't fill (rejection reasons):

## What surprised you

Things the backtest / paper run didn't warn about. Even if trivial.
Write them down — these become property-test cases, alert rules, or
journal followups.

-

## Alerts during day 1

From the Alertmanager log + Discord channel:

| time | severity | alert | root cause |
|---|---|---|---|
|  |  |  |  |

Every critical alert must be explained here. Unexplained criticals at
day 1 means reverting to paper.

## Action items

What must change *before* leaving 10% capital for longer than a week.
If the list has anything in it, don't scale up.

- [ ]
- [ ]
- [ ]

## Verdict

☐ Stay live at 10% for the full week per plan.
☐ Revert to paper (`make paper-switch`), fix above action items, redo Gate 3.
☐ Other:

Operator:  ____________________
Date:      ____________________
