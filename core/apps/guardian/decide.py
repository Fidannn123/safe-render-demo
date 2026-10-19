"""Guardian decide(): pure verdict over GuardianInputs, RFC 8785 canonicalisation, Ed25519 signing.

Pipeline:
  evaluate(inputs, policy)                    -> Evaluation  (pure; verdict/tier/score/reasons)
  decide(inputs, policy, decision_id, nonce, key_id) -> UnsignedDecision (pure; replayable bytes)
  sign_decision(unsigned, keystore)           -> Decision (Ed25519 over RFC 8785 JSON without sig)
  issue_decision(inputs, policy, keystore)    -> IssuedDecision (fresh decision_id + nonce)
  verify_decision(decision, public_key)       -> bool

Verdict rules R1-R5 (spec/policy.yaml verdict_rules, DECISIONS.md D-001), first match wins:
  R1 any hard -> DENY (tier null); R2 any t3 or score >= hold -> HOLD T3; R3 payee new/changed ->
  GUARDED T2; R4 known and score <= t1_max -> ALLOW T1; R5 known and score > t1_max -> GUARDED T2.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from apps.keystore.port import KeyStore, b64encode, verify_signature

from .canonical import canonical_bytes, sha256_hex
from .inputs import GuardianInputs
from .policy import Policy
from .rules import RULES, RuleContext

Verdict = Literal["ALLOW", "GUARDED", "HOLD", "DENY"]
Tier = Literal["T1", "T2", "T3"]


class Reason(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    weight: int = Field(ge=0, le=100)
    detail: str = Field(max_length=500)


class DecisionPayment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    vendor_id: str
    account_id: str
    amount_minor: int
    currency: str
    invoice_fingerprint: str


class UnsignedDecision(BaseModel):
    """Every Decision field except sig (spec/schemas/Decision.json); the signed message."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_id: str
    tenant_id: str
    payment_id: str
    verdict: Verdict
    tier: Tier | None
    payment: DecisionPayment
    score: int = Field(ge=0, le=100)
    reasons: tuple[Reason, ...]
    policy_version: str
    network_epoch: int | None
    inputs_sha256: str
    nonce: str
    issued_at: str
    expires_at: str
    key_id: str

    def canonical(self) -> bytes:
        return canonical_bytes(self)


class Decision(UnsignedDecision):
    sig: str

    def unsigned(self) -> UnsignedDecision:
        return UnsignedDecision.model_validate(self.model_dump(exclude={"sig"}))


@dataclass(frozen=True, slots=True)
class Evaluation:
    verdict: Verdict
    tier: Tier | None
    rule_id: str
    score: int
    reasons: tuple[Reason, ...]
    hard: tuple[str, ...]
    t3: tuple[str, ...]
    signals: tuple[str, ...]
    payee_new_or_changed: bool
    network_epoch: int | None


@dataclass(frozen=True, slots=True)
class IssuedDecision:
    decision: Decision
    canonical_inputs: bytes  # exactly the bytes inputs_sha256 hashes; stored with the Decision


