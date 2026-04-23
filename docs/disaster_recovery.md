# Disaster recovery runbook

Target RTO (recovery time objective): **under 1 hour from "VPS is dead"
to "runner executing next cycle"**. Plan Week 19 acceptance requires
running this drill at least once before go-live.

## Failure modes

This runbook covers the realistic V1 failure modes:

1. **VPS lost** — Hetzner node failure, lost root password, SSH key
   compromised, accidentally `rm -rf`'d. **Recovery: rebuild from
   bootstrap.sh + snapshot restore.**
2. **Postgres corrupted / lost** — disk fills up mid-write, schema
   migration fails halfway. **Recovery: pg_restore from last nightly
   dump.**
3. **Runner wedged but VPS healthy** — stuck cycle, ghost positions,
   Alpaca API change. **Recovery: kill-switch flatten → restart → reconcile.**

## Before you need this

These should be standing behaviours. Verify monthly during ops review.

- [ ] Hetzner snapshot taken after the last successful bootstrap. Take
      a new one any time the VPS image state changes non-trivially
      (package upgrades outside unattended-upgrades, Python version
      bump, schema migration).
      `hcloud server create-image --type snapshot --description
      "post-bootstrap $(date +%F)" <server-id>`
- [ ] `pg_dump quant` shipped off-VPS weekly. Store in an encrypted
      cloud bucket. The dump is ~5 MB at 1 year of live history, grows
      O(days) — cheap to keep many copies.
- [ ] `.env` backed up separately to a password manager (1Password /
      Bitwarden). Never to the same place as the Postgres dump.
- [ ] SSH key backed up to an off-line medium (YubiKey or paper
      printout of the private key if you must; encrypt the file
      otherwise).
- [ ] Alpaca API key rotation schedule documented — default is
      "on-demand, never automatic" — but the path from old-key-revoked
      to new-key-in-prod is tested.

## Scenario 1: full VPS loss

Wall-clock target: **<45 minutes**.

1. **Provision a replacement** Hetzner CX22 (ARM) in the same datacenter.
   Attach the latest snapshot as a boot volume. If the snapshot is
   fresh enough, you're done at step 3.
   *Or:* boot fresh Ubuntu 24.04.
2. Wire up DNS if you point a hostname at the runner (internal only —
   we don't expose anything but Grafana).
3. SSH in, verify `/opt/quant-system/` is present (snapshot path) or
   run `deploy/bootstrap.sh` fresh (from-scratch path):
   ```bash
   export POSTGRES_PASSWORD='...'     # from password manager
   curl -fsSL https://raw.githubusercontent.com/Addaian/arbitrage/main/deploy/bootstrap.sh -o bootstrap.sh
   chmod +x bootstrap.sh
   ./bootstrap.sh
   ```
4. Restore Postgres from the most recent dump:
   ```bash
   scp backup.sql quant-vps:/tmp/
   ssh quant-vps 'sudo -u postgres psql -d quant -f /tmp/backup.sql'
   ```
5. Repopulate `.env` from the password manager. Permissions back to 600:
   ```bash
   sudo cp /your/.env /opt/quant-system/.env
   sudo chown quant:quant /opt/quant-system/.env
   sudo chmod 600 /opt/quant-system/.env
   ```
6. **Reconciliation before the next cycle:** before re-enabling the
   timer, compare what the runner *thinks* it holds vs what Alpaca
   actually holds:
   ```bash
   sudo -u quant bash -c 'cd /opt/quant-system && \
     uv run python scripts/review.py --days 1'
   # vs
   sudo -u quant bash -c 'cd /opt/quant-system && \
     uv run python -m quant.live.runner --broker alpaca-paper --dry-run'
   ```
   The dry-run's "current qty" column is the broker's truth. If it
   matches your DB positions, you're clean. If it doesn't — engage
   the killswitch and reconcile manually before resuming.
7. Re-enable the scheduler:
   ```bash
   sudo systemctl enable --now quant-runner.timer
   sudo systemctl status quant-runner.timer
   ```
8. Restart the observability stack:
   ```bash
   cd /opt/quant-system/deploy/prometheus && docker compose up -d
   ```
9. Discord ping: drop a message so your on-call knows the runner is
   back. `curl` to the webhook URL.
10. Log the incident + recovery time in `docs/journal.md`.

## Scenario 2: Postgres-only loss

Wall-clock target: **<15 minutes**.

If the database is corrupted or got truncated but the VPS is fine:

1. Engage the killswitch — the runner depends on DB writes:
   ```bash
   sudo -u quant touch /var/lib/quant/HALT
   ```
   Verify the next cycle flattens.
2. Stop Postgres, snapshot the broken data dir (for forensics), drop it:
   ```bash
   sudo systemctl stop postgresql
   sudo mv /var/lib/postgresql/16/main /var/lib/postgresql/16/main.broken
   sudo -u postgres /usr/lib/postgresql/16/bin/initdb -D /var/lib/postgresql/16/main
   sudo systemctl start postgresql
   ```
3. Recreate the role + DB (one-shot — `bootstrap.sh` handles this
   idempotently; running the script again is safe):
   ```bash
   export POSTGRES_PASSWORD='...'
   sudo /opt/quant-system/deploy/bootstrap.sh
   ```
4. Restore the most recent nightly dump as in Scenario 1 step 4.
5. Re-apply any migrations that landed since the dump (usually none):
   ```bash
   sudo -u quant bash -c 'cd /opt/quant-system && uv run alembic upgrade head'
   ```
6. Remove the killswitch. The next timer fire resumes trading.
7. Log.

## Scenario 3: runner wedged, VPS healthy

Wall-clock target: **<5 minutes**.

1. Engage the killswitch first — before diagnosing anything, stop any
   ongoing damage:
   ```bash
   sudo -u quant touch /var/lib/quant/HALT
   ```
2. `journalctl -u quant-runner.service --since "15 min ago"` to find
   the stuck point.
3. If the runner's still running (`systemctl status quant-runner`),
   `systemctl stop` it.
4. Inspect positions + orders:
   ```bash
   sudo -u quant bash -c 'cd /opt/quant-system && uv run python scripts/review.py'
   ```
5. If positions don't match what Alpaca shows, reconcile manually — do
   NOT auto-trade until you've figured out why they diverged.
6. `systemctl restart quant-runner.timer` once clean.
7. Remove the killswitch. Log.

## DR-drill acceptance

Wave 19 requires running Scenario 1 end-to-end at least once before
go-live. The pass criterion is wall-clock:

```
<start>  ssh <fresh VPS>
<end>    systemctl status quant-runner.timer shows active + next run
```

Record the time. **Anything over 1 hour is a bug in bootstrap.sh
or this runbook**; fix it before go-live. The common bugs at this
stage are:
- Postgres role creation fails because the dump file references roles
  that bootstrap.sh didn't create yet.
- A timing-dependent env var (`ALPACA_API_KEY`) isn't copied off
  the password manager quickly enough.
- `docker compose up` on the observability stack stalls because the
  Grafana volume wasn't persisted in the snapshot.

## Not covered here (yet)

- Multi-region failover.
- Hot standby Postgres.
- Full off-VPS WAL archiving.

These are V2 concerns. At V1 capital scale (€3/mo VPS, 10% of target
deployed), an hour of downtime costs less than the infrastructure to
prevent it.
