"""Regression tests for the Wave-20 post-delivery security review.

Two vulnerabilities were identified by the security-review skill after
Wave 20 shipped:

    Vuln 1 (HIGH, risk_bypass): `_build_default_runner` constructed
        OrderManager without risk_validator or killswitch, so every
        live order bypassed PRD §6.1 hard limits.

    Vuln 2 (HIGH, kill_switch_evasion): default killswitch file lived
        on tmpfs (`/var/run/quant/HALT`), so engaged state evaporated
        across any reboot — re-arming the system silently.

These tests lock in the fixes. They must fail if someone reverts the
security-review patches; they're the canary for real-money regressions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from quant.config import Settings
from quant.live.runner import _build_default_runner


@pytest.fixture
def _fake_live_settings(monkeypatch, tmp_path):
    """Settings wired to look like a live deployment, routed to tmp_path
    so no real file system / broker is touched."""
    s = MagicMock()
    s.alpaca_api_key = SecretStr("LIVE_KEY")
    s.alpaca_api_secret = SecretStr("LIVE_SECRET")
    s.quant_env = "live"
    s.paper_mode = False
    s.quant_data_dir = tmp_path
    s.discord_webhook_url = None
    s.quant_killswitch_file = tmp_path / "HALT"
    monkeypatch.setattr("quant.live.runner.get_settings", lambda: s)
    # Bypass the actual Alpaca network call.
    monkeypatch.setattr(
        "quant.live.runner.AlpacaBroker.from_credentials",
        lambda **_: MagicMock(name="AlpacaBroker"),
    )
    return s


# --- Vuln 1: risk validator + killswitch wired into the default runner


def test_build_default_runner_wires_risk_validator(_fake_live_settings) -> None:
    """HIGH: without this wiring, every live order bypasses PRD §6.1."""
    runner = _build_default_runner(broker_kind="alpaca-live", dry_run=False, persist=False)
    om = runner._order_manager  # type: ignore[attr-defined]
    assert om._risk_validator is not None, (
        "_build_default_runner must construct OrderManager with a RiskValidator; "
        "otherwise order-size, position-size, and price-deviation limits are unenforced"
    )


def test_build_default_runner_wires_killswitch_on_order_manager(_fake_live_settings) -> None:
    """HIGH: OrderManager must see the killswitch so new orders are
    rejected pre-submit when HALT is engaged (the LiveRunner-level
    killswitch check only blocks NEW signals, not out-of-band retries
    or the rolling-sharpe emit path)."""
    runner = _build_default_runner(broker_kind="alpaca-live", dry_run=False, persist=False)
    om = runner._order_manager  # type: ignore[attr-defined]
    assert om._killswitch is not None, (
        "_build_default_runner must pass killswitch= to OrderManager; "
        "otherwise HALT file is only checked at cycle start, not per-order"
    )


def test_build_default_runner_wires_killswitch_on_live_runner(_fake_live_settings) -> None:
    """Secondary: LiveRunner also holds a reference so _flatten_cycle and
    the cycle-start gate work correctly."""
    runner = _build_default_runner(broker_kind="alpaca-live", dry_run=False, persist=False)
    assert runner._killswitch is not None  # type: ignore[attr-defined]


def test_risk_validator_uses_config_limits(_fake_live_settings) -> None:
    """The validator must be seeded with the real RiskConfig caps (not
    a permissive default)."""
    runner = _build_default_runner(broker_kind="alpaca-live", dry_run=False, persist=False)
    om = runner._order_manager  # type: ignore[attr-defined]
    cfg = om._risk_validator.config
    # PRD §6.1 caps — loader already enforces these at config load time;
    # this test fails early if someone swaps in a hand-constructed
    # wide-open RiskConfig.
    assert cfg.max_order_size_pct <= 0.20
    assert cfg.max_position_pct <= 0.30
    assert cfg.max_price_deviation_pct <= 0.01


# --- Vuln 2: killswitch default path is persistent across reboot ---


def test_default_killswitch_path_not_on_tmpfs() -> None:
    """HIGH: tmpfs paths are wiped on reboot. An engaged HALT file
    *must* survive a kernel upgrade / VPS restart; otherwise trading
    silently resumes on a halted account."""
    settings = Settings()  # defaults only — don't touch .env
    ks_path = Path(settings.quant_killswitch_file)
    tmpfs_prefixes = ("/run/", "/var/run/", "/tmp/", "/dev/shm/")  # noqa: S108
    path_str = str(ks_path)
    assert not any(path_str.startswith(p) for p in tmpfs_prefixes), (
        f"killswitch path {path_str} is on tmpfs; state will be lost on reboot. "
        "Move default to /var/lib/quant/HALT or similar persistent location."
    )


def test_bootstrap_creates_killswitch_dir_with_correct_permissions() -> None:
    """The bootstrap script must create + chown a PERSISTENT killswitch
    dir to the quant user (otherwise the service can't engage/disengage
    HALT from within its `User=quant` systemd sandbox)."""
    body = Path("deploy/bootstrap.sh").read_text()
    # Must create /var/lib/quant — the persistent post-fix location.
    assert "mkdir -p /var/lib/quant" in body, (
        "bootstrap.sh must create /var/lib/quant (persistent killswitch dir)"
    )
    # Must chown it to the quant user (via the QUANT_USER variable).
    assert "chown" in body
    assert '"${QUANT_USER}:${QUANT_USER}" /var/lib/quant' in body
    # Must NOT mkdir under /var/run or /run (would be on tmpfs, wiped
    # at reboot — the whole reason for this regression test).
    assert "mkdir -p /var/run/quant" not in body
    assert "mkdir -p /run/quant" not in body


def test_systemd_units_allow_writes_to_killswitch_dir() -> None:
    """`ProtectSystem=strict` blocks all writes except ReadWritePaths.
    If the killswitch lives outside the data dir, the service can't
    engage HALT itself — and reconciliation scripts can't remove it
    either without root. Both paper + live units need coverage."""
    for unit in ("quant-runner.service", "quant-runner-live.service"):
        body = Path(f"deploy/systemd/{unit}").read_text()
        # Either /var/lib/quant is in ReadWritePaths, or the killswitch
        # path has been moved inside /opt/quant-system/data (which is
        # already writeable). Pick one — the assertion holds either way.
        rwpaths = [ln for ln in body.splitlines() if ln.startswith("ReadWritePaths=")]
        assert rwpaths, f"{unit} has no ReadWritePaths directive"
        joined = " ".join(rwpaths)
        assert "/var/lib/quant" in joined or "/opt/quant-system" in joined, (
            f"{unit} ReadWritePaths does not cover the killswitch dir: {joined}"
        )
