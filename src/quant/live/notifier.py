"""Discord webhook notifier for the daily cycle + PRD §6.3 alerts.

Wraps `discord-webhook` with a tiny, async-friendly surface. All methods
silently no-op when no webhook URL is configured — that's the default
in dev and unit tests, so nothing accidentally spams a channel.

Failures to post (HTTP errors, network drops) are logged but NEVER
raised: a monitoring problem must not block the trading cycle.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from discord_webhook import DiscordWebhook
from loguru import logger

if TYPE_CHECKING:
    from quant.live.runner import CycleResult


class AlertSeverity(StrEnum):
    """Alert severity per PRD §6.3. Maps to the Discord emoji prefix
    so on-call can triage at a glance from a phone notification.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


_SEVERITY_PREFIX: dict[AlertSeverity, str] = {
    AlertSeverity.INFO: ":information_source:",
    AlertSeverity.WARNING: ":warning:",
    AlertSeverity.CRITICAL: ":rotating_light:",
}


class DiscordNotifier:
    def __init__(self, webhook_url: str | None) -> None:
        self._url = webhook_url

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def cycle_start(self, strategy: str, now: datetime) -> None:
        self._send(
            f":arrow_forward: cycle start · {strategy} · {now.isoformat(timespec='seconds')}"
        )

    def cycle_complete(self, strategy: str, result: CycleResult) -> None:
        lines = [
            f":white_check_mark: cycle complete · {strategy}"
            + ("  (dry-run)" if result.dry_run else ""),
            f"orders submitted: {len(result.submitted_orders)}",
            f"positions: {len(result.final_positions)}",
        ]
        if result.drift:
            lines.append(f":warning:  drift on {len(result.drift)} symbols")
        self._send("\n".join(lines))

    def cycle_error(self, strategy: str, message: str) -> None:
        self._send(f":red_circle: cycle error · {strategy}\n`{message}`")

    def alert(
        self,
        severity: AlertSeverity,
        title: str,
        *,
        details: str | None = None,
    ) -> None:
        """Send a severity-tagged alert (PRD §6.3). Used for events
        outside the cycle-start/complete/error flow — daily-loss
        warnings, kill-switch engagement, NTP drift, etc.
        """
        prefix = _SEVERITY_PREFIX[severity]
        body = f"{prefix} **{severity.value.upper()}** · {title}"
        if details:
            body = f"{body}\n```{details}```"
        self._send(body)

    def _send(self, content: str) -> None:
        if not self._url:
            return
        try:
            hook = DiscordWebhook(url=self._url, content=content, timeout=5)
            hook.execute()
        except Exception as exc:
            # Alerting is best-effort. Do not let a webhook failure kill a
            # trading cycle.
            logger.warning("discord notifier failed: {}", exc)
