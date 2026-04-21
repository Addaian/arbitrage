"""Config-loading tests. Invariant: malformed configs must raise; they must
never silently fall back to defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from quant.config import (
    ConfigBundle,
    RiskConfig,
    StrategiesConfig,
    UniverseConfig,
    load_config_bundle,
)

# --- Happy path ---------------------------------------------------------


def test_loads_real_repo_configs() -> None:
    bundle = load_config_bundle(Path("config"))
    assert isinstance(bundle, ConfigBundle)
    assert bundle.strategies.strategies, "expected at least one strategy"
    assert len(bundle.universe.symbols) >= 5
    assert 0.0 < bundle.risk.max_position_pct <= 0.30
    # Hash must be deterministic: loading again yields the same value.
    again = load_config_bundle(Path("config"))
    assert bundle.config_hash == again.config_hash


# --- Strategy validation ------------------------------------------------


def test_strategy_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match=r"sum to ~1\.0"):
        StrategiesConfig.model_validate(
            {
                "strategies": [
                    {"name": "a", "enabled": True, "weight": 0.3, "universe": ["SPY"]},
                    {"name": "b", "enabled": True, "weight": 0.3, "universe": ["SPY"]},
                ]
            }
        )


def test_duplicate_strategy_names_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate strategy names"):
        StrategiesConfig.model_validate(
            {
                "strategies": [
                    {"name": "dup", "enabled": True, "weight": 0.5, "universe": ["SPY"]},
                    {"name": "dup", "enabled": True, "weight": 0.5, "universe": ["SPY"]},
                ]
            }
        )


def test_at_least_one_strategy_enabled() -> None:
    with pytest.raises(ValidationError, match="at least one strategy must be enabled"):
        StrategiesConfig.model_validate(
            {
                "strategies": [
                    {"name": "a", "enabled": False, "weight": 0.5, "universe": ["SPY"]},
                    {"name": "b", "enabled": False, "weight": 0.5, "universe": ["SPY"]},
                ]
            }
        )


def test_disabled_strategies_excluded_from_weight_sum() -> None:
    # If the disabled strategy's 0.8 were counted, total=1.8 and validation would fail.
    cfg = StrategiesConfig.model_validate(
        {
            "strategies": [
                {"name": "on", "enabled": True, "weight": 1.0, "universe": ["SPY"]},
                {"name": "off", "enabled": False, "weight": 0.8, "universe": ["SPY"]},
            ]
        }
    )
    assert [s.name for s in cfg.strategies if s.enabled] == ["on"]


# --- Universe validation ------------------------------------------------


def test_universe_rejects_duplicates() -> None:
    with pytest.raises(ValidationError, match="duplicate symbols"):
        UniverseConfig.model_validate({"symbols": ["SPY", "SPY", "QQQ"]})


# --- Risk validation ----------------------------------------------------


def test_risk_limits_cannot_exceed_prd_caps() -> None:
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"max_position_pct": 0.50})  # PRD cap is 0.30
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"max_daily_loss_pct": 0.10})  # cap 0.05


def test_risk_target_vol_bounds() -> None:
    # Below 5% or above 25% is rejected.
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"target_annual_vol": 0.02})
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({"target_annual_vol": 0.50})
    # Boundary-ok values accepted.
    assert RiskConfig.model_validate({"target_annual_vol": 0.05}).target_annual_vol == 0.05
    assert RiskConfig.model_validate({"target_annual_vol": 0.25}).target_annual_vol == 0.25


# --- Bundle cross-validation --------------------------------------------


def test_strategy_universe_must_be_subset_of_master_universe(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        strategies={
            "strategies": [
                {
                    "name": "bad",
                    "enabled": True,
                    "weight": 1.0,
                    "universe": ["BTC"],  # not in master universe
                }
            ]
        },
        universe={"symbols": ["SPY", "QQQ"], "cash_symbol": "SGOV"},
        risk={},
    )
    with pytest.raises(ValidationError, match="outside the universe"):
        load_config_bundle(tmp_path)


def test_cash_symbol_is_allowed_in_strategy_universe(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        strategies={
            "strategies": [
                {
                    "name": "trend",
                    "enabled": True,
                    "weight": 1.0,
                    "universe": ["SPY", "SGOV"],
                }
            ]
        },
        universe={"symbols": ["SPY"], "cash_symbol": "SGOV"},
        risk={},
    )
    bundle = load_config_bundle(tmp_path)
    assert "SGOV" in bundle.strategies.strategies[0].universe


# --- Malformed YAML -----------------------------------------------------


def test_malformed_yaml_refuses_to_load(tmp_path: Path) -> None:
    (tmp_path / "strategies.yaml").write_text(":\n::not-valid:::", encoding="utf-8")
    (tmp_path / "universe.yaml").write_text("symbols: [SPY]\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text("{}", encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017
        load_config_bundle(tmp_path)


def test_missing_config_file_raises(tmp_path: Path) -> None:
    # Only create two of the three files.
    (tmp_path / "strategies.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "x", "weight": 1.0, "universe": ["SPY"]}]}),
        encoding="utf-8",
    )
    (tmp_path / "universe.yaml").write_text(yaml.safe_dump({"symbols": ["SPY"]}), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_config_bundle(tmp_path)


def test_top_level_list_in_yaml_rejected(tmp_path: Path) -> None:
    (tmp_path / "strategies.yaml").write_text("- just a list\n", encoding="utf-8")
    (tmp_path / "universe.yaml").write_text("symbols: [SPY]\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must parse to a mapping"):
        load_config_bundle(tmp_path)


# --- Helpers ------------------------------------------------------------


def _write_bundle(
    path: Path,
    *,
    strategies: dict[str, object],
    universe: dict[str, object],
    risk: dict[str, object],
) -> None:
    (path / "strategies.yaml").write_text(yaml.safe_dump(strategies), encoding="utf-8")
    (path / "universe.yaml").write_text(yaml.safe_dump(universe), encoding="utf-8")
    (path / "risk.yaml").write_text(yaml.safe_dump(risk), encoding="utf-8")
