"""Typed problem errors with codes from spec/errors.yaml.

RFC 9457 problem+json is rendered by the API layer from ProblemError.as_problem().
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings


@dataclass(frozen=True, slots=True)
class ProblemCode:
    code: str
    status: int
    title: str
    type: str


@functools.cache
def problem_catalog() -> dict[str, ProblemCode]:
    path = Path(settings.SAFEPAY_SPEC_DIR) / "errors.yaml"
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    prefix = str(raw["type_prefix"])
    return {
        str(code): ProblemCode(
            code=str(code), status=int(v["status"]), title=str(v["title"]), type=prefix + str(code)
        )
        for code, v in raw["codes"].items()
    }


class ProblemError(Exception):
    """A domain error carrying a typed code from spec/errors.yaml."""

    def __init__(self, code: str, detail: str = "") -> None:
        catalog = problem_catalog()
        if code not in catalog:
            raise ValueError(f"problem code {code!r} is not in spec/errors.yaml")
        self.problem = catalog[code]
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)

    @property
    def status(self) -> int:
        return self.problem.status

    def as_problem(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.problem.type,
            "title": self.problem.title,
            "status": self.problem.status,
            "code": self.code,
        }
        if self.detail:
            body["detail"] = self.detail
        return body


class IllegalTransitionError(ProblemError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("ILLEGAL_TRANSITION", detail)
