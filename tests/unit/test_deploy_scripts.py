"""Guard rails for deploy/ scripts and systemd units (Wave 17).

These tests catch breakage in production bootstrap from the local dev
loop — we don't have a VPS in CI, but we can lock in script syntax,
safety defaults, and the contract between the unit files and the
Python modules they invoke.
"""

from __future__ import annotations

import shutil
import subprocess
from configparser import ConfigParser, ParsingError
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
BOOTSTRAP = DEPLOY / "bootstrap.sh"
SYSTEMD = DEPLOY / "systemd"


# --- bootstrap.sh ------------------------------------------------------


def test_bootstrap_exists_and_executable() -> None:
    assert BOOTSTRAP.is_file(), "deploy/bootstrap.sh missing"
    assert BOOTSTRAP.stat().st_mode & 0o100, "bootstrap.sh not executable"


def test_bootstrap_has_strict_bash_flags() -> None:
    body = BOOTSTRAP.read_text()
    assert "set -euo pipefail" in body, (
        "bootstrap.sh must start with `set -euo pipefail` so an early failure "
        "halts the script instead of silently corrupting the VPS"
    )


def test_bootstrap_shebang_is_bash() -> None:
    first_line = BOOTSTRAP.read_text().splitlines()[0]
    assert first_line == "#!/usr/bin/env bash"


def test_bootstrap_requires_postgres_password() -> None:
    body = BOOTSTRAP.read_text()
    # The script must refuse to run without POSTGRES_PASSWORD set —
    # a default password would be a security footgun.
    assert "POSTGRES_PASSWORD" in body
    assert 'POSTGRES_PASSWORD=""' in body or "POSTGRES_PASSWORD:-}" in body
    assert "set POSTGRES_PASSWORD" in body or "POSTGRES_PASSWORD in the environment" in body


def test_bootstrap_syntax_is_valid() -> None:
    """`bash -n` parses without executing — catches typos early."""
    bash = shutil.which("bash")
    assert bash is not None, "bash must be on PATH to run this test"
    result = subprocess.run(  # noqa: S603
        [bash, "-n", str(BOOTSTRAP)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def test_bootstrap_enables_firewall() -> None:
    body = BOOTSTRAP.read_text()
    # UFW must be configured with deny-incoming default.
    assert "ufw default deny incoming" in body
    assert "ufw default allow outgoing" in body


def test_bootstrap_enables_fail2ban() -> None:
    body = BOOTSTRAP.read_text()
    assert "fail2ban" in body
    assert "systemctl enable --now fail2ban" in body


def test_bootstrap_sets_timezone_to_new_york() -> None:
    body = BOOTSTRAP.read_text()
    assert "America/New_York" in body
    assert "timedatectl set-timezone" in body


def test_bootstrap_env_file_is_chmod_600() -> None:
    body = BOOTSTRAP.read_text()
    assert "chmod 600" in body, ".env file must be chmod 600 — it contains broker API secrets"


# --- systemd units -----------------------------------------------------


@pytest.mark.parametrize(
    "unit",
    [
        "quant-runner.service",
        "quant-runner.timer",
        "quant-scheduler.service",
    ],
)
def test_systemd_unit_exists(unit: str) -> None:
    assert (SYSTEMD / unit).is_file(), f"{unit} missing from deploy/systemd"


def _load_unit(name: str) -> ConfigParser:
    # systemd unit files are INI-ish but allow duplicate keys. ConfigParser
    # tolerates this when we disable interpolation and use `strict=False`.
    cfg = ConfigParser(strict=False, interpolation=None)
    try:
        cfg.read(SYSTEMD / name)
    except ParsingError as exc:  # pragma: no cover — would fail the test
        pytest.fail(f"systemd unit {name} is not valid INI: {exc}")
    return cfg


def test_runner_service_runs_as_quant_user() -> None:
    cfg = _load_unit("quant-runner.service")
    assert cfg.get("Service", "User") == "quant"
    assert cfg.get("Service", "Group") == "quant"


def test_runner_service_is_oneshot() -> None:
    cfg = _load_unit("quant-runner.service")
    assert cfg.get("Service", "Type") == "oneshot"


def test_runner_service_has_security_hardening() -> None:
    body = (SYSTEMD / "quant-runner.service").read_text()
    # These directives limit blast radius if the Python process is compromised.
    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ProtectKernelModules=true",
        "RestrictSUIDSGID=true",
    ):
        assert directive in body, f"missing {directive} in quant-runner.service"


def test_runner_service_execstart_matches_live_module() -> None:
    cfg = _load_unit("quant-runner.service")
    exec_start = cfg.get("Service", "ExecStart")
    assert "quant.live.runner" in exec_start
    assert "--broker alpaca-paper" in exec_start
    assert "--persist" in exec_start


def test_runner_service_requires_postgres() -> None:
    cfg = _load_unit("quant-runner.service")
    assert "postgresql.service" in cfg.get("Unit", "Requires")
    assert "postgresql.service" in cfg.get("Unit", "After")


def test_runner_timer_fires_mon_fri_at_3_45pm_et() -> None:
    cfg = _load_unit("quant-runner.timer")
    on_calendar = cfg.get("Timer", "OnCalendar")
    assert "Mon..Fri" in on_calendar
    assert "15:45" in on_calendar
    # Inline timezone survives DST without relying on the VPS local TZ.
    assert "America/New_York" in on_calendar


def test_runner_timer_is_persistent() -> None:
    """`Persistent=true` means a cycle missed while the VPS was down
    fires on reboot — important so a 4am-restart doesn't cost a day."""
    cfg = _load_unit("quant-runner.timer")
    assert cfg.getboolean("Timer", "Persistent") is True


def test_scheduler_service_is_long_running() -> None:
    cfg = _load_unit("quant-scheduler.service")
    assert cfg.get("Service", "Type") == "simple"
    assert cfg.get("Service", "Restart") == "on-failure"


def test_scheduler_service_execstart_matches_scheduler_module() -> None:
    cfg = _load_unit("quant-scheduler.service")
    exec_start = cfg.get("Service", "ExecStart")
    assert "quant.live.scheduler" in exec_start


def test_scheduler_service_has_security_hardening() -> None:
    body = (SYSTEMD / "quant-scheduler.service").read_text()
    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
    ):
        assert directive in body, f"missing {directive} in quant-scheduler.service"


def test_bootstrap_installs_every_unit_file() -> None:
    """Whatever .service / .timer files exist under deploy/systemd MUST
    be copied into /etc/systemd/system by the bootstrap. Catches the
    classic bug of adding a new unit file but forgetting to wire it up.
    """
    unit_files = sorted(p.name for p in SYSTEMD.glob("*.service"))
    unit_files += sorted(p.name for p in SYSTEMD.glob("*.timer"))
    body = BOOTSTRAP.read_text()
    for name in unit_files:
        assert name in body, f"bootstrap.sh does not reference {name}"


def test_deploy_readme_exists() -> None:
    assert (DEPLOY / "README.md").is_file()