def format_timestamp(value: dt.datetime) -> str:
    """RFC 3339 UTC with Z; microseconds only when present (deterministic)."""
    utc = value.astimezone(dt.UTC)
    if utc.microsecond:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def fired_codes(ctx: RuleContext, codes: tuple[str, ...]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for code in codes:
        detail = RULES[code](ctx)
        if detail is not None:
            out.append((code, detail))
    return out


def _verdict(
    policy: Policy, hard: bool, t3: bool, score: int, new_or_changed: bool
) -> tuple[str, Verdict, Tier | None]:
    predicates: dict[str, Callable[[], bool]] = {
        "R1": lambda: hard,
        "R2": lambda: t3 or score >= policy.score_hold_threshold,
        "R3": lambda: new_or_changed,
        "R4": lambda: not new_or_changed and score <= policy.score_t1_max,
        "R5": lambda: not new_or_changed and score > policy.score_t1_max,
    }
    outcomes: dict[str, tuple[Verdict, Tier | None]] = {
        "R1": ("DENY", None),
        "R2": ("HOLD", "T3"),
        "R3": ("GUARDED", "T2"),
        "R4": ("ALLOW", "T1"),
        "R5": ("GUARDED", "T2"),
    }
    for rule in policy.verdict_rules:  # policy order; parse_policy pins ids/verdicts/tiers
        if predicates[rule.id]():
            verdict, tier = outcomes[rule.id]
            return rule.id, verdict, tier
    raise AssertionError("verdict rules R1-R5 are exhaustive")  # pragma: no cover


def evaluate(inputs: GuardianInputs, policy: Policy) -> Evaluation:
    """Pure: run every reason-code rule and resolve the verdict. No I/O, no clock, no randomness."""
    ctx = RuleContext.build(inputs, policy)
    hard = fired_codes(ctx, policy.hard_codes)
    t3 = fired_codes(ctx, policy.t3_codes)
    signals = fired_codes(ctx, tuple(policy.signal_weights.keys()))
    score = min(policy.score_max, sum(policy.weight(code) for code, _ in signals))
    rule_id, verdict, tier = _verdict(policy, bool(hard), bool(t3), score, ctx.new_or_changed)
    reasons = tuple(
        Reason(code=code, weight=policy.weight(code), detail=detail)
        for code, detail in (*hard, *t3, *signals)
    )
    network_epoch = inputs.network.epoch if inputs.network.status in ("HIT", "MISS") else None
    return Evaluation(
        verdict=verdict,
        tier=tier,
        rule_id=rule_id,
        score=score,
        reasons=reasons,
        hard=tuple(c for c, _ in hard),
        t3=tuple(c for c, _ in t3),
        signals=tuple(c for c, _ in signals),
        payee_new_or_changed=ctx.new_or_changed,
        network_epoch=network_epoch,
    )


def canonical_inputs(inputs: GuardianInputs) -> bytes:
    return canonical_bytes(inputs)


def decide(
    inputs: GuardianInputs, policy: Policy, *, decision_id: uuid.UUID, nonce: str, key_id: str
) -> UnsignedDecision:
    """Pure: same inputs + policy (+ the same id, nonce, key_id) -> byte-identical canonical JSON."""
    ev = evaluate(inputs, policy)
    p = inputs.proposal
    return UnsignedDecision(
        decision_id=str(decision_id),
        tenant_id=str(inputs.tenant_id),
        payment_id=str(p.payment_id),
        verdict=ev.verdict,
        tier=ev.tier,
        payment=DecisionPayment(
            vendor_id=str(p.vendor_id),
            account_id=str(p.account_id),
            amount_minor=p.amount_minor,
            currency=p.currency,
            invoice_fingerprint=p.invoice_fingerprint,
        ),
        score=ev.score,
        reasons=ev.reasons,
        policy_version=policy.policy_version,
        network_epoch=ev.network_epoch,
        inputs_sha256=sha256_hex(canonical_inputs(inputs)),
        nonce=nonce,
        issued_at=format_timestamp(inputs.now),
        expires_at=format_timestamp(inputs.now + dt.timedelta(seconds=policy.decision_ttl_seconds)),
        key_id=key_id,
    )


def sign_decision(unsigned: UnsignedDecision, keystore: KeyStore) -> Decision:
    sig = keystore.sign(unsigned.key_id, unsigned.canonical())
    return Decision(**unsigned.model_dump(), sig=b64encode(sig))


def verify_decision(decision: Decision, public_key: Ed25519PublicKey) -> bool:
    return verify_signature(public_key, decision.unsigned().canonical(), decision.sig)


def issue_decision(inputs: GuardianInputs, policy: Policy, keystore: KeyStore) -> IssuedDecision:
    """decide() with a fresh decision_id and a 128-bit nonce, signed with the tenant's active key."""
    key_id = keystore.active_key_id(inputs.tenant_id, "decision")
    unsigned = decide(inputs, policy, decision_id=uuid.uuid4(), nonce=secrets.token_hex(16), key_id=key_id)
    return IssuedDecision(
        decision=sign_decision(unsigned, keystore), canonical_inputs=canonical_inputs(inputs)
    )
