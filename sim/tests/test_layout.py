from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize("module", ["twins", "scenarios", "attacker", "harness"])
def test_sim_packages_import(module: str) -> None:
    mod = importlib.import_module(f"safepay_sim.{module}")
    assert mod.__doc__
