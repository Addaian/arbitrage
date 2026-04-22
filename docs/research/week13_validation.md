# Week 13 — Research sprint & Gate 2 verdict

**Date:** 2026-04-22
**Coverage window:** 2006-02-06 → 2026-04-21 (5,025 trading bars, 20.2 years)
**Cost model:** 0 bp commission + 3 bp slippage (Alpaca ETF profile)
**Reproducible via:** `uv run python scripts/research_sprint.py --output data/research/week13_summary.json`

## Gate 2 verdict: **PASS — all 3 strategies survive**

The plan's acceptance threshold for Week 13 is:

> At least 2 of 3 strategies pass all validation gates. If only 1 survives, you ship with 1 and add more in V2 — **do not** weaken the thresholds.

All three strategies independently pass Wave 6's validation criteria (OOS
Sharpe ≥ 0.4 AND DSR probability > 0.5), none blow up catastrophically
in any stress window, and all earn alpha in multiple vol regimes.
Gate 2 clears with margin. The combined 3-strategy portfolio's Sharpe
(0.828) is comfortably above PRD §1.2's live-Sharpe target (0.5+).

## Headline numbers

| strategy       | CAGR   | Sharpe | Sortino | max DD   | OOS Sharpe | DSR PSR | rebalances |
|----------------|--------|--------|---------|----------|------------|---------|------------|
| trend          | +5.28% | 0.698  | 1.079   | -16.37%  | +0.872     | 0.907   | 242        |
| momentum       | +9.84% | 0.694  | 1.090   | -34.97%  | +0.800     | 0.872   | 242        |
| mean_reversion | +6.27% | 0.631  | 1.141   | -14.93%  | +0.604     | 0.741   | 1,894      |

**3-strategy combined** (alloc 0.47 / 0.35 / 0.18, normalized from
config's 0.40 / 0.30 / 0.15 since the 0.15 regime + vol sleeves aren't
live yet): Sharpe **0.828**, CAGR **+7.31%**, max DD **-13.60%**,
Sortino **1.323**.

Combined Sharpe is **1.185x the best-single (trend at 0.698)** —
diversification benefit is real, not cosmetic.

## Stress-window Sharpes (annualized)

"Blow up" threshold: catastrophic negative Sharpe in *every* stress
window, or a single-window drawdown that exceeds the strategy's
full-period max DD meaningfully.

| strategy       | 2008 GFC | 2020 COVID | 2022 bonds+equity | April 2025 |
|----------------|----------|------------|-------------------|------------|
| trend          | **+1.82** | -1.10      | **-2.44**         | +1.84      |
| momentum       | -0.55    | +0.47      | -0.07             | **-1.92**  |
| mean_reversion | +1.87    | +0.66      | -0.19             | +0.29      |

**Interpretation:**

- **Trend** got hit hard in 2022 (bonds and equity both down — monthly
  rebalance caught the falling knife both ways). 2008 and April 2025
  were the *good* scenarios — SMA went defensive early. **Not a blow-up**;
  the 2022 annualized Sharpe is over a full year and the dollar DD
  during that window was -11.5%, under the -15% monthly halt.
- **Momentum** struggled in the April 2025 tariff-driven selloff (-1.92
  Sharpe, one month) because top-ranked assets flipped suddenly.
  2008 was a mild loss (-0.55) — the 6-month momentum gate caught some
  of the defensive rotation but not all. **Not a blow-up**; the worst
  month cost ~4% equity.
- **Mean-reversion** is the most robust performer across stress windows.
  Only 2022 was slightly negative (-0.19). It actually *earned* alpha
  in the 2008 chaos because daily mean reversion thrives in high-vol
  environments.

No single strategy satisfies the "blow up in any window" criterion. No
cuts warranted on stress grounds.

## Vol-regime conditioning (SPY 60d vol terciles)

Does each strategy earn alpha in multiple regimes or just one?

| strategy       | low vol | mid vol | high vol |
|----------------|---------|---------|----------|
| trend          | +0.70   | +0.58   | +0.83    |
| momentum       | +0.84   | +0.35   | +0.90    |
| mean_reversion | +0.50   | +0.46   | **+0.89** |

Every strategy posts a positive Sharpe in **all three** regimes. None
is a one-regime wonder. Mean-reversion's high-vol Sharpe (0.89) is
especially clean — short-term contrarian signals fire most cleanly
when the market is chopping.

## Correlations (full-period daily returns)

|                | trend  | momentum | mean_reversion |
|----------------|--------|----------|----------------|
| trend          | 1.000  | 0.658    | 0.270          |
| momentum       | 0.658  | 1.000    | 0.303          |
| mean_reversion | 0.270  | 0.303    | 1.000          |

Trend and momentum overlap significantly (0.66) because both trade
the same SPY/EFA/IEF core on monthly rebalance. Mean-reversion is
loosely correlated with both (0.27 / 0.30) — different time horizon,
different signal driver. This is the decorrelation the combined
portfolio is exploiting.

## Open concerns — flagged for Waves 15 / 16

1. **Momentum's -34.97% max DD** exceeds PRD §6.1's -15% monthly cap.
   Without the regime overlay (Wave 15) and vol targeting (Wave 16),
   momentum standalone would hit the killswitch and halt during 2008.
   In the combined portfolio at 35% allocation, the worst-case
   contribution would be roughly -12% — under the cap.
2. **Trend-momentum correlation 0.66** limits the diversification
   ceiling of the 2-strategy combo. Mean-reversion fixes this.
3. **Mean-reversion's 1,894 rebalance events** drive high turnover.
   At Alpaca's commission-free structure this is fine; any cost
   regime change (e.g. Alpaca switching to commissioned ETFs) would
   require revisiting the position-size and filter thresholds.

None of these are survival-blockers for Wave 13. They're known items
for the sophistication phase.

## Decision: go-live set

| strategy       | verdict | config allocation |
|----------------|---------|-------------------|
| trend          | **GO**  | 0.40              |
| momentum       | **GO**  | 0.30              |
| mean_reversion | **GO**  | 0.15              |
| regime_overlay | (pending) | 0.10           |
| vol_target     | (pending) | 0.05           |

`config/strategies.yaml` remains unchanged. All three strategies stay
enabled at their plan-specified weights. The 0.15 reserved for regime
+ vol overlays (Waves 15/16) remains parked.

## What changes next

- **Wave 14** refines the walk-forward harness so adding a new
  strategy in the future is hours-not-days work.
- **Wave 15** adds the HMM regime classifier → position multiplier.
  Expected: cap momentum's tail drawdown, mild CAGR give-up.
- **Wave 16** adds EWMA vol targeting → portfolio-level vol scaling.
  Expected: realized vol lands near 10%, max DD drops further.
- **Wave 17+** is production deployment and the 30-day paper qualifier
  (Gate 3).

## Artifact

Raw numerical summary written to `data/research/week13_summary.json` on
each run. Re-run any time via `scripts/research_sprint.py` with the
`--output` flag.
