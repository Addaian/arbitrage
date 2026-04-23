#!/usr/bin/env bash
# One-command VPS bootstrap for the quant-system paper deployment.
# Target: Ubuntu 24.04 LTS on Hetzner CX22 (ARM) or CPX21 (x86).
#
# Acceptance (plan Week 17): cold-booted VPS → running scheduler in <30 min.
#
# Usage (as root on the fresh VPS):
#
#   wget -O bootstrap.sh https://raw.githubusercontent.com/Addaian/arbitrage/main/deploy/bootstrap.sh
#   chmod +x bootstrap.sh
#   ./bootstrap.sh
#
# The script is idempotent — safe to re-run. Steps that have already
# taken effect (apt installs, user creation, firewall rules, systemd
# unit enable) short-circuit cleanly. Re-running on a live host
# updates the repo and restarts services.

set -euo pipefail

# ---------- Config (override via env before invocation) ----------------

REPO_URL="${REPO_URL:-https://github.com/Addaian/arbitrage.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/quant-system}"
QUANT_USER="${QUANT_USER:-quant}"
POSTGRES_DB="${POSTGRES_DB:-quant}"
POSTGRES_USER="${POSTGRES_USER:-quant}"
# A password is required for Postgres even when peer-authing locally —
# the app uses a TCP connection with md5 auth.
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
DEPLOY_SYSTEMD="${DEPLOY_SYSTEMD:-timer}"  # one of: timer | scheduler

# ---------- Pre-flight -------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root (or via sudo)." >&2
    exit 1
fi

if ! grep -q '^ID=ubuntu' /etc/os-release; then
    echo "WARNING: this script targets Ubuntu; /etc/os-release says otherwise." >&2
fi

if [[ -z "$POSTGRES_PASSWORD" ]]; then
    echo "ERROR: set POSTGRES_PASSWORD in the environment before running." >&2
    echo "  export POSTGRES_PASSWORD='...'" >&2
    exit 1
fi

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ---------- 1. Base packages + timezone --------------------------------

log "apt: update + upgrade"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

log "apt: installing base packages"
apt-get install -y -qq \
    build-essential \
    ca-certificates \
    curl \
    fail2ban \
    git \
    libssl-dev \
    postgresql \
    postgresql-contrib \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    ufw \
    unattended-upgrades

log "timezone: set to America/New_York (matches PRD §4.2 schedule)"
timedatectl set-timezone America/New_York

# ---------- 2. Unattended security upgrades ---------------------------

log "unattended-upgrades: enabling security channel"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades.service

# ---------- 3. UFW (SSH + Grafana only, deny everything else) ----------

log "ufw: resetting + configuring firewall"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
# Grafana (Wave 18). The HTTP port itself is allowed; restrict by source
# IP post-install if the VPS has a public IP.
ufw allow 3000/tcp comment 'Grafana (Wave 18)'
ufw --force enable

# ---------- 4. fail2ban (SSH brute-force protection) ------------------

log "fail2ban: enable"
systemctl enable --now fail2ban

# ---------- 5. uv (package manager) -----------------------------------

if ! command -v uv >/dev/null 2>&1; then
    log "uv: installing to /usr/local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
else
    log "uv: already installed ($(uv --version))"
fi

# ---------- 6. Postgres: create user + db + enable md5 -----------------

log "postgres: ensure user/db exist"
# Use `psql -tc` to check idempotently.
if ! sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';"
else
    # Reset password in case the operator rotated it.
    sudo -u postgres psql -c "ALTER ROLE ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';"
fi
if ! sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"
fi
# Enable TimescaleDB extension if available (optional; the app tolerates its absence).
sudo -u postgres psql -d "${POSTGRES_DB}" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" || \
    log "timescaledb: extension unavailable — continuing without it"

systemctl enable --now postgresql

# ---------- 7. quant user + install dir -------------------------------

if ! id -u "${QUANT_USER}" >/dev/null 2>&1; then
    log "user: creating ${QUANT_USER}"
    useradd --system --home "${INSTALL_DIR}" --shell /bin/bash "${QUANT_USER}"
else
    log "user: ${QUANT_USER} already exists"
fi

mkdir -p "${INSTALL_DIR}"
chown -R "${QUANT_USER}:${QUANT_USER}" "${INSTALL_DIR}"

# ---------- 8. Clone / pull repo --------------------------------------

if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "repo: pulling latest ${REPO_BRANCH}"
    sudo -u "${QUANT_USER}" git -C "${INSTALL_DIR}" fetch --quiet
    sudo -u "${QUANT_USER}" git -C "${INSTALL_DIR}" reset --hard "origin/${REPO_BRANCH}"
else
    log "repo: cloning ${REPO_URL} @ ${REPO_BRANCH}"
    sudo -u "${QUANT_USER}" git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
