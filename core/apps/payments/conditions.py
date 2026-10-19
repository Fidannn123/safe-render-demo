"""Evaluator for the tiny condition language of spec/state_machine.yaml.

posting_condition: atoms joined by " and "; an atom is a boolean fact (`vendor_bound`, `invoice_accrued`)
or `field == literal` (`reject_reason == fraud_or_invalid`). effects: `<effect> [if <condition>]`.
Unknown names raise ValueError when compiled (at startup), never silently pass.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from .models import Payment

Predicate = Callable[[Payment], bool]
Effect = Callable[[Payment], None]

BOOL_FACTS: dict[str, Predicate] = {
    "vendor_bound": lambda p: p.vendor_id is not None,
    "invoice_accrued": lambda p: bool(p.invoice_accrued),
}
FIELD_FACTS: dict[str, Callable[[Payment], str | None]] = {
    "reject_reason": lambda p: p.reject_reason,
}


def _mark_payee_reportable(p: Payment) -> None:
    p.payee_reportable = True


EFFECTS: dict[str, Effect] = {"mark_payee_reportable": _mark_payee_reportable}


def _atom(text: str) -> Predicate:
    if "==" in text:
        name, _, literal = (part.strip() for part in text.partition("=="))
        getter = FIELD_FACTS.get(name)
        if getter is None or not literal:
            raise ValueError(f"unknown condition field in {text!r}")
        return lambda p: getter(p) == literal
    pred = BOOL_FACTS.get(text)
    if pred is None:
        raise ValueError(f"unknown condition fact {text!r}")
    return pred


@functools.cache
def compile_condition(expr: str) -> Predicate:
    atoms = [_atom(part.strip()) for part in expr.split(" and ")]
    return lambda p: all(a(p) for a in atoms)


@functools.cache
def compile_effect(expr: str) -> Effect:
    name, sep, cond = (part.strip() for part in expr.partition(" if "))
    effect = EFFECTS.get(name)
    if effect is None:
        raise ValueError(f"unknown effect {name!r}")
    if not sep:
        return effect
    pred = compile_condition(cond)

    def run(p: Payment) -> None:
        if pred(p):
            effect(p)

    return run
