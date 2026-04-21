"""Week 1 smoke test: every quant submodule must be importable.

Guards the package tree against typos and missing __init__.py files. As modules
get fleshed out in later waves, they must remain importable from an empty state.
"""

from __future__ import annotations

import importlib

import pytest

import quant

SUBMODULES: list[str] = [
    "quant",
    "quant.config",
    "quant.types",
    "quant.data",
    "quant.features",
    "quant.signals",
    "quant.models",
    "quant.portfolio",
    "quant.execution",
    "quant.risk",
    "quant.backtest",
    "quant.live",
    "quant.monitoring",
    "quant.storage",
]


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_importable(name: str) -> None:
    module = importlib.import_module(name)
    assert module is not None


def test_package_version_is_set() -> None:
    assert isinstance(quant.__version__, str)
    assert quant.__version__
