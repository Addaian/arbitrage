"""Sentry initialisation (PRD §6.3).

Thin wrapper over `sentry-sdk` that reads `sentry_dsn` + `sentry_environment`
from `Settings`. No-op when `sentry_dsn` is unset — the dev loop and
unit tests never ship events.

Integrations are intentionally minimal: the runner wraps each cycle in
try/except and calls `capture_exception()` directly. We don't install
the logging integration (too chatty) or the asyncio integration
(false-positive noise on short-lived tasks).
"""

from __future__ import annotations

import sentry_sdk

from quant.config import Settings


def init_sentry(settings: Settings) -> bool:
    """Initialise the global Sentry hub if a DSN is configured.

    Returns True if Sentry was actually initialised, False otherwise
    (either no DSN or re-called — second calls no-op to avoid swapping
    the global hub under the runner's feet).
    """
    if settings.sentry_dsn is None or not settings.sentry_dsn.strip():
        return False
    hub = sentry_sdk.Hub.current
    if hub.client is not None:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        # Send unhandled exceptions only; skip logging breadcrumbs to keep
        # the event stream signal-rich.
        default_integrations=False,
        traces_sample_rate=0.0,
    )
    return True


def capture_cycle_exception(exc: BaseException) -> None:
    """Convenience wrapper: forward an exception to Sentry if active."""
    sentry_sdk.capture_exception(exc)
