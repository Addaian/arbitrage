"""Tests for the monitoring layer (Wave 18).

Covers:
    - `quant.monitoring.metrics` — registry + record helpers + label
      lifecycle.
    - `quant.monitoring.sentry` — init is a no-op without a DSN.
    - `quant.live.notifier.AlertSeverity` — severity-tagged Discord
      alert renders with the right emoji prefix.
    - Prometheus / Alertmanager / alerts.yml configuration files parse
      and implement the PRD §6.3 alert catalogue.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from quant.live.notifier import AlertSeverity, DiscordNotifier
from quant.monitoring import (
    record_cycle_error,
    record_cycle_success,
    record_killswitch_state,
    record_order_submit,
    registry,
    reset_metrics_registry,
    set_position_values,
)
from quant.monitoring.metrics import metric
from quant.monitoring.sentry import init_sentry

DEPLOY_PROM = Path(__file__).resolve().parents[2] / "deploy" / "prometheus"


# --- Metrics registry -------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_metrics() -> None:
    """Reset the process-global Prometheus registry between tests —
    otherwise Counter increments bleed across test cases."""
    reset_metrics_registry()


def _sample(metric_name: str, labels: dict[str, str] | None = None) -> float | None:
    """Look up the latest sample for a metric by name."""
    for family in registry().collect():
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if labels is None or all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return None


def test_record_cycle_success_sets_equity_cash_positions() -> None:
    record_cycle_success(equity=100_000.0, cash=20_000.0, position_count=3, duration_seconds=1.2)
    assert _sample("quant_equity_usd") == 100_000.0
    assert _sample("quant_cash_usd") == 20_000.0
    assert _sample("quant_position_count") == 3.0
    # Heartbeat is a unix timestamp; just assert it's been set.
    heartbeat = _sample("quant_heartbeat_seconds")
    assert heartbeat is not None and heartbeat > 0


def test_record_cycle_success_observes_duration_histogram() -> None:
    record_cycle_success(equity=1.0, cash=1.0, position_count=0, duration_seconds=2.0)
    # _count sample on the histogram tracks number of observations.
    count = _sample("quant_cycle_duration_seconds_count")
    assert count == 1.0


def test_record_cycle_error_increments_counter() -> None:
    record_cycle_error()
    record_cycle_error()
    assert _sample("quant_cycle_errors_total") == 2.0


def test_record_killswitch_state_toggles_gauge() -> None:
    record_killswitch_state(True)
    assert _sample("quant_killswitch_engaged") == 1.0
    record_killswitch_state(False)
    assert _sample("quant_killswitch_engaged") == 0.0


def test_record_order_submit_labels_correctly() -> None:
    record_order_submit(strategy="trend", side="buy", result="filled", latency_seconds=0.3)
    record_order_submit(strategy="trend", side="buy", result="filled", latency_seconds=0.4)
    count = _sample(
        "quant_order_submit_total",
        {"strategy": "trend", "side": "buy", "result": "filled"},
    )
    assert count == 2.0


def test_set_position_values_vector_gauge() -> None:
    set_position_values({"SPY": 5000.0, "QQQ": 3000.0})
    assert _sample("quant_position_value_usd", {"symbol": "SPY"}) == 5000.0
    assert _sample("quant_position_value_usd", {"symbol": "QQQ"}) == 3000.0


def test_unknown_metric_name_raises() -> None:
    with pytest.raises(KeyError, match="unknown metric"):
        metric("does_not_exist")


# --- Sentry initialisation -------------------------------------------


def test_sentry_init_is_noop_without_dsn() -> None:
    settings = MagicMock()
    settings.sentry_dsn = None
    assert init_sentry(settings) is False


def test_sentry_init_is_noop_on_empty_dsn() -> None:
    settings = MagicMock()
    settings.sentry_dsn = "   "
    assert init_sentry(settings) is False


# --- Discord alert severity ------------------------------------------


def test_alert_severity_enum_values() -> None:
    assert AlertSeverity.INFO.value == "info"
    assert AlertSeverity.WARNING.value == "warning"
    assert AlertSeverity.CRITICAL.value == "critical"


def test_alert_silently_no_ops_without_webhook() -> None:
    notifier = DiscordNotifier(webhook_url=None)
    # Should not raise even though there's no webhook.
    notifier.alert(AlertSeverity.CRITICAL, "test alert", details="context")


def test_alert_builds_severity_prefixed_message(monkeypatch) -> None:
    """With a webhook set, `alert()` calls DiscordWebhook with a
    severity-prefixed body. We monkeypatch the webhook class and
    capture the `content` kwarg."""
    captured: dict[str, object] = {}

    class _FakeWebhook:
        def __init__(self, *, url: str, content: str, timeout: int) -> None:
            captured["url"] = url
            captured["content"] = content
            captured["timeout"] = timeout

        def execute(self) -> None:
            captured["executed"] = True

    monkeypatch.setattr("quant.live.notifier.DiscordWebhook", _FakeWebhook)
    notifier = DiscordNotifier(webhook_url="https://discord/webhook")
    notifier.alert(AlertSeverity.CRITICAL, "kill-switch engaged", details="daily loss -5.1%")

    content = captured.get("content")
    assert isinstance(content, str)
    assert ":rotating_light:" in content
    assert "CRITICAL" in content
    assert "kill-switch engaged" in content
    assert "daily loss -5.1%" in content


# --- Config files --------------------------------------------------


def test_prometheus_yml_parses_and_scrapes_runner() -> None:
    path = DEPLOY_PROM / "prometheus.yml"
    assert path.is_file()
    cfg = yaml.safe_load(path.read_text())
    jobs = {j["job_name"] for j in cfg["scrape_configs"]}
    assert "quant-runner" in jobs
    runner_job = next(j for j in cfg["scrape_configs"] if j["job_name"] == "quant-runner")
    targets = runner_job["static_configs"][0]["targets"]
    # Runner exporter port must match `start_exporter(port=9000)` default.
    assert any(":9000" in t for t in targets)


def test_alertmanager_yml_routes_to_discord_webhook() -> None:
    path = DEPLOY_PROM / "alertmanager.yml"
    assert path.is_file()
    cfg = yaml.safe_load(path.read_text())
    receivers = {r["name"]: r for r in cfg["receivers"]}
    assert "discord" in receivers
    assert "webhook_configs" in receivers["discord"]
    # URL comes from env substitution — alertmanager resolves it at runtime.
    assert "DISCORD_WEBHOOK_URL" in receivers["discord"]["webhook_configs"][0]["url"]


def test_alerts_yml_covers_prd_6_3() -> None:
    """PRD §6.3 enumerates 7 alert conditions. Every one must have a
    corresponding rule — identified via the `prd_row` label — so that
    adding a new row to the PRD surfaces a missing rule in CI.
    """
    path = DEPLOY_PROM / "alerts.yml"
    cfg = yaml.safe_load(path.read_text())
    rules = [r for g in cfg["groups"] for r in g["rules"]]
    prd_rows = {int(r["labels"]["prd_row"]) for r in rules if "prd_row" in r.get("labels", {})}
    # PRD §6.3 has 7 rows (order rejected, -3%, -5%, tracking err, no heartbeat, exception, NTP).
    assert prd_rows >= {1, 2, 3, 4, 5, 6, 7}, (
        f"missing PRD §6.3 rows: {sorted({1, 2, 3, 4, 5, 6, 7} - prd_rows)}"
    )


def test_alerts_yml_daily_loss_critical_matches_prd_5pct() -> None:
    path = DEPLOY_PROM / "alerts.yml"
    cfg = yaml.safe_load(path.read_text())
    rules = [r for g in cfg["groups"] for r in g["rules"]]
    daily_crit = next(r for r in rules if r["alert"] == "DailyLossCritical")
    # -5% threshold per PRD §6.1.
    assert "-0.05" in daily_crit["expr"]
    assert daily_crit["labels"]["severity"] == "critical"


def test_docker_compose_stack_parses() -> None:
    path = DEPLOY_PROM / "docker-compose.yml"
    assert path.is_file()
    cfg = yaml.safe_load(path.read_text())
    services = set(cfg["services"].keys())
    assert services == {"prometheus", "alertmanager", "grafana"}
    # Prometheus must mount alerts.yml so Alertmanager can fire.
    prom_volumes = cfg["services"]["prometheus"]["volumes"]
    assert any("alerts.yml" in v for v in prom_volumes)


def test_grafana_datasource_provisioned() -> None:
    path = DEPLOY_PROM / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    assert path.is_file()
    cfg = yaml.safe_load(path.read_text())
    ds = cfg["datasources"][0]
    assert ds["type"] == "prometheus"
    assert ds["url"] == "http://prometheus:9090"


def test_grafana_dashboard_has_expected_panels() -> None:
    path = DEPLOY_PROM / "grafana" / "dashboards" / "quant-system.json"
    dash = json.loads(path.read_text())
    titles = {p["title"] for p in dash["panels"]}
    # Must cover the four plan-required panel categories.
    assert "Equity curve" in titles
    assert any("Per-symbol" in t for t in titles)
    assert any("Sharpe" in t for t in titles)
    assert "Killswitch" in titles
