"""Tests for Wave 20 go-live deliverables."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from configparser import ConfigParser, ParsingError
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from quant.config import Settings
from quant.live.runner import _build_default_runner

REPO = Path(__file__).resolve().parents[2]


# --- Broker kind: alpaca-live builds a real-money AlpacaBroker --------


def test_alpaca_live_builds_real_money_broker(monkeypatch) -> None:
    """`_build_default_runner(broker_kind='alpaca-live')` should call
    `AlpacaBroker.from_credentials(paper=False)`, i.e. point at the
    non-paper Alpaca endpoint."""
    fake_settings = MagicMock()
    fake_settings.alpaca_api_key = SecretStr("LIVE_KEY")
    fake_settings.alpaca_api_secret = SecretStr("LIVE_SECRET")
    fake_settings.quant_env = "live"
    fake_settings.paper_mode = False
    fake_settings.quant_data_dir = Path("/nonexistent/data")
    fake_settings.discord_webhook_url = None

    # Bypass closes-provider (no cached bars in this test).
    monkeypatch.setattr("quant.live.runner.get_settings", lambda: fake_settings)

    captured: dict[str, object] = {}

    def fake_from_creds(*, api_key, api_secret, paper):
        captured["api_key"] = api_key
        captured["paper"] = paper
        return MagicMock(name="AlpacaBroker")

    monkeypatch.setattr("quant.live.runner.AlpacaBroker.from_credentials", fake_from_creds)

    # The closes_provider isn't called at construction — only at cycle
    # time — so we never actually need cached bars. But the constructor
    # still calls get_sessionmaker() if persist=True; keep persist False.
    _build_default_runner(broker_kind="alpaca-live", dry_run=True, persist=False)

    assert captured["paper"] is False
    assert captured["api_key"] == "LIVE_KEY"


def test_alpaca_live_requires_live_env(monkeypatch) -> None:
    """If `quant_env != 'live'` OR `paper_mode=true`, alpaca-live refuses
    to construct the broker. Belt-and-braces on top of config.py's own
    validator."""
    fake_settings = MagicMock()
    fake_settings.alpaca_api_key = SecretStr("LIVE_KEY")
    fake_settings.alpaca_api_secret = SecretStr("LIVE_SECRET")
    fake_settings.quant_env = "paper"  # mismatch: not live
    fake_settings.paper_mode = False
    fake_settings.quant_data_dir = Path("/nonexistent/data")
    fake_settings.discord_webhook_url = None

    monkeypatch.setattr("quant.live.runner.get_settings", lambda: fake_settings)

    with pytest.raises(ValueError, match="QUANT_ENV=live"):
        _build_default_runner(broker_kind="alpaca-live", dry_run=True, persist=False)


def test_alpaca_live_requires_credentials(monkeypatch) -> None:
    fake_settings = MagicMock()
    fake_settings.alpaca_api_key = None
    fake_settings.alpaca_api_secret = None
    fake_settings.quant_env = "live"
    fake_settings.paper_mode = False
    fake_settings.quant_data_dir = Path("/nonexistent/data")
    fake_settings.discord_webhook_url = None

    monkeypatch.setattr("quant.live.runner.get_settings", lambda: fake_settings)

    with pytest.raises(ValueError, match="ALPACA_API_KEY"):
        _build_default_runner(broker_kind="alpaca-live", dry_run=True, persist=False)


# --- Config.py: live-env guard --------------------------------------


def test_settings_live_env_requires_paper_mode_false() -> None:
    """`Settings(quant_env='live', paper_mode=True, broker_provider='alpaca', ...)`
    must raise. This guard was added in Wave 2 and is validated per-wave."""
    with pytest.raises(ValueError, match="paper_mode"):
        Settings(
            quant_env="live",
            paper_mode=True,
            broker_provider="alpaca",
            alpaca_api_key=SecretStr("K"),  # type: ignore[arg-type]
            alpaca_api_secret=SecretStr("S"),  # type: ignore[arg-type]
        )


def test_settings_live_env_allows_paper_mode_false() -> None:
    s = Settings(
        quant_env="live",
        paper_mode=False,
        broker_provider="alpaca",
        alpaca_api_key=SecretStr("K"),  # type: ignore[arg-type]
        alpaca_api_secret=SecretStr("S"),  # type: ignore[arg-type]
    )
    assert s.quant_env == "live"
    assert s.paper_mode is False


# --- systemd: quant-runner-live.service -----------------------------


SYSTEMD = REPO / "deploy" / "systemd"


def test_live_service_exists() -> None:
    assert (SYSTEMD / "quant-runner-live.service").is_file()


def _load_unit(name: str) -> ConfigParser:
    cfg = ConfigParser(strict=False, interpolation=None)
    try:
        cfg.read(SYSTEMD / name)
    except ParsingError as exc:  # pragma: no cover
        pytest.fail(f"{name} unparseable: {exc}")
    return cfg


def test_live_service_uses_alpaca_live_broker() -> None:
    cfg = _load_unit("quant-runner-live.service")
    exec_start = cfg.get("Service", "ExecStart")
    assert "--broker alpaca-live" in exec_start
    assert "--persist" in exec_start


def test_live_service_runs_preflight_first() -> None:
    """The real-money unit must gate on scripts/preflight_live.py so a
    mis-configured .env can't fire a live cycle.
    """
    cfg = _load_unit("quant-runner-live.service")
    pre = cfg.get("Service", "ExecStartPre")
    assert "preflight_live.py" in pre


def test_live_service_has_hardening() -> None:
    body = (SYSTEMD / "quant-runner-live.service").read_text()
    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
    ):
        assert directive in body


def test_live_service_conflicts_with_paper_service() -> None:
    """Operators should never have both enabled at once — systemd
    enforces this via the `Conflicts=` directive."""
    cfg = _load_unit("quant-runner-live.service")
    conflicts = cfg.get("Unit", "Conflicts")
    assert "quant-runner.service" in conflicts


def test_bootstrap_installs_live_service() -> None:
    body = (REPO / "deploy" / "bootstrap.sh").read_text()
    assert "quant-runner-live.service" in body


# --- preflight_live.py ----------------------------------------------


def _load_preflight():
    path = REPO / "scripts" / "preflight_live.py"
    spec = importlib.util.spec_from_file_location("preflight_live", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["preflight_live"] = module
    spec.loader.exec_module(module)
    return module


def test_preflight_check_live_base_url_paper_rejected() -> None:
    mod = _load_preflight()
    assert mod._check_live_base_url("https://paper-api.alpaca.markets/v2") is False


def test_preflight_check_live_base_url_live_accepted() -> None:
    mod = _load_preflight()
    assert mod._check_live_base_url("https://api.alpaca.markets/v2") is True


# --- Go-live docs ---------------------------------------------------


def test_day_one_retrospective_exists_and_has_sections() -> None:
    body = (REPO / "docs" / "day_one_retrospective.md").read_text().lower()
    for required in (
        "pre-flight",
        "first cycle",
        "deltas from paper",
        "alerts",
        "action items",
        "verdict",
    ):
        assert required in body, f"missing section: {required}"


def test_scaling_plan_covers_three_step_increments() -> None:
    body = (REPO / "docs" / "scaling_plan.md").read_text()
    # Plan explicitly names weeks 20, 22, 26.
    assert "10%" in body
    assert "50%" in body
    assert "100%" in body
    assert "week 22" in body.lower() or "20 + 2" in body.lower()
    assert "week 26" in body.lower() or "20 + 6" in body.lower()


def test_scaling_plan_has_scale_down_triggers() -> None:
    body = (REPO / "docs" / "scaling_plan.md").read_text().lower()
    # The plan warns against "only scaling up" — there must be a
    # documented response to live divergence.
    assert "scale down" in body or "revert" in body
    # Specific PRD §6.1 trip wires should appear.
    assert "-15%" in body or "15%" in body


def test_gate4_checklist_enumerates_plan_acceptance() -> None:
    body = (REPO / "docs" / "gate4_checklist.md").read_text().lower()
    # Plan says "5 trading days with no manual interventions". Both
    # phrases must appear somewhere.
    assert "5 trading days" in body or "5 days" in body
    assert "no manual intervention" in body or "zero" in body
    # Sign-off + GO/NO-GO slot must be present.
    assert "go" in body and "no-go" in body


def test_gate4_checklist_includes_dr_rehearsal() -> None:
    body = (REPO / "docs" / "gate4_checklist.md").read_text().lower()
    assert "disaster_recovery" in body or "dr drill" in body or "scenario 2" in body


# --- Makefile targets ------------------------------------------------


def test_makefile_has_live_targets() -> None:
    body = (REPO / "Makefile").read_text()
    for target in (
        "preflight-live:",
        "live-dry:",
        "live-run:",
        "live-switch:",
        "paper-switch:",
    ):
        assert target in body, f"missing Makefile target: {target}"


def test_makefile_live_switch_runs_preflight_first() -> None:
    body = (REPO / "Makefile").read_text()
    # Find the live-switch target block and confirm it invokes preflight.
    match = re.search(
        r"live-switch:.*?(?=^[A-Za-z_][A-Za-z0-9_-]*:)", body, re.DOTALL | re.MULTILINE
    )
    assert match is not None
    block = match.group(0)
    assert "preflight-live" in block


# --- End-to-end: runner CLI accepts alpaca-live -------------------


def test_runner_cli_accepts_alpaca_live_choice() -> None:
    """The argparse layer must list `alpaca-live` as a valid --broker.
    Catches regressions where someone adds the broker kind to the
    function but forgets the CLI choice."""
    result = subprocess.run(
        [sys.executable, "-m", "quant.live.runner", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "alpaca-live" in result.stdout


def test_scheduler_cli_accepts_alpaca_live_choice() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "quant.live.scheduler", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "alpaca-live" in result.stdout
