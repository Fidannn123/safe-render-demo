"""Every transition pair absent from the frozen state machine raises ILLEGAL_TRANSITION."""

from __future__ import annotations

import uuid

import pytest

from apps.audit.models import AuditEvent
from apps.ledger.models import JournalEntry
from apps.ledger.tenancy import tenant_transaction
from apps.payments.errors import IllegalTransitionError
from apps.payments.models import Payment
from apps.payments.service import transition
from apps.payments.spec import state_machine
from apps.payments.types import Actor, TransitionContext

from .helpers import VENDOR

SM = state_machine()
STATES = sorted(SM.states)
ILLEGAL = [(a, b) for a in STATES for b in STATES if (a, b) not in SM.transitions]
LEGAL = sorted(SM.transitions)


def _row(tenant: uuid.UUID, state: str, reject_reason: str | None = None) -> Payment:
    with tenant_transaction(tenant):
        return Payment.objects.create(
            tenant_id=tenant,
            invoice_id=uuid.uuid4(),
            state=state,
            vendor_id=VENDOR,
            amount_minor=1_000,
            currency="USD",
            reject_reason=reject_reason or ("not_now" if state == "REJECTED" else None),
        )


def test_spec_has_illegal_pairs() -> None:
    assert len(ILLEGAL) == len(STATES) ** 2 - len(LEGAL)
    assert len(LEGAL) == 15


@pytest.mark.django_db
@pytest.mark.parametrize(("from_state", "to_state"), ILLEGAL, ids=[f"{a}->{b}" for a, b in ILLEGAL])
def test_illegal_pair_raises(from_state: str, to_state: str) -> None:
    tenant = uuid.uuid4()
    p = _row(tenant, from_state)
    # Use every trigger/actor the spec knows, so no trigger can sneak an illegal pair through.
    for t in SM.transitions.values():
        with pytest.raises(IllegalTransitionError) as err:
            transition(p, to_state, t.trigger, Actor(t.actor, "x"), TransitionContext())
        assert err.value.code == "ILLEGAL_TRANSITION"
        assert err.value.status == 409
    with tenant_transaction(tenant):
        assert Payment.objects.get(id=p.id).state == from_state
        assert not AuditEvent.objects.filter(subject_id=str(p.id)).exists()
        assert not JournalEntry.objects.filter(payment_id=p.id).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("pair", LEGAL, ids=[f"{a}->{b}" for a, b in LEGAL])
def test_legal_pair_with_wrong_trigger_raises(pair: tuple[str, str]) -> None:
    tenant = uuid.uuid4()
    p = _row(tenant, pair[0])
    with pytest.raises(IllegalTransitionError):
        transition(p, pair[1], "not_a_trigger", Actor(SM.transitions[pair].actor, "x"))


@pytest.mark.django_db
def test_rejected_fraud_is_terminal_for_every_target() -> None:
    tenant = uuid.uuid4()
    p = _row(tenant, "REJECTED", reject_reason="fraud_or_invalid")
    for target in STATES:
        with pytest.raises(IllegalTransitionError):
            transition(p, target, "proposal_received", Actor("agent", "agent-1"))


@pytest.mark.django_db
@pytest.mark.parametrize("terminal", [s for s in STATES if SM.states[s].terminal])
def test_terminal_states_have_no_exit(terminal: str) -> None:
    tenant = uuid.uuid4()
    p = _row(tenant, terminal)
    for target in STATES:
        with pytest.raises(IllegalTransitionError):
            transition(p, target, "recon_matched", Actor("reconciler", "r"))
