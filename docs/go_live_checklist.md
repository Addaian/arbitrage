# Go-live checklist (Gate 3 / pre-Wave-20)

Plan Week 19 acceptance: *"A printed go-live checklist with every item
checked, signed/dated by you."*

Every box must be physically checked off, not hand-waved. If anything
on this list is unresolved, the correct answer is **extend paper by
another 30 days**, not ship live. Per the plan: *"do not go live with
unresolved issues."*

---

## A. Paper qualifier numbers

Wave 18 started the 30-day paper qualifier. By the time you run this
checklist, you should have 30 trading days of `pnl_snapshots` in
Postgres.

- [ ] `uv run python scripts/paper_vs_backtest.py --days 30` exits 0
- [ ] Paper 30-day Sharpe: ______________________
- [ ] Backtest 30-day Sharpe: ______________________
- [ ] Tracking error: ______ %   (**plan Gate 3: < 50%**)
- [ ] If tracking error ≥ 50%, you're extending paper by 30 days. Stop.

PRD §1.2 *long-run* criterion is <30% on 90-day rolling — that's a V1
success metric, not Gate 3. Gate 3 is the looser 30-day 50% bar.

## B. Alert review

Over the 30-day paper window, review every alert that fired.

- [ ] Total alerts fired: ______
- [ ] Each was valid (no spurious critical pages)? ☐ yes ☐ no
- [ ] Each mapped to a real anomaly, not a monitoring bug? ☐ yes ☐ no
- [ ] If any alert was a false-positive critical, the alert rule has
      been tightened before go-live? ☐ yes ☐ no ☐ n/a

Use `journalctl -u quant-runner.service --since "30 days ago" | grep
-i error` as one input, plus scroll back through the Discord channel.

## C. Disaster-recovery drill

Per `docs/disaster_recovery.md`, run Scenario 1 end-to-end.

- [ ] Scenario 1 (full VPS loss) rehearsed
- [ ] Wall-clock time: __________ minutes (**plan: < 60**)
- [ ] Any step in the runbook didn't work as written? Fix it and
      note the patch here: ____________________________________

## D. Kill-switch drill

The mechanism is tested in `tests/unit/test_killswitch_chaos.py` —
drill it against live paper now.

- [ ] SSH in, `sudo -u quant touch /var/lib/quant/HALT`
- [ ] Next cycle (wait or trigger manually) flattens every open position
- [ ] `journalctl -u quant-runner.service --since "5 minutes ago"`
      confirms the `killswitch engaged — flattening` message
- [ ] Alpaca account confirms 0 positions
- [ ] Remove the HALT file, next cycle runs normally
- [ ] Alpaca positions again match targets

## E. Broker / tax / admin

The operational-only items that can't be automated but block go-live.

- [ ] Alpaca **live** account KYC complete + approved
- [ ] Alpaca live account funded with **10%** of target capital (per plan Week 20)
- [ ] Beneficiary designation set on the Alpaca account
- [ ] Tax scheme recorded (W-9 on file with Alpaca US; ITIN/CP575 for
      non-US entities; note wash-sale handling strategy)
- [ ] 1099-B expected-delivery month noted in calendar: ________
- [ ] Live-account API keys generated, stored in password manager,
      **different from the paper keys**
- [ ] The person running this checklist knows how to call Alpaca
      support and has account-ID + PIN written down

## F. Observability sanity

- [ ] `curl -s http://localhost:9000/metrics | grep -c ^quant_` returns ≥ 10
- [ ] Grafana dashboard renders all 7 panels with non-empty data
- [ ] Alertmanager → Discord path verified by firing a test alert:
      `curl -s -XPOST http://localhost:9093/api/v2/alerts -d '[{"labels":{"alertname":"SmokeTest","severity":"info"}}]' -H "Content-Type: application/json"`
- [ ] Sentry receives a test exception: `cd /opt/quant-system && uv run
      python -c "from quant.monitoring.sentry import init_sentry; from
      quant.config import get_settings; init_sentry(get_settings()); raise
      RuntimeError('sentry smoke test')"`
- [ ] Rolling 30d Sharpe gauge in Grafana shows a reasonable non-zero value

## G. Codebase sanity

- [ ] `make check` green on main (local)
- [ ] CI green on main (GitHub Actions)
- [ ] PRs since paper started have all merged or been explicitly parked
- [ ] `config/strategies.yaml` allocations sum to 1.0 with the final
      strategy weights
- [ ] `config/risk.yaml` limits are at or below PRD §6.1 caps
- [ ] `git log --since="30 days ago" --oneline` has been reviewed —
      every commit is understood, none are "accidentally shipped
      half-done"

## H. Gates cleared (historical context)

- [ ] Gate 1 (Week 6) — trend validation passed. See `docs/research/week13_validation.md` for trend OOS Sharpe.
- [ ] Gate 2 (Week 13) — all 3 strategies survived. Same doc.
- [ ] Gate 3 (this wave, above §A) — ☐ PASS ☐ FAIL

**If Gate 3 fails, stop here. Extend paper by 30 days, re-run this
checklist. Do not weaken the threshold.**

## I. Sign-off

Completed by:  ________________________________________________

Date:          ____________   Time:     ____________   TZ: America/New_York

Paper qualifier window: _____________  →  _____________

Live go-live decision: ☐ GO (proceed to Wave 20)  ☐ NO-GO (extend paper)

If NO-GO, extension rationale and new paper-end date:

_______________________________________________________________

_______________________________________________________________

---

Keep a printed copy of this sheet in a physical binder alongside the
written Alpaca account ID + recovery PIN. Future-you at 2am needs the
paper copy.
