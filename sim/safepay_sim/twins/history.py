"""90 simulated days of history for one SYNTHETIC twin: registry, AP, AR and recurring opex."""

from __future__ import annotations

import random
import string

from .catalog import SectorSpec, VendorSpec
from .iban import IbanFactory
from .model import (
    AS_OF_DAY,
    HISTORY_DAYS,
    ApInvoice,
    ArInvoice,
    Customer,
    OpexDebit,
    OpexItem,
    Twin,
    Vendor,
    VendorRole,
    new_uuid,
)
from .plan import roles_needed
from .textutil import damerau_levenshtein, normalise_ref, slug

CHANGED_ACCOUNT_DAY = AS_OF_DAY - 9  # registry account changed 9 days before as-of (< 30 days)
NEW_VENDOR_DAY = AS_OF_DAY - 5  # new vendors onboarded 5 days before as-of
REGISTRY_ADDED_DAY = -180  # established vendors were onboarded long before the window

# Discount offers used on open AP (bp, within_days). APR vs the 12% hurdle is far from the boundary.
DISCOUNT_OFFERS: tuple[tuple[int, int], ...] = ((200, 10), (100, 10), (50, 10), (300, 15), (50, 5))


def ref_prefix(name: str) -> str:
    words = [w for w in slug(name).split("-") if w]
    return "".join(w[0] for w in words[:3]).upper() or "INV"


def random_ref(rng: random.Random, prefix: str, existing: list[str]) -> str:
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(1000):
        ref = f"{prefix}-{''.join(rng.choice(alphabet) for _ in range(6))}"
        n = normalise_ref(ref)
        if all(damerau_levenshtein(n, normalise_ref(e)) > 4 for e in existing):
            return ref
    raise RuntimeError("reference space exhausted")  # pragma: no cover


def jitter(rng: random.Random, base: int, lo_permille: int, hi_permille: int) -> int:
    return base * rng.randint(lo_permille, hi_permille) // 1000


def split_lines(rng: random.Random, amount: int) -> list[int]:
    n = rng.randint(1, 3)
    if n == 1 or amount < 300:
        return [amount]
    cuts = sorted(rng.sample(range(1, amount), n - 1))
    parts = [b - a for a, b in zip([0, *cuts], [*cuts, amount], strict=True)]
    return parts if all(p > 0 for p in parts) else [amount]


def tax_id(rng: random.Random, country: str) -> str:
    return f"{country}{rng.randint(100_000_000, 999_999_999)}"


def _make_vendor(
    rng: random.Random,
    factory: IbanFactory,
    spec: VendorSpec,
    country: str,
    role: VendorRole,
) -> Vendor:
    base = jitter(rng, spec.base_minor, 900, 1100) if spec.base_minor else 0
    return Vendor(
        vendor_id=new_uuid(rng),
        name=spec.name,
        tax_id=tax_id(rng, country),
        country=country,
        iban=factory.issue(rng, country),
        expense_account=spec.expense_account,
        cadence_days=spec.cadence_days,
        terms_days=spec.terms_days,
        base_minor=base,
        criticality=spec.criticality,
        late_fee_minor=spec.late_fee_minor,
        role=role,
        added_day=REGISTRY_ADDED_DAY,
        email_domain=f"{slug(spec.name)}.example",
        ref_prefix=ref_prefix(spec.name),
    )


LOOKALIKE_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("l", "1"),
    ("o", "0"),
    ("i", "1"),
    ("m", "rn"),
    ("e", "\u0435"),  # Cyrillic ie (email-only names)
)
# Only for names with none of the letters above after the first one (e.g. "Oakhurst Snacks Ltd").
LOOKALIKE_FALLBACK: tuple[tuple[str, str], ...] = (
    ("a", "4"),
    ("s", "5"),
    ("t", "7"),
    ("n", "rn"),
)


def lookalike_name(name: str, rng: random.Random, ascii_only: bool) -> str:
    options = [
        (src, dst)
        for src, dst in LOOKALIKE_SUBSTITUTIONS
        if src in name[1:] and (dst.isascii() or not ascii_only)
    ]
    if not options:
        options = [(src, dst) for src, dst in LOOKALIKE_FALLBACK if src in name[1:]]
    src, dst = rng.choice(options)
    positions = [i for i in range(1, len(name)) if name.startswith(src, i)]
    pos = rng.choice(positions)
    return name[:pos] + dst + name[pos + len(src) :]


