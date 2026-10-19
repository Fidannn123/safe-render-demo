"""Validate request objects against the frozen contract, including local JSON Schema references."""

from __future__ import annotations

import functools
import json
from typing import Any

import yaml
from django.conf import settings
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from apps.payments.errors import ProblemError


@functools.cache
def contract_registry() -> Registry[Any]:
    root = settings.SAFEPAY_SPEC_DIR
    resources = []
    for path in (root / "schemas").glob("*.json"):
        resources.append(
            (path.as_uri(), Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))))
        )
    path = root / "openapi-core.yaml"
    resources.append(
        (
            path.as_uri(),
            Resource.from_contents(
                yaml.safe_load(path.read_text(encoding="utf-8")), default_specification=DRAFT202012
            ),
        )
    )
    return Registry().with_resources(resources)


def validate_payload(payload: dict[str, Any], name: str, *, component: bool = True) -> None:
    root = settings.SAFEPAY_SPEC_DIR
    ref = (
        (root / "openapi-core.yaml").as_uri() + "#/components/schemas/" + name
        if component
        else (root / "schemas" / (name + ".json")).as_uri()
    )
    try:
        Draft202012Validator({"$ref": ref}, registry=contract_registry()).validate(payload)
    except ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path)
        raise ProblemError(
            "VALIDATION_FAILED", f"Invalid {name} field {path or '<root>'}: {error.validator}"
        ) from None
