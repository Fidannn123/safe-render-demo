"""Loader for spec/state_machine.yaml (FROZEN). The payment state machine is data, never hard-coded."""

from __future__ import annotations

import functools
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings


@dataclass(frozen=True, slots=True)
class StateSpec:
    name: str
    terminal: bool
    terminal_when: tuple[tuple[str, str], ...]  # (field, value) pairs that make the state terminal


@dataclass(frozen=True, slots=True)
class TransitionSpec:
    from_state: str
    to_state: str
    trigger: str
    actor: str
    posting: str | None
    posting_condition: str | None
    guards: tuple[str, ...]
    effects: tuple[str, ...]
    outbox: str | None


@dataclass(frozen=True, slots=True)
class StateMachineSpec:
    version: int
    initial: str
    states: dict[str, StateSpec]
    transitions: dict[tuple[str, str], TransitionSpec]
    atomic_chains: tuple[tuple[str, ...], ...]

    def get(self, from_state: str, to_state: str) -> TransitionSpec | None:
        return self.transitions.get((from_state, to_state))

    def chain_continuation(self, from_state: str, to_state: str) -> tuple[str, ...]:
        """States that MUST follow (from_state -> to_state) in the same transaction (atomic_chains)."""
        for chain in self.atomic_chains:
            for i in range(len(chain) - 1):
                if (chain[i], chain[i + 1]) == (from_state, to_state):
                    return chain[i + 2 :]
        return ()

    def is_terminal(self, state: str, fields: dict[str, Any]) -> bool:
        st = self.states[state]
        if st.terminal:
            return True
        return bool(st.terminal_when) and all(fields.get(k) == v for k, v in st.terminal_when)


def parse_state_machine(raw: dict[str, Any]) -> StateMachineSpec:
    states = {
        str(name): StateSpec(
            name=str(name),
            terminal=bool(val["terminal"]),
            terminal_when=tuple((str(k), str(v)) for k, v in (val.get("terminal_when") or {}).items()),
        )
        for name, val in raw["states"].items()
    }
    transitions: dict[tuple[str, str], TransitionSpec] = {}
    for t in raw["transitions"]:
        spec = TransitionSpec(
            from_state=str(t["from"]),
            to_state=str(t["to"]),
            trigger=str(t["trigger"]),
            actor=str(t["actor"]),
            posting=None if t.get("posting") is None else str(t["posting"]),
            posting_condition=None if t.get("posting_condition") is None else str(t["posting_condition"]),
            guards=tuple(str(g) for g in t.get("guards") or ()),
            effects=tuple(str(e) for e in t.get("effects") or ()),
            outbox=None if t.get("outbox") is None else str(t["outbox"]),
        )
        key = (spec.from_state, spec.to_state)
        if key in transitions:
            raise ValueError(f"duplicate transition {key}")
        if spec.from_state not in states or spec.to_state not in states:
            raise ValueError(f"transition {key} references an unknown state")
        transitions[key] = spec
    chains = tuple(tuple(str(s) for s in chain) for chain in raw.get("atomic_chains") or ())
    for chain in chains:
        for a, b in itertools.pairwise(chain):
            if (a, b) not in transitions:
                raise ValueError(f"atomic chain step {a}->{b} is not a transition")
    initial = str(raw["initial"])
    if initial not in states:
        raise ValueError("initial state unknown")
    return StateMachineSpec(
        version=int(raw["version"]),
        initial=initial,
        states=states,
        transitions=transitions,
        atomic_chains=chains,
    )


@functools.cache
def state_machine() -> StateMachineSpec:
    path = Path(settings.SAFEPAY_SPEC_DIR) / "state_machine.yaml"
    with path.open(encoding="utf-8") as fh:
        return parse_state_machine(yaml.safe_load(fh))
