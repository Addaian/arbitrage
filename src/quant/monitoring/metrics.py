"""Prometheus metric definitions (PRD §6.3 / plan Week 18).

Single source of truth for metric *names* so Grafana dashboards and
alert rules don't drift from the emitter. Every module in the runner
pulls its metric handles from this module; nothing else constructs
Prometheus primitives directly.

Metric families:

* **equity / P&L** — `quant_equity_usd`, `quant_cash_usd`,
  `quant_daily_return`, `quant_rolling_30d_sharpe`.
* **positions** — `quant_position_count`,
  `quant_position_value_usd{symbol="..."}`.
* **orders** — `quant_order_submit_total{strategy,side,result}`,
  `quant_order_latency_seconds{strategy}` (histogram).
* **cycle health** — `quant_cycle_duration_seconds`,
  `quant_cycle_errors_total`, `quant_heartbeat_seconds` (Unix time of
  last successful cycle complete).

The Prometheus registry is a process-global singleton. Tests reset
metric state via `reset_metrics_registry()` so parallel test runs
don't bleed into each other.
"""

from __future__ import annotations

import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

# ---- Registry + metric constructors ----------------------------------


def _build_registry() -> tuple[CollectorRegistry, dict[str, object]]:
    """Construct a fresh registry + every metric. Pulled out so
    `reset_metrics_registry()` can rebuild during tests without
    relying on module-reimport tricks.
    """
    registry = CollectorRegistry()
    metrics: dict[str, object] = {
        "equity_usd": Gauge(
            "quant_equity_usd",
            "Account equity in USD at cycle completion.",
            registry=registry,
        ),
        "cash_usd": Gauge(
            "quant_cash_usd",
            "Uninvested cash in USD at cycle completion.",
            registry=registry,
        ),
        "daily_return": Gauge(
            "quant_daily_return",
            "Fractional daily return (e.g. 0.01 = 1%).",
            registry=registry,
        ),
        "rolling_30d_sharpe": Gauge(
            "quant_rolling_30d_sharpe",
            "Annualized Sharpe over the trailing 30 days.",
            registry=registry,
        ),
        "position_count": Gauge(
            "quant_position_count",
            "Number of non-zero positions at cycle completion.",
            registry=registry,
        ),
        "position_value_usd": Gauge(
            "quant_position_value_usd",
            "Market value of each open position in USD.",
            ["symbol"],
            registry=registry,
        ),
        "order_submit_total": Counter(
            "quant_order_submit_total",
            "Orders submitted, by strategy / side / outcome.",
            ["strategy", "side", "result"],
            registry=registry,
        ),
        "order_latency_seconds": Histogram(
            "quant_order_latency_seconds",
            "Wall-clock seconds from submit to terminal status.",
            ["strategy"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0),
            registry=registry,
        ),
        "cycle_duration_seconds": Histogram(
            "quant_cycle_duration_seconds",
            "Wall-clock seconds for a full daily cycle.",
            buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
            registry=registry,
        ),
        "cycle_errors_total": Counter(
            "quant_cycle_errors_total",
            "Cycles that raised an exception before completing.",
            registry=registry,
        ),
        "heartbeat_seconds": Gauge(
            "quant_heartbeat_seconds",
            "Unix timestamp of the last successful cycle completion.",
            registry=registry,
        ),
        "killswitch_engaged": Gauge(
            "quant_killswitch_engaged",
            "1 if the file-sentinel kill-switch is engaged, 0 otherwise.",
            registry=registry,
        ),
    }
    return registry, metrics


_REGISTRY, _METRICS = _build_registry()


def registry() -> CollectorRegistry:
    """Return the process-wide Prometheus registry."""
    return _REGISTRY


def metric(name: str) -> object:
    """Look up a metric by short name. Raises on unknown names —
    callers can't silently drift from the catalogue above.
    """
    if name not in _METRICS:
        raise KeyError(f"unknown metric {name!r}; defined: {sorted(_METRICS)}")
    return _METRICS[name]


def reset_metrics_registry() -> None:
    """Test helper — rebuild the registry + metrics, clearing all state."""
    global _REGISTRY, _METRICS
    _REGISTRY, _METRICS = _build_registry()


# ---- HTTP exporter ---------------------------------------------------


def start_exporter(port: int = 9000) -> None:
    """Start an HTTP /metrics endpoint bound to `0.0.0.0:port`.

    Idempotent-ish: `prometheus_client.start_http_server` binds a new
    port each call; only the LiveRunner daemon entrypoint should call
    this. One-shot runners export nothing — the cycle's rows end up in
    Postgres, which is the source of truth there.
    """
    start_http_server(port, registry=_REGISTRY)


# ---- Small helpers for callers ---------------------------------------


def record_cycle_success(
    *, equity: float, cash: float, position_count: int, duration_seconds: float
) -> None:
    """Update all cycle-completion metrics in one call. Keeps the
    LiveRunner emitting-code focused on its control flow instead of
    per-metric plumbing.
    """
    m_equity = metric("equity_usd")
    m_cash = metric("cash_usd")
    m_count = metric("position_count")
    m_duration = metric("cycle_duration_seconds")
    m_heartbeat = metric("heartbeat_seconds")
    m_equity.set(equity)  # type: ignore[attr-defined]
    m_cash.set(cash)  # type: ignore[attr-defined]
    m_count.set(position_count)  # type: ignore[attr-defined]
    m_duration.observe(duration_seconds)  # type: ignore[attr-defined]
    m_heartbeat.set(time.time())  # type: ignore[attr-defined]


def record_cycle_error() -> None:
    counter = metric("cycle_errors_total")
    counter.inc()  # type: ignore[attr-defined]


def record_killswitch_state(engaged: bool) -> None:
    g = metric("killswitch_engaged")
    g.set(1 if engaged else 0)  # type: ignore[attr-defined]


def record_order_submit(*, strategy: str, side: str, result: str, latency_seconds: float) -> None:
    counter = metric("order_submit_total")
    counter.labels(strategy=strategy, side=side, result=result).inc()  # type: ignore[attr-defined]
    hist = metric("order_latency_seconds")
    hist.labels(strategy=strategy).observe(latency_seconds)  # type: ignore[attr-defined]


def set_position_values(values_by_symbol: dict[str, float]) -> None:
    """Replace the per-symbol position-value gauge vector.

    Prometheus labels aren't automatically garbage-collected when a
    symbol disappears — callers must explicitly zero out closed
    positions. This helper does the bookkeeping.
    """
    g = metric("position_value_usd")
    for symbol, value in values_by_symbol.items():
        g.labels(symbol=symbol).set(value)  # type: ignore[attr-defined]