fi

# ---------- 9. uv sync (creates .venv, installs deps) -----------------

log "uv: syncing project deps"
sudo -u "${QUANT_USER}" bash -c "cd ${INSTALL_DIR} && /usr/local/bin/uv sync --extra dev"

# ---------- 10. .env template (operator fills in secrets) -------------

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    log ".env: writing template (edit in secrets before starting the timer)"
    cat >"${INSTALL_DIR}/.env" <<EOF
# quant-system runtime config. Populate secrets before enabling the timer.
QUANT_ENV=paper
BROKER_PROVIDER=alpaca
PAPER_MODE=true
ALPACA_API_KEY=REPLACE_ME
ALPACA_API_SECRET=REPLACE_ME
ALPACA_BASE_URL=https://paper-api.alpaca.markets
DATABASE_URL=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}
DISCORD_WEBHOOK_URL=
SENTRY_DSN=
SENTRY_ENVIRONMENT=paper
QUANT_DATA_DIR=${INSTALL_DIR}/data
QUANT_KILLSWITCH_FILE=/var/lib/quant/HALT
EOF
    chown "${QUANT_USER}:${QUANT_USER}" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
else
    log ".env: already present — leaving alone"
fi

# Killswitch dir for the quant user. Must be PERSISTENT (not tmpfs) —
# `/var/run` / `/run` is tmpfs on Ubuntu, so a HALT file there would
# evaporate on reboot and trading would silently resume on an account
# the operator had deliberately halted.
mkdir -p /var/lib/quant
chown "${QUANT_USER}:${QUANT_USER}" /var/lib/quant
chmod 0750 /var/lib/quant

# ---------- 11. Alembic migrations -----------------------------------

log "alembic: upgrade head"
sudo -u "${QUANT_USER}" bash -c "cd ${INSTALL_DIR} && /usr/local/bin/uv run alembic upgrade head"

# ---------- 12. systemd units ----------------------------------------

log "systemd: installing units (${DEPLOY_SYSTEMD} mode)"
install -m 0644 "${INSTALL_DIR}/deploy/systemd/quant-runner.service" /etc/systemd/system/
install -m 0644 "${INSTALL_DIR}/deploy/systemd/quant-runner.timer" /etc/systemd/system/
install -m 0644 "${INSTALL_DIR}/deploy/systemd/quant-scheduler.service" /etc/systemd/system/
# Live-mode unit ships but stays disabled until the operator flips
# QUANT_ENV=live in .env and swaps enables via `make live-switch`.
install -m 0644 "${INSTALL_DIR}/deploy/systemd/quant-runner-live.service" /etc/systemd/system/
systemctl daemon-reload

case "${DEPLOY_SYSTEMD}" in
    timer)
        # Timer fires the oneshot service at 3:45pm ET Mon-Fri.
        systemctl disable --now quant-scheduler.service 2>/dev/null || true
        systemctl enable --now quant-runner.timer
        log "systemd: quant-runner.timer enabled"
        systemctl list-timers --no-pager quant-runner.timer | head -3
        ;;
    scheduler)
        # Long-running APScheduler daemon handles the cron itself.
        systemctl disable --now quant-runner.timer 2>/dev/null || true
        systemctl enable --now quant-scheduler.service
        log "systemd: quant-scheduler.service enabled"
        systemctl status --no-pager quant-scheduler.service | head -5
        ;;
    *)
        echo "ERROR: DEPLOY_SYSTEMD must be 'timer' or 'scheduler', got ${DEPLOY_SYSTEMD}" >&2
        exit 1
        ;;
esac

# ---------- 13. Final handoff ----------------------------------------

cat <<EOF

[bootstrap done]
  VPS    : $(hostname) ($(hostnamectl --static 2>/dev/null || echo 'unknown'))
  TZ     : $(timedatectl show -p Timezone --value)
  User   : ${QUANT_USER}
  Dir    : ${INSTALL_DIR}
  Mode   : ${DEPLOY_SYSTEMD}
  DB     : ${POSTGRES_DB} on localhost:5432 as ${POSTGRES_USER}

Next steps:
  1. Fill in Alpaca / Discord secrets in ${INSTALL_DIR}/.env  (chmod 600 already).
  2. Restart the timer/service so the new env takes effect:
       systemctl restart quant-runner.timer      # timer mode
       systemctl restart quant-scheduler.service # scheduler mode
  3. Tail logs:
       journalctl -u quant-runner.service -f     # per-cycle logs
       journalctl -u quant-scheduler.service -f  # scheduler mode
  4. Smoke test a single cycle by hand:
       sudo -u ${QUANT_USER} bash -c 'cd ${INSTALL_DIR} && uv run python -m quant.live.runner --broker alpaca-paper --dry-run'
EOF
