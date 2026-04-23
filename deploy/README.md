# Production deployment (Wave 17)

A cold-booted Ubuntu 24.04 VPS is bootable to a running paper scheduler
in under 30 minutes via `deploy/bootstrap.sh`. This doc is the operator
runbook.

## Target hardware

- **Hetzner CX22** (2 vCPU ARM, 4 GB RAM, 40 GB NVMe — €3.29/mo) or
  **CPX21** (3 vCPU x86, 4 GB RAM, 80 GB NVMe — €4.15/mo).
- Either is enough for V1: Postgres (~5 GB on 5 years of bars), Python
  runtime, APScheduler.
- Ubuntu 24.04 LTS.

## One-shot bootstrap

On a freshly-provisioned VPS, logged in as `root` over SSH:

```bash
export POSTGRES_PASSWORD='...'            # required
# Optional overrides:
# export REPO_BRANCH=main
# export INSTALL_DIR=/opt/quant-system
# export DEPLOY_SYSTEMD=timer              # or 'scheduler'

curl -fsSL https://raw.githubusercontent.com/Addaian/arbitrage/main/deploy/bootstrap.sh -o bootstrap.sh
chmod +x bootstrap.sh
./bootstrap.sh
```

The script:

1. Apt-updates + installs Python 3.12, postgres, ufw, fail2ban,
   unattended-upgrades, build toolchain.
2. Sets system timezone to `America/New_York` (aligns the
   systemd-timer OnCalendar with market hours without per-unit TZ
   overrides).
3. Enables unattended security upgrades, UFW (deny incoming, allow
   SSH + Grafana :3000), fail2ban.
4. Installs `uv` to `/usr/local/bin`.
5. Creates Postgres role + database (idempotent), enables TimescaleDB
   if available.
6. Creates the `quant` system user + `/opt/quant-system` install dir.
7. Clones the repo at the configured branch (or pulls `--hard reset` if
   the dir already exists).
8. `uv sync --extra dev` to build the venv.
9. Writes a `.env` template (the script REFUSES to start if
   `POSTGRES_PASSWORD` is unset in the bootstrap env; API keys are
   placeholders you fill in after).
10. `alembic upgrade head`.
11. Installs `deploy/systemd/*.service` + `*.timer` under
    `/etc/systemd/system/`, enables the chosen mode (`timer` by default).

Idempotent: safe to re-run. Re-running updates the repo to the latest
branch HEAD, re-applies migrations, and restarts the service.

## After bootstrap

1. **Fill in secrets.** Edit `/opt/quant-system/.env`:

   ```ini
   ALPACA_API_KEY=AK...
   ALPACA_API_SECRET=...
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   SENTRY_DSN=https://...@sentry.io/...
   ```

   Permissions are already `chmod 600`. Don't relax them.

2. **Restart so the new env takes effect:**

   ```bash
   systemctl restart quant-runner.timer
   # or, scheduler mode:
   systemctl restart quant-scheduler.service
   ```

3. **Smoke test a single cycle:**

   ```bash
   sudo -u quant bash -c 'cd /opt/quant-system && uv run python -m quant.live.runner --broker alpaca-paper --dry-run'
   ```

   Should print target weights and planned orders in <10s. No DB
   writes in dry-run mode.

4. **Tail the logs** at 3:45pm ET on any weekday:

   ```bash
   journalctl -u quant-runner.service -f
   ```

## Deployment modes

The plan deliberately supports two mutually-exclusive scheduling
strategies:

### `timer` mode (default) — systemd cron

`quant-runner.timer` → `quant-runner.service` (oneshot). systemd owns
the schedule; a missed run (VPS was down at 3:45pm) fires on reboot
(`Persistent=true`).

Best for: standard paper/live trading where systemd's robustness
(restart-on-failure, missed-run recovery, timezone handling) is worth
more than in-process scheduling precision.

### `scheduler` mode — Python APScheduler daemon

`quant-scheduler.service` runs `python -m quant.live.scheduler` as a
long-lived daemon. APScheduler handles the cron internally.

Best for: deployments where a single process owning the lifecycle
matters (in-process caching, warmed connections, etc).

Toggle by re-running bootstrap with `DEPLOY_SYSTEMD=scheduler` or by
hand:

```bash
systemctl disable --now quant-runner.timer
systemctl enable --now quant-scheduler.service
```

## Operational checklist (first-run)

- [ ] `ssh vps` — landed as root on fresh Ubuntu 24.04
- [ ] `./bootstrap.sh` — exits cleanly
- [ ] `/opt/quant-system/.env` filled with real Alpaca keys
- [ ] `systemctl status quant-runner.timer` — **active (waiting)**
- [ ] `systemctl list-timers quant-runner.timer` — next fire at 3:45pm ET
- [ ] `journalctl -u quant-runner.service -n 50` — no startup errors
- [ ] Dry-run cycle completes with a populated target-weights table
- [ ] Kill-switch touch works:
      `sudo -u quant touch /var/lib/quant/HALT` then verify next cycle
      flattens and exits; remove the file to resume.

Target: all boxes checked within **30 minutes** of initial SSH.

## Rolling back

- **Stop the live cycle:** `systemctl stop quant-runner.timer` (or
  `quant-scheduler.service`).
- **Engage the kill switch:** `touch /var/lib/quant/HALT`. The next
  cycle (whether from the timer or the running scheduler) flattens all
  positions and exits.
- **Downgrade a bad release:** `sudo -u quant git -C /opt/quant-system
  reset --hard <prev-sha>` then `systemctl restart quant-runner.timer`.

## Hetzner notes

- The Hetzner Cloud console exposes a recovery mode if you lose SSH —
  boot into rescue, mount the disk, edit `/etc/ssh/sshd_config` or the
  authorized_keys file. Don't rely on this; keep the SSH key backed up.
- Snapshots: `hcloud server create-image` via the Hetzner CLI. Take a
  snapshot **before each bootstrap re-run** for easy rollback. Monthly
  fee is fraction-of-a-cent per GB.

## Backup

At this point the only local state worth backing up is:

- `/opt/quant-system/.env` (secrets, fit on a Post-It).
- `/opt/quant-system/data/` (Parquet cache + model artifacts; re-
  buildable via `scripts/backfill.py` + `scripts/train_regime.py`).
- The `quant` Postgres database (trade log, pnl history — not re-
  buildable). `pg_dump quant > backup.sql` weekly; ship off-VPS.

Formal backup automation lands in Wave 18 alongside monitoring.
