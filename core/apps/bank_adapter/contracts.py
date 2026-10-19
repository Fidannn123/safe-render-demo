"""Validate inbound signed documents against the frozen contracts before consuming their fields."""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from django.conf import settings
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .signing import RemoteError


@cache
def validator(name: str) -> Draft202012Validator:
    base = "https://safepay.invalid/schemas/"
    resources = [
        (base + path.name, Resource.from_contents(json.loads(path.read_text("utf-8")), DRAFT202012))
        for path in (settings.SAFEPAY_SPEC_DIR / "schemas").glob("*.json")
    ]
    return Draft202012Validator(
        {"$ref": base + name + ".json"},
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def validate_document(name: str, document: dict[str, Any]) -> None:
    try:
        validator(name).validate(document)
    except ValidationError as exc:
        raise RemoteError("DOCUMENT_INVALID") from exc
