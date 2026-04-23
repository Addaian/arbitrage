"""Pydantic-backed configuration.

Two layers:

1. `Settings` — 12-factor-style secrets/env loaded via `pydantic-settings`. These
   never live in git (see `.env.example`).
2. YAML configs under `config/` — strategy / universe / risk. Validated on load
   through Pydantic models below. Malformed YAML refuses to load: the runner
   will not start on a bad config. See PRD §4.4 and §6.1.

Both layers are immutable (frozen models). Rely on `load_all_configs()` at
startup and pass the validated objects downstream.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from quant.types import Symbol

# --- Env / secrets -------------------------------------------------------


class Settings(BaseSettings):
    """Environment-driven runtime settings. Loaded from `.env` + process env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Broker
    broker_provider: Literal["paper", "alpaca", "backtest"] = "paper"
    paper_mode: bool = True
    alpaca_api_key: SecretStr | None = None
    alpaca_api_secret: SecretStr | None = None
    alpaca_base_url: HttpUrl = HttpUrl("https://paper-api.alpaca.markets")

    # Database
    database_url: str = "postgresql+psycopg://quant:quant_dev_pw@localhost:5432/quant"

    # Notifications
    discord_webhook_url: HttpUrl | None = None
    discord_killswitch_bot_token: SecretStr | None = None

    # Sentry
    sentry_dsn: str | None = None
    sentry_environment: Literal["dev", "paper", "live"] = "dev"

    # Runtime
    quant_env: Literal["dev", "paper", "live"] = "dev"
    quant_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    quant_data_dir: Path = Path("./data")
    # Persistent location — `/var/run` (alias of `/run`) is a tmpfs on
    # Ubuntu, so a HALT file there evaporates on reboot and trading
    # silently resumes. `/var/lib/quant` is on the root filesystem.
    quant_killswitch_file: Path = Path("/var/lib/quant/HALT")
    quant_config_dir: Path = Path("./config")

    # Observability
    prometheus_port: int = Field(default=9000, ge=1024, le=65535)

    @model_validator(mode="after")
    def _live_requires_alpaca_creds(self) -> Settings:
        if self.broker_provider == "alpaca":
            if self.alpaca_api_key is None or self.alpaca_api_secret is None:
                raise ValueError(
                    "broker_provider=alpaca requires ALPACA_API_KEY and ALPACA_API_SECRET"
                )
            if self.quant_env == "live" and self.paper_mode:
                # Guard against accidental paper mode on a live-tagged deploy.
                raise ValueError("quant_env=live but paper_mode=true; refusing to start")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


# --- YAML config models --------------------------------------------------


class _FrozenBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class StrategyConfig(_FrozenBase):
    """One enabled strategy. `weight` is its slice of portfolio capital."""

    name: str = Field(min_length=1)
    enabled: bool = True
    universe: list[Symbol] = Field(min_length=1)
    weight: float = Field(ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)


class StrategiesConfig(_FrozenBase):
    """Top-level strategies YAML."""

    strategies: list[StrategyConfig]

    @model_validator(mode="after")
    def _weights_sum_and_unique(self) -> StrategiesConfig:
        enabled = [s for s in self.strategies if s.enabled]
        if not enabled:
            raise ValueError("at least one strategy must be enabled")
        names = [s.name for s in self.strategies]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate strategy names: {names}")
        total = sum(s.weight for s in enabled)
        if not 0.99 <= total <= 1.01:
            raise ValueError(f"enabled strategy weights must sum to ~1.0 (got {total:.4f})")
        return self


class UniverseConfig(_FrozenBase):
    """Master universe: all symbols the system may ever trade."""

    name: str = "v1"
    symbols: list[Symbol] = Field(min_length=1)
    cash_symbol: Symbol = Symbol("SGOV")  # type: ignore[valid-type]
    data_sources: list[Literal["yfinance", "alpaca"]] = Field(default_factory=lambda: ["yfinance"])

    @field_validator("symbols")
    @classmethod
    def _symbols_unique(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("universe contains duplicate symbols")
        return v


RiskPct = Annotated[float, Field(gt=0.0, lt=1.0)]


class RiskConfig(_FrozenBase):
    """Risk limits — cap-guarded per PRD §6.1. Loaders cannot loosen these
    beyond the cap; attempts to exceed will raise.
    """

    # Caps come from PRD §6.1 — bounds are the *maximum* permitted per limit.
    max_position_pct: Annotated[float, Field(gt=0.0, le=0.30)] = 0.30
    max_daily_loss_pct: Annotated[float, Field(gt=0.0, le=0.05)] = 0.05
    max_monthly_drawdown_pct: Annotated[float, Field(gt=0.0, le=0.15)] = 0.15
    max_order_size_pct: Annotated[float, Field(gt=0.0, le=0.20)] = 0.20
    max_price_deviation_pct: Annotated[float, Field(gt=0.0, le=0.01)] = 0.01
    target_annual_vol: Annotated[float, Field(ge=0.05, le=0.25)] = 0.10
    max_gross_exposure: Annotated[float, Field(ge=0.0, le=2.0)] = 1.0
    killswitch_file: Path = Path("/var/lib/quant/HALT")


# --- Loader --------------------------------------------------------------


class ConfigBundle(_FrozenBase):
    """All validated configs, plus a content hash for drift detection."""

    strategies: StrategiesConfig
    universe: UniverseConfig
    risk: RiskConfig
    config_hash: str

    @model_validator(mode="after")
    def _strategies_within_universe(self) -> ConfigBundle:
        master = set(self.universe.symbols) | {self.universe.cash_symbol}
        for strat in self.strategies.strategies:
            extras = set(strat.universe) - master
            if extras:
                raise ValueError(
                    f"strategy {strat.name!r} references symbols outside the universe: "
                    f"{sorted(extras)}"
                )
        return self


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config file {path} must parse to a mapping, got {type(raw).__name__}")
    return raw


def load_config_bundle(config_dir: Path | None = None) -> ConfigBundle:
    """Load + validate all YAML configs. Raises on any invalid content."""

    cfg_dir = config_dir or get_settings().quant_config_dir
    cfg_dir = Path(cfg_dir)

    strategies_raw = _load_yaml(cfg_dir / "strategies.yaml")
    universe_raw = _load_yaml(cfg_dir / "universe.yaml")
    risk_raw = _load_yaml(cfg_dir / "risk.yaml")

    strategies = StrategiesConfig.model_validate(strategies_raw)
    universe = UniverseConfig.model_validate(universe_raw)
    risk = RiskConfig.model_validate(risk_raw)

    config_hash = _hash_configs(strategies_raw, universe_raw, risk_raw)

    return ConfigBundle(
        strategies=strategies,
        universe=universe,
        risk=risk,
        config_hash=config_hash,
    )


def _hash_configs(*raws: dict[str, Any]) -> str:
    """Stable SHA256 over the canonical YAML form. Used for drift detection
    (PRD §6.1: config hash mismatch → refuse to start).
    """
    hasher = hashlib.sha256()
    for raw in raws:
        hasher.update(yaml.safe_dump(raw, sort_keys=True).encode("utf-8"))
    return hasher.hexdigest()
