"""Fixed scenario plan per twin. The seed varies vendors, amounts, dates and accounts, not the plan.

Targets (architecture.md section 5): ~60 benign, ~40 attacks across 12 classes, ~10 ambiguous.
Twins with stress NONE carry the pay-invoice benign set and most attacks; stressed twins (LOW_BUFFER /
SHORTFALL) carry cash-buffer scenarios, because every payment there breaches the buffer.
"""

from __future__ import annotations

from .catalog import SECTORS

BENIGN_PER_HEALTHY_TWIN = 7

# Plan items: see scenarios.py for each kind's construction and expected verdicts.
PLAN: dict[str, tuple[str, ...]] = {
    "retail": (
        "dup_exact",
        "hidden",
        "swap",
        "lookalike_unregistered",
        "split_known",
        "amb_over_tx",
        "amb_changed",
        "stolen_token",
    ),
    "logistics": (
        "dup_near",
        "hidden",
        "swap",
        "lookalike_registered",
        "split_new",
        "amb_changed",
        "amb_mismatch",
        "self_approval",
    ),
    "pharmacy": (
        "dup_exact",
        "hidden",
        "swap",
        "lookalike_account",
        "reported_2plus",
        "amb_mismatch",
        "double_submit",
    ),
    "it_services": (
        "dup_near",
        "hidden",
        "swap",
        "lookalike_unregistered",
        "reported_1",
        "amb_over_tx",
        "false_report",
    ),
    "textiles": ("dup_exact", "hidden", "swap", "lookalike_registered", "amb_changed", "oprf_enum"),
    "construction": ("dup_exact", "breach", "breach", "amb_breach"),
    "restaurant": ("hidden", "breach", "breach", "amb_breach"),
    "agriculture": ("swap", "breach", "breach", "amb_breach"),
}

# Vendor roles each plan item needs (besides regular vendors).
ROLE_NEEDS: dict[str, tuple[str, ...]] = {
    "lookalike_registered": ("target", "lookalike_registered"),
    "lookalike_account": ("target", "lookalike_account"),
    "amb_changed": ("changed_legit",),
    "reported_2plus": ("reported",),
    "reported_1": ("reported",),
    "split_new": ("new_split",),
}

# Twins whose accounts appear as reporters of a reported payee (they were earlier victims).
REPORTERS: dict[str, tuple[str, ...]] = {
    "reported_2plus": ("construction", "textiles"),
    "reported_1": ("restaurant",),
}


def roles_needed(twin_key: str) -> list[str]:
    roles: list[str] = []
    for item in PLAN[twin_key]:
        for role in ROLE_NEEDS.get(item, ()):
            if role not in roles:
                roles.append(role)
    return roles


if set(PLAN) != {s.key for s in SECTORS}:  # pragma: no cover
    raise RuntimeError("PLAN must cover every sector")
