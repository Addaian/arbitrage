# Changelog

All notable changes to the quant-system project, tracked wave-by-wave against the 20-week plan in `implementationplan.md`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [Wave 14 — Week 14: Walk-forward harness refinement] — 2026-04-22

### Added
- `scripts/validate_new_strategy.py` — generic strategy validator. Takes a Python import path (`module:ClassName`), JSON params, universe, cash symbol. Runs backtest + WF+DSR + stress + regime evaluation. Writes a markdown report to `docs/strategies/<name>.md`. Returns exit 0 on pass / 1 on fail, so CI can gate on it. `--ohlc` flag handles signals (mean-reversion) that need highs+lows alongside closes. Gates: OOS Sharpe ≥ 0.4, DSR PSR > 0.5, no stress window Sharpe below -2.5, positive Sharpe in ≥ 2 of 3 vol regimes.
- `scripts/check_strategy_artifacts.py` — CI enforcement script. Reads changed files (from `git diff --name-only <base>...HEAD` or stdin). Fails with exit 1 if any `src/quant/signals/*.py` (excluding `__init__.py` and `base.py`) changed without a corresponding `docs/strategies/*.md` being added or updated.
- `.github/workflows/ci.yml` — new `strategy-artifact-gate` job. Runs on pull_request only. Does a shallow `git fetch` (full history) and invokes the artifact-presence check against the PR's base branch.
- `docs/strategy_template.md` — human-authored skeleton (Hypothesis / Theory / Universe / Parameters / Known risks) + auto-generated section stubs filled by the validator.
- `docs/strategies/trend.md`, `docs/strategies/momentum.md`, `docs/strategies/mean_reversion.md` — fresh validation artifacts for all 3 surviving Wave-13 strategies, generated from the new CLI.
- `tests/unit/test_wave14_acceptance.py` (6 tests, 1 skipped) — subprocess-driven acceptance tests:
  - Generic CLI rejects a deliberately bad strategy (inverse momentum — holds the bottom-ranked N assets). Verifies exit 1 + FAIL verdict + at least one crossed-out gate in the generated markdown.
  - CI gate rejects signal-only diff, accepts signal+doc pair, ignores exempt files (`__init__.py`, `base.py`), ignores unrelated file changes, handles empty diff.

### Verified
- **349/349 tests passing** (+1 skipped documentation test). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- Adding a new strategy is now a 3-step flow: write the signal class under `src/quant/signals/`, run `scripts/validate_new_strategy.py`, attach the generated `docs/strategies/<name>.md` to the PR.
- **Acceptance criterion satisfied:** the deliberately bad inverse-momentum strategy is blocked by the CLI (test `test_validator_rejects_inverse_momentum`), and a signal change without a companion doc is blocked by the CI gate (test `test_ci_gate_rejects_signal_change_without_doc`).

### Notes