def build_twin(  # noqa: PLR0912, PLR0915
    spec: SectorSpec, rng: random.Random, factory: IbanFactory
) -> Twin:
    tenant_id = new_uuid(rng)
    bank_iban = factory.issue(rng, spec.country)
    needs = roles_needed(spec.key)

    regular_specs = list(spec.vendors[:6])
    extra_specs = list(spec.vendors[6:])
    rng.shuffle(extra_specs)
    vendors: list[Vendor] = [_make_vendor(rng, factory, vs, spec.country, "regular") for vs in regular_specs]

    if "target" in needs:
        # The imitated vendor gets no scenario invoices (keeps benign payees clear of look-alikes).
        candidates = sorted((v for v in vendors), key=lambda v: v.base_minor)[:3]
        rng.choice(candidates).role = "target"
    for role in ("changed_legit", "reported", "lookalike_account", "new_split"):
        if role in needs:
            if role == "reported":
                # Its scenario must stay under new_payee_cap without tripping AMOUNT_ANOMALY, so the
                # reported vendor is the cheapest spare one (a costly one has no unremarkable amount there).
                vs = min(extra_specs, key=lambda s: s.base_minor)
                extra_specs.remove(vs)
            else:
                vs = extra_specs.pop()
            v = _make_vendor(rng, factory, vs, spec.country, role)  # type: ignore[arg-type]
            vendors.append(v)
    for vs in extra_specs:
        vendors.append(_make_vendor(rng, factory, vs, spec.country, "regular"))
    if spec.big_vendor is not None:
        vendors.append(_make_vendor(rng, factory, spec.big_vendor, spec.country, "big"))

    target = next((v for v in vendors if v.role == "target"), None)
    for v in vendors:
        if v.role in ("changed_legit", "reported", "lookalike_account"):
            v.previous_ibans = [v.iban]
            v.account_changed_day = CHANGED_ACCOUNT_DAY
            if v.role == "lookalike_account":
                if target is None:
                    raise RuntimeError("lookalike_account needs a target vendor")
                v.iban = factory.lookalike_of(rng, target.iban)
                v.lookalike_of = target.vendor_id
            else:
                v.iban = factory.issue(rng, spec.country)
        if v.role == "new_split":
            v.added_day = NEW_VENDOR_DAY
    if "lookalike_registered" in needs:
        if target is None:
            raise RuntimeError("lookalike_registered needs a target vendor")
        fake = Vendor(
            vendor_id=new_uuid(rng),
            name=lookalike_name(target.name, rng, ascii_only=True),
            tax_id=tax_id(rng, spec.country),
            country=spec.country,
            iban=factory.issue(rng, spec.country),
            expense_account=target.expense_account,
            cadence_days=target.cadence_days,
            terms_days=target.terms_days,
            base_minor=target.base_minor,
            criticality=1,
            late_fee_minor=target.late_fee_minor,
            role="lookalike_registered",
            added_day=NEW_VENDOR_DAY,
            email_domain=f"{slug(target.name)}-billing.example",
            ref_prefix=target.ref_prefix,
            lookalike_of=target.vendor_id,
        )
        vendors.append(fake)

    ap: list[ApInvoice] = []
    for v in vendors:
        if not v.has_history or v.role == "big":
            continue
        offset = rng.randrange(v.cadence_days)
        refs: list[str] = []
        for issue in range(offset, HISTORY_DAYS, v.cadence_days):
            amount = jitter(rng, v.base_minor, 880, 1120)
            ref = random_ref(rng, v.ref_prefix, refs)
            refs.append(ref)
            inv = ApInvoice(
                invoice_id=new_uuid(rng),
                vendor_id=v.vendor_id,
                reference=ref,
                issue_day=issue,
                due_day=issue + v.terms_days,
                amount_minor=amount,
                lines=split_lines(rng, amount),
            )
            if inv.due_day < AS_OF_DAY:
                inv.paid_day = inv.due_day
                inv.payment_id = new_uuid(rng)
                changed = v.account_changed_day
                inv.paid_iban = (
                    v.previous_ibans[0] if changed is not None and inv.paid_day < changed else v.iban
                )
            ap.append(inv)

    if spec.stress == "NONE":
        _assign_discounts(rng, ap)

    customers = _build_customers(spec, rng, vendors)
    ar = _build_ar(spec, rng, customers)
    opex_items, opex_debits = _build_opex(spec, rng)
    return Twin(
        spec=spec,
        tenant_id=tenant_id,
        bank_iban=bank_iban,
        vendors=vendors,
        customers=customers,
        ap=ap,
        ar=ar,
        opex_items=opex_items,
        opex_debits=opex_debits,
        opening_e2e=new_uuid(rng),
    )


