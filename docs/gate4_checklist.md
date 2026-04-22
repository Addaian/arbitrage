# Gate 4 checklist (Week 20 + 1 week live)

**Plan acceptance:** *"End of week 20, live account has been trading for
5 days with no manual interventions required."*

**Gate 4** is the final gate of the V1 plan. Pass clears the path to
the 50% capital step-up in week 22 (see `docs/scaling_plan.md`); fail
reverts to paper and re-runs Gate 3 after another 30 days.

Run this checklist exactly 7 calendar days after `make live-switch`.

---

## A. Cycle completions

- [ ] 5 trading days' worth of cycles fired at 15:45 ET (accounting for
      holidays — skip those, don't count them as misses)
- [ ] Every cycle has a corresponding row in `pnl_snapshots`
- [ ] `quant_cycle_errors_total` over the window: ______  (must be 0)
- [ ] `quant_heartbeat_seconds` never more than 26h stale during
      trading-day gaps

Query to verify:
```sql
SELECT ts, equity, cash, daily_return
FROM pnl_snapshots
WHERE ts >= now() - interval '7 days'
ORDER BY ts DESC;
```

Expect exactly 5 rows.

## B. No manual interventions

Per the plan's literal acceptance: **zero**. Count manual events
strictly — anything that required you to touch the VPS or the
strategies config beyond "ssh in to read logs" is an intervention.

- [ ] No `systemctl restart quant-runner-live.service` (other than
      planned restarts at a full deploy)
- [ ] No killswitch engage/disengage
- [ ] No `psql` writes
- [ ] No `config/*.yaml` edits
- [ ] No `scripts/` run that mutates state (backfill is read-only OK;
      train_regime that overwrites the model is OK)

If any of these happened, write the root cause in `docs/journal.md`
and then decide whether it was a bug that must land before advancing
or a one-off acceptable hiccup.

## C. Alert review

From Discord channel + `journalctl -u quant-runner-live.service --since
"7 days ago" | grep -i alert`:

- Total alerts fired: ______
- Critical alerts: ______   (**must all be valid or this gate fails**)
- Warnings fired: ______

For each critical, write: what fired, why, did it need action.

| time | alert | root cause | action taken |
|---|---|---|---|
|  |  |  |  |

## D. Tracking error

```bash
uv run python scripts/paper_vs_backtest.py --days 7
```

Note: the tool compares against the SAME 3-strategy combined backtest
window. A 7-day Sharpe is very noisy — this is a smell test only.

- Live 7-day Sharpe: ______
- Backtest 7-day Sharpe: ______
- Tracking error: ______ %

**Gate 4 criterion**: tracking error < 75% on 7-day (loose, because
7 days is noisy). The gating tracking-error number is the **2-week**
check in `docs/scaling_plan.md` at the 50% step-up, not here.

## E. DR drill re-run (on live data)

Scenario 2 from `docs/disaster_recovery.md` — Postgres-only loss —
should be rehearsed against the LIVE database to prove the pg_dump /
pg_restore flow works on production data. Target: <15 min RTO.

- [ ] Fresh `pg_dump quant > live-backup.sql` taken and verified
      (grep for `pnl_snapshots` in the dump — must be non-empty)
- [ ] Restore into a scratch database verified:
      `createdb quant_dr_scratch && psql -d quant_dr_scratch -f live-backup.sql`
- [ ] Row counts match source (pnl_snapshots, orders, fills, positions)
- [ ] Scratch database dropped

Do NOT run Scenario 1 (full VPS wipe) against the live VPS while the
runner is trading. Schedule it for a weekend.

## F. Grafana dashboard audit

Not every panel needs to be exciting; the dashboard just needs to
reflect reality.

- [ ] Equity curve is continuous (no gaps mid-trading-day, no flat
      horizontal lines during market open)
- [ ] Per-symbol position values match `scripts/review.py`
- [ ] Rolling 30d Sharpe shows a reasonable value (could be anything —
      5 days of live is too short for a stable 30d number; it's just a
      sanity check that the gauge is being written)
- [ ] Killswitch panel shows 0 (not engaged)
- [ ] Cycle duration p95 < 60s

## G. Verdict

☐ **PASS** — hold 10% through week 22, then evaluate for step-up per
`docs/scaling_plan.md`.

☐ **FAIL** — revert: `make paper-switch`. Open an incident in
`docs/journal.md` with the root cause. Wait 30 calendar days in paper
before retrying Gate 3.

---

Operator sign-off:

Completed by:  ____________________________________________

Date:          ____________    Time:   ____________  (America/New_York)

Paper→live flip date:  ____________________

Live window reviewed: _____________  to  _____________

Decision: ☐ GO (hold 10%)  ☐ NO-GO (revert to paper)

Comments / follow-ups:

_______________________________________________________________

_______________________________________________________________

_______________________________________________________________
