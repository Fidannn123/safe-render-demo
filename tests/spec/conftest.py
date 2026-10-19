"""Shared loaders for the spec/ contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec"
SCHEMAS = SPEC / "schemas"
SCHEMA_BASE = "https://safepay.invalid/spec/schemas/"  # never fetched; resolution is local only


def load_yaml(name: str) -> Any:
    return yaml.safe_load((SPEC / name).read_text(encoding="utf-8"))


def schema_files() -> list[Path]:
    return sorted(SCHEMAS.glob("*.json"))


def load_schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    return data


def schema_registry() -> Registry:
    resources = [
        (SCHEMA_BASE + p.name, Resource.from_contents(json.loads(p.read_text("utf-8")), DRAFT202012))
        for p in schema_files()
    ]
    return Registry().with_resources(resources)


@pytest.fixture(scope="session")
def registry() -> Registry:
    return schema_registry()


@pytest.fixture(scope="session")
def policy() -> Any:
    return load_yaml("policy.yaml")


@pytest.fixture(scope="session")
def state_machine() -> Any:
    return load_yaml("state_machine.yaml")


@pytest.fixture(scope="session")
def ledger() -> Any:
    return load_yaml("ledger_accounts.yaml")


@pytest.fixture(scope="session")
def errors() -> Any:
    return load_yaml("errors.yaml")


@pytest.fixture(scope="session")
def common() -> dict[str, Any]:
    return load_schema("common.json")
