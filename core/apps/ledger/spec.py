"""Loader for spec/ledger_accounts.yaml (FROZEN).

Account properties and posting templates come from here only.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from django.conf import settings

Side = Literal["debit", "credit"]

# Placeholders used by the spec. Each maps a template account to an argument of post_template().
VENDOR_PLACEHOLDER = "<vendor>"
CUSTOMER_PLACEHOLDER = "<customer>"
EXPENSE_PLACEHOLDER = "{expense_account}"


@dataclass(frozen=True, slots=True)
class AccountSpec:
    key: str  # spec key, e.g. "Bank:available" or "AP:<vendor>"
    type: str
    normal: Side
    non_negative: bool

    @property
    def normal_sign(self) -> int:
        """+1 for debit-normal accounts, -1 for credit-normal (balance = sign * sum(postings))."""
        return 1 if self.normal == "debit" else -1


@dataclass(frozen=True, slots=True)
class TemplateLine:
    account: str  # spec account key or {expense_account}
    side: Side

    @property
    def sign(self) -> int:
        return 1 if self.side == "debit" else -1


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    name: str
    lines: tuple[TemplateLine, ...]


@dataclass(frozen=True, slots=True)
class LedgerSpec:
    version: int
    accounts: dict[str, AccountSpec]
    templates: dict[str, TemplateSpec]

    @property
    def fixed_accounts(self) -> tuple[str, ...]:
        return tuple(k for k in self.accounts if "<" not in k)

    @property
    def non_negative_accounts(self) -> tuple[str, ...]:
        return tuple(k for k, a in self.accounts.items() if a.non_negative)

    @property
    def expense_accounts(self) -> tuple[str, ...]:
        """Accounts allowed for {expense_account}: fixed accounts of type expense (Expense, COGS)."""
        return tuple(k for k, a in self.accounts.items() if a.type == "expense" and "<" not in k)

    def account_for_code(self, code: str) -> AccountSpec:
        """Spec entry for a concrete account code such as "AP:3f0c..." or "Bank:available"."""
        if code in self.accounts and "<" not in code:
            return self.accounts[code]
        prefix, sep, _ = code.partition(":")
        for key, acct in self.accounts.items():
            if "<" in key and sep and key.split(":", 1)[0] == prefix:
                return acct
        raise KeyError(f"account code not in spec: {code}")


def _side(value: Any) -> Side:
    if value == "debit":
        return "debit"
    if value == "credit":
        return "credit"
    raise ValueError(f"invalid side {value!r}")


def parse_ledger_spec(raw: dict[str, Any]) -> LedgerSpec:
    accounts = {
        str(key): AccountSpec(
            key=str(key),
            type=str(val["type"]),
            normal=_side(val["normal"]),
            non_negative=bool(val["non_negative"]),
        )
        for key, val in raw["accounts"].items()
    }
    templates: dict[str, TemplateSpec] = {}
    for name, tpl in raw["templates"].items():
        lines = tuple(TemplateLine(account=str(a), side=_side(s)) for a, s in tpl["lines"])
        for line in lines:
            if line.account != EXPENSE_PLACEHOLDER and line.account not in accounts:
                raise ValueError(f"template {name}: unknown account {line.account}")
        if sum(line.sign for line in lines) != 0:
            raise ValueError(f"template {name} does not balance")
        templates[str(name)] = TemplateSpec(name=str(name), lines=lines)
    return LedgerSpec(version=int(raw["version"]), accounts=accounts, templates=templates)


@functools.cache
def ledger_spec() -> LedgerSpec:
    path = Path(settings.SAFEPAY_SPEC_DIR) / "ledger_accounts.yaml"
    with path.open(encoding="utf-8") as fh:
        return parse_ledger_spec(yaml.safe_load(fh))