def _assign_discounts(rng: random.Random, ap: list[ApInvoice]) -> None:
    """Early-payment discounts on open AP whose discount window is still open at as-of."""
    offers = list(DISCOUNT_OFFERS)
    rng.shuffle(offers)
    eligible = [
        inv
        for inv in ap
        if inv.is_open and inv.due_day - inv.issue_day == 30 and inv.issue_day + 15 >= AS_OF_DAY + 1
    ]
    eligible.sort(key=lambda inv: inv.invoice_id)
    for inv, (bp, days) in zip(eligible, offers, strict=False):
        if inv.issue_day + days < AS_OF_DAY:
            continue
        # Whole hundreds of dollars: discount amounts are exact integers for every offer.
        inv.amount_minor = max(10000, (inv.amount_minor // 10000) * 10000)
        inv.lines = [inv.amount_minor]
        inv.discount_bp = bp
        inv.discount_days = days


def _build_customers(spec: SectorSpec, rng: random.Random, vendors: list[Vendor]) -> list[Customer]:
    weekly_cost = spec.payroll_weekly_minor + sum(a for _, a in spec.bundle) // 4
    for v in vendors:
        if v.has_history and v.role != "big":
            weekly_cost += v.base_minor * 7 // v.cadence_days
    weekly_revenue = weekly_cost * spec.revenue_factor_pct // 100
    total_weight = sum(c.weight for c in spec.customers)
    customers: list[Customer] = []
    for cs in spec.customers:
        base = weekly_revenue * cs.weight * cs.cadence_days // (total_weight * 7)
        customers.append(
            Customer(
                customer_id=new_uuid(rng),
                name=cs.name,
                cadence_days=cs.cadence_days,
                terms_days=cs.terms_days,
                on_time_pct=cs.on_time_pct,
                base_minor=base,
            )
        )
    return customers


def _build_ar(spec: SectorSpec, rng: random.Random, customers: list[Customer]) -> list[ArInvoice]:
    ar: list[ArInvoice] = []
    seq = 1000 + rng.randrange(9000)
    prefix = ref_prefix(spec.company)
    for c in customers:
        offset = rng.randrange(c.cadence_days)
        for issue in range(offset, HISTORY_DAYS, c.cadence_days):
            amount = jitter(rng, c.base_minor, 900, 1100)
            due = issue + c.terms_days
            if rng.randrange(100) < c.on_time_pct:
                paid = max(issue + 1, due - rng.randint(0, 3))
            else:
                paid = due + rng.randint(4, 21)
            seq += 1
            is_paid = paid < AS_OF_DAY
            ar.append(
                ArInvoice(
                    invoice_id=new_uuid(rng),
                    customer_id=c.customer_id,
                    reference=f"{prefix}-{seq}",
                    issue_day=issue,
                    due_day=due,
                    amount_minor=amount,
                    paid_day=paid if is_paid else None,
                    end_to_end_id=new_uuid(rng) if is_paid else None,
                )
            )
    return ar


def _build_opex(spec: SectorSpec, rng: random.Random) -> tuple[list[OpexItem], list[OpexDebit]]:
    """Weekly payroll plus a 28-day bundle debited on one day: periods divide 84 days exactly, so the
    12-week median weekly opex is the payroll amount regardless of week alignment."""
    payroll_day = rng.randrange(7)
    bundle_day = rng.randrange(28)
    items = [OpexItem("Payroll (net wages run)", spec.payroll_weekly_minor, 7, payroll_day)]
    items += [OpexItem(payee, amount, 28, bundle_day) for payee, amount in spec.bundle]
    debits: list[OpexDebit] = []
    for item in items:
        for day in item.days(HISTORY_DAYS):
            debits.append(OpexDebit(day, item.payee, item.amount_minor, new_uuid(rng)))
    debits.sort(key=lambda d: (d.day, d.payee))
    return items, debits