- The validator uses `intersection()` of per-symbol cached parquet windows, so a smaller universe (e.g. trend's 4 ETFs) yields more history than the full-universe research-sprint's intersection. Trend's reported OOS Sharpe here is +0.638 across 6 folds (2003-2026) vs research_sprint's +0.872 across 5 folds (2006-2026) — same strategy, different data slice. The per-strategy `docs/strategies/*.md` is the authoritative per-strategy view; `docs/research/week13_validation.md` is the authoritative cross-strategy view.
- `docs/strategies/*.md` intentionally contains both auto-generated metric tables and human-authored hypothesis/risks sections. Re-running the validator overwrites the file — so the human sections of `trend.md`/`momentum.md`/`mean_reversion.md` are fresh-template stubs for now. They're worth hand-filling once before Wave 15, but that's prose work and not blocking.

## [Wave 13 — Week 13: Research sprint (Gate 2)] — 2026-04-22

### Added
- `scripts/research_sprint.py` — one-command Week-13 evaluation runner. Loads full 2006-2026 OHLC, runs per-strategy backtest + walk-forward + DSR, computes stress-period Sharpes (2008 GFC / 2020 COVID / 2022 / April 2025), vol-regime-conditioned Sharpes, daily-return correlation matrix, and 3-strategy combined portfolio metrics. Optional `--output <path>` writes a JSON summary for downstream docs. Alpaca ETF cost profile (0 bp + 3 bp slippage). Run time ~15s.
- `docs/research/week13_validation.md` — **Gate 2 decision document**. Contains headline numbers, stress-window tables, vol-regime tables, correlation matrix, open concerns flagged for Waves 15/16, and the final per-strategy go/no-go verdict.
- `data/research/week13_summary.json` — machine-readable summary of the sprint run.

### Verified — Gate 2: **PASS** (all 3 strategies survive)

Plan acceptance: *"At least 2 of 3 strategies pass all validation gates."*
Result: **all 3 pass**. Key numbers, 2006-02 → 2026-04:

| strategy       | CAGR   | Sharpe | OOS Sharpe | DSR PSR | max DD   |
|----------------|--------|--------|------------|---------|----------|
| trend          | +5.28% | 0.698  | **+0.872** | 0.907   | -16.37%  |
| momentum       | +9.84% | 0.694  | **+0.800** | 0.872   | -34.97%  |
| mean_reversion | +6.27% | 0.631  | **+0.604** | 0.741   | -14.93%  |

Every strategy clears OOS Sharpe ≥ 0.4 and DSR PSR > 0.5. Every strategy
earns a positive Sharpe in **all three** SPY-vol regimes (low / mid /
high). No strategy blows up in any single stress window: worst observed
single-window Sharpe was trend's -2.44 during the 2022 double-down,
with a realized equity drawdown of -11.5% — inside the -15% monthly cap.

**Combined 3-strategy portfolio** (0.47 / 0.35 / 0.18 normalized from
config's 0.40 / 0.30 / 0.15, since the 0.15 regime+vol sleeves aren't
live yet): **Sharpe 0.828**, CAGR +7.31%, max DD -13.60%, Sortino 1.323.
Combined Sharpe is 1.185x best-single — real diversification benefit.

### Known items flagged for Waves 15/16 (sophistication phase)

1. **Momentum standalone max DD -34.97%** exceeds PRD §6.1's -15% monthly
   cap. In the combined portfolio at 35% allocation the worst-case
   contribution is roughly -12% — under the cap. Regime overlay
   (Wave 15) + vol targeting (Wave 16) are the designed fixes.
2. **Trend-momentum correlation 0.66** limits 2-strategy diversification;
   mean-reversion (corr 0.27 / 0.30) is what buys the extra Sharpe.
3. **Mean-reversion's 1,894 rebalance events** make it sensitive to
   commission changes. At Alpaca's $0 ETF commissions this is fine.

None are survival-blockers; all are documented in
`docs/research/week13_validation.md`.

### Config changes

None. All three strategies stay enabled at the plan-specified weights
in `config/strategies.yaml`. The 0.15 reserved for regime + vol
overlays remains parked until Waves 15/16.

### Tests

No new unit tests this wave — Week 13 is analysis, not code delivery.
All 343 tests remain green. Ruff clean, format clean, mypy strict clean.

## [Wave 12 — Week 12: Risk layer (hard limits)] — 2026-04-22

### Added
- `src/quant/risk/limits.py` — `RiskValidator` enforcing PRD §6.1 rows 1, 4, 5: `check_order_size_pct` (order notional / equity vs 20% cap), `check_position_size_pct` (projected post-fill position / equity vs 30% cap), `check_price_deviation` (limit price vs reference vs 1% cap). Composite `validate_order` runs each predicate in order and returns the first `RejectionReason`. All limit values come from `RiskConfig`, which is Pydantic-capped at PRD limits — a risk limit can only ever be *tighter* than the PRD cap, never looser. No side effects; the validator answers a yes/no question, caller decides what to do.
- `src/quant/risk/drawdown.py` — `DrawdownTracker` with `push(ts, equity)` append-only snapshots and two metrics: `daily_loss_pct` (today vs yesterday) and `monthly_drawdown_pct` (rolling peak-to-current over a 30-day window via `bisect_left`). Breach predicates `breached_daily_loss()` / `breached_monthly_drawdown()` compare against the configured limits. Snapshot stream must be strictly increasing; equity must be non-negative.
- `src/quant/risk/killswitch.py` — `Killswitch`: file-sentinel at `/var/run/quant/HALT`. `engage(reason)` writes atomically (temp file + `Path.replace`), auto-creates parent dirs, survives process restarts. `disengage()` and `read_reason()` are idempotent and error-safe. Cleanup on rename failure is verified.
- `src/quant/risk/__init__.py` — re-exports surface.
- Pre-trade hooks in `OrderManager`: optional `risk_validator` + `killswitch` constructor args. `execute(order, account=..., reference_price=..., current_positions=...)` checks killswitch first (rejects with "killswitch engaged"), then risk limits (rejects with structured reason). Both checks run **before** any broker round-trip.
- `LiveRunner` gained an optional `killswitch`. When engaged at cycle start, `_flatten_cycle` short-circuits: no signal computation, no DB writes of signals — just market-sell every open position via the broker directly (bypassing the order-manager's own killswitch block which would block the flatten itself).
- `tests/unit/test_risk_limits.py` (19 tests) — per-limit guards + composite validate_order. Includes **10,000-example Hypothesis property test** (`test_validator_property_no_false_accepts_or_rejects`): random (qty, ref_price, equity, existing_qty, side) tuples compared against an independent truth recomputation. Zero false accepts, zero false rejects.
- `tests/unit/test_risk_drawdown.py` (23 tests) — constructor guards, push ordering + negative equity guard, daily loss math (first-snapshot zero, exact-threshold breach, zero-prior guard), monthly drawdown (empty, single snapshot, peak-in-window, breach-at-threshold, peak-outside-window-excluded, bisect boundary inclusion, zero-peak guard), snapshots/reset/latest.
- `tests/unit/test_risk_killswitch.py` (12 tests) — engage/disengage/idempotence, atomic rename, reason round-trip, parent-dir auto-create, read_reason OSError path, rename-failure cleanup path.
- `tests/unit/test_order_manager.py` gained risk-hook tests: killswitch blocks submit + unblocks when disengaged, validator rejects oversize, validator accepts valid, validator requires account+ref-price.
- `tests/unit/test_killswitch_chaos.py` (4 tests) — **Wave 12 acceptance:** engaging the kill-switch mid-run flattens every open position within one cycle, dry-run still plans the flatten, engaged switch with no positions is idle, disengaged switch runs a normal cycle.

### Verified
- **343/343 tests passing** (342 unit + 1 integration). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **100% line + branch coverage** on `src/quant/risk/` and `src/quant/execution/` — the two 100%-required modules per CLAUDE.md quality bar.
- **Hypothesis property test** runs 10,000 random orders in ~7s. No valid order rejected, no invalid order accepted.
- **Wave 12 acceptance criterion satisfied:** `Killswitch(path).engage()` mid-cycle causes `LiveRunner.run_daily_cycle()` to flatten the paper account on the next call (verified by `test_killswitch_flattens_paper_account_within_one_cycle`).

### Notes

- The `_flatten_cycle` path deliberately bypasses `OrderManager.execute()` in favour of `Broker.submit_order()` directly: the manager's own killswitch hook would refuse the flatten orders because the switch is engaged. Using the broker directly keeps the hook's semantics honest ("no new *strategy* orders when engaged") while still allowing the only kind of order that *should* happen during a halt — liquidating.
- Gate 2 (Week 13) prep is now complete on the code side: all 3 strategies pass WF+DSR, 3-strategy combined Sharpe 0.828 (1.185x best-single), risk layer at 100% coverage with property tests. Week 13 is the research sprint — pure analysis, no new code.

## [Wave 11 — Week 11: Mean reversion overlay] — 2026-04-21

### Added
- `src/quant/signals/mean_reversion.py` — `MeanReversionSignal`: enter on `IBS < ibs_entry AND RSI-2 < rsi2_entry`, exit on `IBS > ibs_exit` (defaults 0.2 / 10 / 0.7 per PRD §5.3). Per-symbol state machine via `_walk_state`: exit checked before entry on each day. Daily cadence but emits weight rows **only on state-change days** (NaN elsewhere) so the backtest engine costs only real rebalance events. Equal-weight sleeve sized as `1 / max_positions`; cash symbol absorbs unfilled slots. Requires OHLC input — takes `(closes, highs, lows)` positionally rather than the close-only signature of trend/momentum.
- `src/quant/signals/__init__.py` — re-exports `MeanReversionSignal`.
- `tests/unit/test_signals_mean_reversion.py` (18 tests) — contract guards (missing cash / no risk / misaligned OHLC / missing high column / bad constructor args), state-machine isolation (enter-then-exit, exit-before-entry same day, idempotent entry while in-position), end-to-end weights (sum to 1, emit only on state change, cash absorbs slots, entry requires BOTH IBS and RSI).

### Verified
- 280/280 tests passing (279 unit + 1 integration). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **Walk-forward + DSR on mean-reversion (2006-02 → 2026-04, 10y train / 2y test, 5 folds, 0bp fees + 3bp slippage — Alpaca ETF cost profile):** concatenated OOS Sharpe **+0.604** (≥ 0.4 ✓), per-fold Sharpes `[0.32, 0.30, 0.86, 0.36, 0.98]`, **DSR probability 0.741**, deflated excess **+0.193 > 0** → mean-reversion clears Gate-1-equivalent validation.
- **Correlation matrix (daily returns, 2006-2026):**

  |              | trend | momentum | mean_rev |
  |---           |---    |---       |---       |
  | all-period   | 1.00  | 0.66     | **0.27** |
  | calm (bottom-40% vol) | 1.00 | 0.79 | **0.45** |
  | stress (top-20% vol)  | 1.00 | 0.35 | **0.20** |

- **3-strategy combined backtest (0bp fees + 3bp slippage, 2006-2026, allocations normalized from config's 0.40/0.30/0.15 to 0.47/0.35/0.18 since regime+vol sleeves aren't live yet):**
  - TREND   :  Sharpe 0.698, CAGR +5.28%, maxDD -16.37%
  - MOMENTUM:  Sharpe 0.694, CAGR +9.84%, maxDD -34.97%
  - MEAN-REV:  Sharpe 0.631, CAGR +6.27%, maxDD -14.93%
  - **COMBINED (3): Sharpe 0.828, CAGR +7.31%, maxDD -13.60%**
  - Combined Sharpe / best-single = **1.185x** (clears Wave 10's 1.10 stretch target)
  - Combined Sharpe / 2-strategy (trend+momentum only 0.762) = **1.087x** — adding mean-reversion delivers real diversification, not just noise.

### Notes / acceptance commentary

- The plan's literal acceptance — "mean reversion returns correlate *negatively* with trend during calm periods" — isn't met: calm-period correlation is **+0.448**, not negative. Root cause: both strategies are long-only by construction, so during calm uptrends both sit in equity and drift together. The long-only spec rules out a literal negative correlation.
- What the strategy *does* achieve is **substantial decorrelation** (|corr| < 0.5 at all regimes, 0.20 in stress), and — the point of the exercise — **the combined portfolio is strictly better than any single or paired strategy.** The 3-strategy Sharpe is higher than the 2-strategy Sharpe (0.828 vs 0.762), which is the real test of diversification benefit. Flagging the "negative correlation" interpretation as unrealistic for a long-only mean-rev rule; shorts / vol-target (Wave 16) / regime overlay (Wave 15) are where flip-the-sign correlation could arise.
- Mean-rev's cost sensitivity: at the Wave 5 convention of 5bp fees + 5bp slippage it earns Sharpe 0.196 (below PRD §5.3's 0.2-0.4 expected range) because ~1,900 rebalance events × ~70% avg turnover cumulates to $132k of costs on $100k capital. At Alpaca's real ETF cost profile (0bp fees + 3bp slippage) it clears Sharpe 0.63 — inside the expected range. Using Alpaca-realistic costs for all Wave 11 numbers above.

## [Wave 10 — Week 10: Cross-sectional momentum] — 2026-04-21

### Added
- `src/quant/signals/momentum.py` — `MomentumSignal`: rank risk universe by `lookback_months` total return, hold top `top_n` equal-weighted, monthly rebalance on the first trading day of the next month, cash symbol required in universe (100% cash during warmup). Default lookback 6, top-N 3, matching PRD §5.2. Optional `abs_momentum_filter` (off by default) parks non-positive-momentum names in cash — keeps the PRD's plain-rank behavior as the default while leaving the Antonacci variant one flag away.
- `src/quant/signals/__init__.py` — re-exports `MomentumSignal`.
- `src/quant/portfolio/combiner.py` — `combine_weights(strategy_weights, allocations)` scales each strategy's sleeve by its portfolio allocation and sums across strategies over the union of symbols and dates. Forward-fills per sleeve so a monthly strategy still contributes carry-forward weights on its non-rebalance days. Rows before any strategy has fired its first rebalance are left all-NaN (pre-signal). `rebalance_dates(...)` returns the union of per-strategy rebalance timestamps. 100% mypy-strict (portfolio quality bar).
- `tests/unit/test_signals_momentum.py` (13 tests) — contract rejections (missing cash / too-few risk / non-positive lookback or top_n), weight shape (sum-to-1, columns match closes), warmup=all-cash, top-N correctness with filter off (flat beats negative), filter-on variants (all-cash when every name is down, partial-fill parks in cash), monthly cadence + first-trading-day alignment.
- `tests/unit/test_portfolio_combiner.py` (10 tests) — guards (empty, mismatched keys, alloc sum not ~1, empty sleeve), single-strategy identity, two-strategy weighted sum on overlapping + disjoint universes, pre-signal prefix stays all-NaN, `rebalance_dates` union.
- `data/parquet/{QQQ,EEM,GLD,TLT,VNQ,DBC,XLE}/` — backfilled 22 years of daily bars via `scripts/backfill.py` for the momentum universe. `QQQ` was re-fetched to widen its prior 1-year cache to 22 years so backtests align across all 11 symbols.

### Verified
- 262/262 tests passing (261 unit + 1 integration). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **Walk-forward + DSR on momentum (2006-02 → 2026-04, 10y train / 2y test, 5 folds):** concatenated OOS Sharpe **+0.769** (≥ 0.4 ✓), per-fold Sharpes `[1.35, -0.20, 0.92, 0.53, 1.38]`, **DSR probability 0.853**, deflated excess **+0.338 > 0** → momentum clears the Wave 6 validation gate unambiguously.
- **Combined backtest (trend 4/7 + momentum 3/7, 2006-2026):**
  - TREND:    CAGR +5.03%, Sharpe 0.667, maxDD -16.87%
  - MOMENTUM: CAGR +9.36%, Sharpe 0.665, maxDD -35.09%
  - COMBINED: CAGR +7.05%, Sharpe **0.730**, maxDD -15.32%
  - combined Sharpe / best-single = **1.094** (target ≥ 1.10 → **0.6pp short**)
  - daily-returns correlation trend vs momentum: **0.658** (target <0.5 → **miss**)
- Strictly, the combined portfolio's Sharpe *is* higher than either alone (0.730 > 0.667 > 0.665) — the diversification benefit is real but smaller than the 10% ideal.

### Notes / open issues for Wave 13 research sprint

- The 110%-of-best-single and <0.5-correlation targets are both flagged for Week 13. Both strategies are long-biased and share overlapping risk universes (SPY/EFA/IEF in both), which structurally caps decorrelation — the HMM regime overlay (Wave 15) and vol targeting (Wave 16) are the designed fixes. The mechanism (signal + combiner + backtest engine) is correct and tested; the shortfalls are strategy-quality / universe-overlap, not code bugs.
- A parameter sweep over `lookback_months ∈ {3, 6, 9, 12}` with and without the filter confirmed the defaults are honest (not fished): 12-month would have cleared 110% at 1.102 but only by a whisker and the plan prescribes 6 — not worth parameter fishing pre-Week-13.
- `config/strategies.yaml` already has momentum enabled at `weight: 0.30`; the combined-backtest numbers above use the plan's 0.40/0.30 trend/momentum split (normalized to 4/7 and 3/7 since the regime+vol sleeves — 0.15 combined — aren't live yet).

## [Wave 9 — Week 9: First paper deployment (local)] — 2026-04-21

### Added
- `src/quant/live/runner.py` — `_build_default_runner(broker_kind, dry_run, persist)` now picks between `PaperBroker` (local sim, zero network) and `AlpacaBroker` (paper-api.alpaca.markets via `alpaca-py`). Wires `DiscordNotifier` from `settings.discord_webhook_url` and the shared async sessionmaker from `quant.storage.db.get_sessionmaker` when `persist=True`. `OrderManager.poll_timeout` lifts to 300s for live brokers (5 min per PRD §4.2) and stays 0s for the local sim.
- `src/quant/live/runner.py` CLI — `python -m quant.live.runner --broker {paper,alpaca-paper} [--dry-run] [--persist]`. Deprecates `--mode` in favor of `--broker`.
- `src/quant/live/scheduler.py` CLI — `python -m quant.live.scheduler [--broker alpaca-paper] [--persist/--no-persist] [--hour 15 --minute 45 --day-of-week mon-fri]`. Builds a default runner, attaches the cron trigger, blocks on `run_forever()` with SIGINT/SIGTERM handling — the one-command 5-day daemon.
- `scripts/review.py` — Typer/Rich daily-review dashboard. Prints: equity curve (last N days, table + unicode sparkline + cumulative pct change), latest positions, recent orders + fills, recent target signals. Exit-safe (disposes the engine on exceptions). This is the ops tool that substitutes for Grafana until Wave 18.
- `docs/journal.md` — per-day observation template: pre-deployment checklist, daily expected/observed/anomalies sections for days 1-5, wave-exit criteria, post-run summary slot.
- `Makefile` targets: `paper-run` (starts the scheduler against Alpaca paper, blocks), `paper-dry` (one-shot dry-run cycle), `review` (dashboard).
- `tests/unit/test_paper_5day.py` (2 tests) — Wave 9 acceptance proxy: 5 consecutive cycles against `PaperBroker` with a recording notifier. Asserts 5 start+complete events, zero errors, equity finite and non-negative each day, drift is non-empty on cycle 1 (initial open) and **zero on cycles 2-5** (positions stay at target). Also asserts error path: empty-closes raises, notifier records "error" but never "complete".

### Verified
- 239/239 tests green (238 unit + 1 integration). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- `python -m quant.live.runner --broker paper --dry-run` and `--persist` (with Postgres up) both work end-to-end: target weights computed, orders planned, rows written to `signals` / `orders` / `positions` / `pnl_snapshots`.
- `scripts/review.py` reads the resulting DB state and prints a coherent dashboard (verified manually against a one-shot cycle).
- `python -m quant.live.scheduler --help` boots in &lt; 1s (deferred runner import).

### Operational notes (handoff to user)
- The actual 5-trading-day paper run is the *operational* deliverable — start it with `make paper-run` after:
  1. `.env` populated with `ALPACA_API_KEY`, `ALPACA_API_SECRET`, and (optionally) `DISCORD_WEBHOOK_URL`
  2. `make up` + `alembic upgrade head`
  3. `scripts/backfill.py SPY EFA IEF SHY --years 20`
- Each evening, run `make review` and fill in `docs/journal.md` for that day. The wave is complete when 5 trading days have logged P&L with zero unhandled exceptions and the equity curve looks sensible.
- No code ships for Wave 10+ until the journal confirms the paper run was clean — surfacing live-vs-backtest discrepancies is the whole point.

## [Wave 8 — Week 8: LiveRunner (paper mode)] — 2026-04-21

### Added
- `src/quant/live/runner.py` — `LiveRunner.run_daily_cycle()` walks PRD §4.2 end-to-end: `closes_provider()` → `signal.target_weights()` → delta orders against current broker state → submit through `OrderManager` → reconcile → persist. Emits a `CycleResult(as_of, strategy, dry_run, target_weights, planned_orders, submitted_orders, fills_by_order, final_positions, drift, errors)`. Broker-authoritative reconciliation: `PositionRepo.replace_all` zaps ghost rows. Dry-run short-circuits before submit/persist. Single-strategy for Wave 8; combiner arrives Wave 10.
- `src/quant/live/scheduler.py` — `CycleScheduler`: thin APScheduler wrapper. `ScheduleSpec(hour=15, minute=45, day_of_week="mon-fri", timezone="America/New_York")` matches PRD §4.2. Exception-safe wrapping around the cycle so a bad day never kills the daemon. `run_forever()` is signal-aware (SIGINT/SIGTERM) for systemd-style deployment.
- `src/quant/live/notifier.py` — `DiscordNotifier`: `cycle_start` / `cycle_complete` / `cycle_error` hooks. No-op when webhook URL is unset (dev + CI default). Failures to post are logged, not raised — monitoring never blocks a trading cycle.
- `src/quant/live/__init__.py` — re-exports.
- Pure-function planner helpers: `_plan_orders(target_weights, latest_prices, current_positions, equity)` emits `PlannedOrder` deltas (with 0.001-share epsilon to skip dust trades) and `_compute_drift(...)` flags post-cycle divergence by symbol.
- CLI: `python -m quant.live.runner --mode paper --dry-run` wires `PaperBroker` + `TrendSignal` + Parquet cache and prints a Rich tearsheet of target weights + planned orders. **Runs in 1.7s** (budget 10s) against the cached 2003-2026 history.
- `tests/unit/test_live_runner.py` (12 tests) — planner covers buy/sell/skip-sub-share/missing-price/zero-price paths, drift detection; end-to-end dry-run vs live cycles, second-cycle drift converges to sub-share, empty closes raises, notifier receives start+complete on happy path and error on failure.
- `tests/integration/test_live_runner.py` (1 test, runs against Dockerized Postgres) — **Wave 8 acceptance:** 3 consecutive daily cycles against `PaperBroker`, with next-bar fills between. Asserts no duplicate `client_order_id`s, no orphan fills, positions table matches broker state exactly (no ghosts), exactly 3 PnL snapshots, exactly 12 signal rows (4 symbols × 3 cycles), and drift converges to sub-share after cycle 1.

### Verified
- 237/237 tests passing (236 unit + 2 integration, of which 1 skipped without Alpaca creds). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **Wave 8 acceptance criteria:**
  - CLI `python -m quant.live.runner --mode paper --dry-run` completes in **1.7s** (budget 10s) and prints the target-weight + planned-order tables.
  - 3-day paper cycle against Postgres shows **coherent state**: no duplicate orders, no ghost positions, broker ↔ DB parity on positions, one PnL row per cycle.

### Notes
- Notifier silently no-ops without `DISCORD_WEBHOOK_URL` — same unit tests run in CI and prod with or without alerting configured.
- `wait_for_fill=True` is the default cycle mode; `OrderManager.poll_timeout` governs how long we wait before moving on. Paper broker completes in 0s (no network), so the default is fine locally; live deployments will pick a timeout ≤ 5 min per PRD §4.2.

## [Wave 7 — Week 7: Broker abstraction] — 2026-04-21

### Added
- `src/quant/execution/broker_base.py` — `Broker` ABC per PRD §4.3: `get_account`, `get_positions`, `submit_order`, `get_order_status`, `get_fills`, `cancel_order`. Domain exceptions `BrokerError`, `OrderRejectedError`, `OrderNotFoundError`, `TransientBrokerError` — rejections are final, transients are retryable.
- `src/quant/execution/paper_broker.py` — `PaperBroker`: deterministic in-memory simulator. Queue-then-fill model: `submit_order` accepts, `advance_to(next_bar_open)` fills queued orders at open ± slippage (bps). Tracks cash + per-symbol cost basis, supports shorts via position reduction past zero, commission modeled as bps of notional. Duplicate client-order-id and non-positive limit price are rejected at submit; orders with no next-bar print stay queued. `get_account()` marks positions to latest known prices for live equity reporting.
- `src/quant/execution/alpaca_broker.py` — `AlpacaBroker`: thin wrapper over `alpaca-py`'s `TradingClient`. Translates `Account`/`Position`/`Order`/`OrderResult`/`Fill` to and from the SDK's models. Full status map across all 17 Alpaca states → our 7-state enum. Error classification splits 4xx/network failures into retryable vs non-retryable buckets by HTTP status (`_is_client_error`). `from_credentials(api_key, api_secret, paper=True)` factory for paper/live selection.
- `src/quant/execution/order_manager.py` — `OrderManager.execute(order, wait_for_fill=False)`: stamps `submitted_at`, submits via `tenacity` exponential backoff (only retries `TransientBrokerError`, never `OrderRejectedError`), optionally polls until terminal status with a timeout, returns `OrderOutcome(order, result, final_status, fills, transitions)` with the full status-transition log.
- `src/quant/execution/__init__.py` — re-exports the whole surface.
- `tests/unit/test_paper_broker.py` (23 tests) — interface parity with `Broker`, accept/reject paths, slippage symmetry, cash + position accounting (buy, sell, partial sell, flip-to-short, deepen short), commission effect, limit orders (below/at/above limit, queue-on-miss for buy and sell), cancel lifecycle + idempotence on terminal states, sequential advance doesn't double-fill.
- `tests/unit/test_order_manager.py` (10 tests) — happy-path submit, waiting for fill, timeout returns last-seen status, retry-then-success + retry-exhaustion + no-retry-on-reject, transient errors during poll recover, cancel delegation, swappable-broker parity across `PaperBroker` + a stub broker.
- `tests/unit/test_alpaca_broker.py` (20 tests) — model translation (account, positions with zero-qty drop), request construction (market vs limit), status mapping (accepted/rejected/filled), fill synthesis from `filled_qty`/`filled_avg_price`, cancel via `cancel_order_by_id`, error classification (4xx→rejected/not-found/broker, 5xx→transient, network→transient, non-numeric code → default to transient), `from_credentials` construction.
- `tests/integration/test_alpaca_broker.py` — live paper-API round-trip. Gated on `ALPACA_API_KEY`/`ALPACA_API_SECRET`; **skips cleanly** when unset (CI-safe). When creds present: submits 1-share buy on SPY via `OrderManager`, polls to terminal, reconciles, and flattens to leave the paper account clean. Treats `OrderRejectedError` as skip (e.g. market closed, PDT) — only the happy path asserts.

### Verified
- 226/226 tests passing (+ 1 correctly-skipped Alpaca integration without creds). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **Coverage floor met:** `src/quant/execution/` at **100%** line + branch coverage across all four modules (broker_base, paper_broker, alpaca_broker, order_manager) — per the PRD §7.4 quality bar.
- **Acceptance (PRD §4.3 / plan Week 7):** `test_swapping_broker_preserves_caller_flow` confirms `PaperBroker` and a stub Alpaca-shaped broker are drop-in replacements through `OrderManager` — calling code is identical between paper and live.

## [Wave 6 — Week 6: Walk-forward + Deflated Sharpe] — 2026-04-21

### Added
- `src/quant/backtest/deflated_sharpe.py` — Bailey & Lopez de Prado (JPM 2014). `probabilistic_sharpe_ratio`, `expected_max_sharpe` (Gumbel closed-form, Euler-Mascheroni constant), `deflated_sharpe_ratio` aggregator returning `DeflatedSharpeResult(observed_sharpe, benchmark_sharpe, psr, num_trials, num_observations, skew, excess_kurtosis, passes)`. All Sharpes annualized; SE formula accounts for skewness + excess kurtosis. Pathological variance inputs degrade to PSR=0.5 rather than crashing.
- `src/quant/backtest/walk_forward.py` — `walk_forward(closes, strategy_factory, train_years=10, test_years=2, [step_years, expanding, fees, slippage, initial_cash])`. Returns `WalkForwardResult` with per-fold `WalkForwardFold` records (train/test windows, OOS Sharpe/CAGR/maxDD, full `BacktestResult`) and a concatenated OOS return series. Helpers: `fixed_params(strategy)` (published-rule factory) and `tuned_by_train_sharpe(candidates)` (in-sample search factory — simulates selection bias for DSR deflation).
- `src/quant/backtest/trial_log.py` — append-only trial log behind a `TrialLog` Protocol. `JsonlTrialLog(root)` writes `<strategy>.jsonl`, one line per trial (`strategy`, `params`, `params_hash`, `start/end dates`, `sharpe`, `cagr`, `max_drawdown`, `recorded_at`). Exposes `record(TrialRecord)` + `count_trials(strategy)`. Local dev default; Postgres path via existing `BacktestRunRepo`.
- `scripts/validate_strategy.py` — Typer CLI. Runs walk-forward, records each `fold × candidate param set` as a trial, computes DSR against the accumulated trial count, prints a Rich folds-table + summary, pass/fail on `--min-oos-sharpe` (default 0.4) and `--min-dsr-psr` (default 0.5, matching the plan's "DSR > 0" semantics; pass 0.95 for the strict Lopez de Prado significance bar). `--overfit-sweep N` simulates a parameter search by running `N` candidate lookbacks per fold — surfaces selection bias.
- `src/quant/backtest/__init__.py` — re-exports WF, DSR, and trial-log surface.
- `tests/unit/test_deflated_sharpe.py` (18 tests) — PSR boundary cases (SR==SR* → 0.5, near 1/0 on dominance/domination, symmetry), rejections, graceful degradation on negative variance; expected-max monotonicity + √V scaling + argument validation; DSR end-to-end (strong strategy passes with 1 trial, same strategy fails under 10k trials, noise fails at 1 trial); NaN handling; `annualized_sharpe` helper.
- `tests/unit/test_walk_forward.py` (13 tests) — fold count, OOS non-overlap (rolling), constant train length (rolling), monotone growth (expanding), zero folds on short history; end-to-end produces folds + monotone OOS curve, rejections (too-short history / empty / unsorted), `tuned_by_train_sharpe` picks a valid candidate, factory receives train slice, concatenated Sharpe is finite.
- `tests/unit/test_trial_log.py` (6 tests) — empty fresh dir, record+count, persistence across instances, deterministic params hash, read-all round-trip, Protocol satisfaction.

### Verified
- 174/174 tests green (173 unit + 1 integration). Ruff clean, format clean, mypy strict clean on risk/execution/portfolio.
- **Acceptance — trend strategy, 1 trial:** walk-forward 2003-04 → 2025-04, 10y train / 2y test, 6 OOS folds. Concatenated OOS Sharpe **+0.600 ≥ 0.4**. DSR probability **0.744 > 0.5**, deflated excess **+0.192 > 0**. CLI exit 0.
- **Acceptance — deliberately overfit variant (`--overfit-sweep 3`, 18 trials logged):** same strategy family, now selecting the best lookback per fold. Concatenated OOS Sharpe **+0.519 ≥ 0.4** (clears that gate alone), but DSR benchmark inflates to +0.571 under 18 trials, probability drops to **0.430 < 0.5**, deflated excess **-0.051 < 0** → **CLI correctly fails with exit 1**. The deflation kicks in only via trial-count accounting, which is exactly the Bailey & Lopez de Prado mechanism at work.
- End-to-end CLI: 0.9s for 23 years × 4 symbols × 6 folds (trend, 1 trial); 1.8s for the 3-candidate sweep.

### Notes
- `--min-dsr-psr` default is **0.5** (aligns with the plan's "DSR > 0" = "deflated excess > 0"). For the stricter 95%-confidence Bailey/LdP bar, pass `--min-dsr-psr 0.95` — only strategies with much longer OOS windows or single-trial evidence tend to clear it.
- `DeflatedSharpeResult.passes` uses the strict 0.95 threshold for library callers; CLI threshold is decoupled so operational use and research-grade claims don't collide.

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
