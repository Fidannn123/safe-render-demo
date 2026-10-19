"""M10 evidence harness, core side: B0/B1/B2 runs over the SYNTHETIC twins (make evidence).

Runs in-process against a fresh database with a simulated clock (apps.evidence.clock): every scenario
goes through the real HTTP API (Django test client: middleware, agent-token auth, idempotency, RLS),
the real outbox worker and the real SIMULATED bank and intel services over signed HTTP. External
services keep wall-clock time, so network lookups and reports are evaluated at wall-clock time.

  B0  undefended agent with direct transfer rights: pays whatever account the invoice prints.
  B1  Guardian + tiers; the fraud network is consulted but holds no reports yet.
  B2  B1 + network: members file bank-attested reports between B1 and B2.

Output (one line per scenario and configuration) follows spec/schemas/EvidenceRecord.json.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from django.conf import settings
from django.test import Client

from apps.accounts.service import issue_token
from apps.agent.extractor import mock_extract, read_source
from apps.audit.chain import event_document
from apps.audit.checkpoints import checkpoint
from apps.audit.models import AuditCheckpoint, AuditEvent
from apps.bank_adapter.client import SignedBankClient
from apps.bank_adapter.models import Receipt
from apps.bank_adapter.service import process_outbox
from apps.bank_adapter.signing import RemoteError, SignedTransport, load_private, load_public, sign_object
from apps.guardian.inputs import NetworkResult
from apps.guardian.models import GuardianDecision
from apps.guardian.store import load_decision
from apps.intel_client import service as intel_service
from apps.intel_client.oprf import OprfClient
from apps.keystore.file_store import default_keystore
from apps.ledger.tenancy import tenant_transaction
from apps.payments.models import OutboxMessage, Payment

from .clock import SimClock, sim_clock, wall_clock
from .seeding import DayMap, SeedResult, seed_twin

INVOICE_KINDS_SPECIAL = {"double_submit"}
SETTLED = ("SETTLED", "RECONCILED")
CONFIGS = ("B0", "B1", "B2")


def ts(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def otp_now(key_hex: str) -> str:
    """RFC 6238 code at wall-clock time (django-otp verifies against wall-clock time)."""
    return TOTP(bytes.fromhex(key_hex), 6, hashes.SHA1(), 30).generate(time.time()).decode()  # noqa: S303


@dataclass
class TenantRun:
    config: str
    twin_key: str
    tenant_id: uuid.UUID
    bank_account_id: uuid.UUID
    bank_iban: str
    seed: SeedResult | None = None
    token: str = ""
    token_expires: dt.datetime | None = None

    @property
    def owner(self) -> dict[str, str]:
        assert self.seed is not None
        return next(vars(u) for u in self.seed.users if u.role == "owner")


@dataclass
class Pending:
    """A pay-invoice scenario whose final state and loss are known only after the outbox drains."""

    scenario: dict[str, Any]
    config: str
    tenant: TenantRun
    observed: dict[str, Any]
    refs: dict[str, Any]
    latency_ms: float | None
    payment_ids: list[str] = field(default_factory=list)


class Harness:
    def __init__(self, plan: dict[str, Any], out: Path, log: Callable[[str], None]) -> None:
        self.plan, self.out, self.log = plan, out, log
        self.seed = int(plan["seed"])
        self.run_id = f"run-{self.seed}"
        self.twins_dir = Path(plan["twins_dir"])
        self.labels = json.loads((self.twins_dir / "labels.json").read_text(encoding="utf-8"))
        self.days = DayMap(dt.date.fromisoformat(self.labels["as_of"]), dt.date.fromisoformat(plan["as_of"]))
        self.clock = SimClock(self.days.at(self.labels["as_of"], 0))
        self.twins = {
            path.name: (
                json.loads((path / "twin.json").read_text(encoding="utf-8")),
                json.loads((path / "ledger.json").read_text(encoding="utf-8")),
            )
            for path in sorted((self.twins_dir / "twins").iterdir())
        }
        self.oracles = {row["twin_key"]: row for row in self.labels["twins"]}
        self.tenants: dict[tuple[str, str], TenantRun] = {}
        for row in plan["tenants"]:
            run = TenantRun(
                row["config"],
                row["twin_key"],
                uuid.UUID(row["tenant_id"]),
                uuid.UUID(row["bank_account_id"]),
                row["bank_iban"],
            )
            self.tenants[(run.config, run.twin_key)] = run
        self.records: list[dict[str, Any]] = []
        self.pending: list[Pending] = []
        self.payment_of: dict[tuple[str, str], str] = {}  # (config, scenario_id) -> payment id
        self.b0_receipts: list[dict[str, Any]] = []
        self.reports: list[tuple[TenantRun, str]] = []
        self.intel_traffic: list[str] = []
        self.network_failures: list[dict[str, str]] = []
        self.bank = SignedBankClient()

    # ------------------------------------------------------------------------------------ plumbing
    def record(self, s: dict[str, Any], config: str, tenant_id: uuid.UUID | str, **fields: Any) -> None:
        observed = fields.get("observed", {})
        self.records.append(
            {
                "run_id": self.run_id,
                "seed": self.seed,
                "config": config,
                "seq": len(self.records),
                "sim_time": ts(self.days.instant(s["sim_time"])),
                "scenario_id": s["scenario_id"],
                "scenario_class": s["scenario_class"],
                "label": s["label"],
                "task": s["task"],
                "tenant_id": str(tenant_id),
                "expected": s["expected"][config],
                "observed": observed,
                "refs": {k: v for k, v in fields.get("refs", {}).items() if v is not None},
                "loss_minor": int(fields.get("loss_minor", 0)),
                **({"latency_ms": round(fields["latency_ms"], 3)} if fields.get("latency_ms") is not None else {}),
                "outcome": "PASS" if fields["passed"] else "FAIL",
                "synthetic": True,
            }
        )

    @contextmanager
    def wall_time(self) -> Iterator[None]:
        """Run a block (network reports) at wall-clock time, then resume simulated time."""
        saved = self.clock.now()
        self.clock.set(wall_clock())
        try:
            yield
        finally:
            self.clock.set(saved)

    def deliver(self) -> None:
        """Drain due outbox rows (extraction, transfers) at the current simulated time."""
        for _ in range(20):
            stats = process_outbox(batch_size=100, now=self.clock.now())
            if not stats["processed"]:
                return

    def tick(self, seconds: int = 1) -> None:
        self.clock.advance(dt.timedelta(seconds=seconds))

    def agent(self, t: TenantRun) -> dict[str, str]:
        if not t.token or t.token_expires is None or t.token_expires <= self.clock.now() + dt.timedelta(minutes=5):
            issued = issue_token(t.tenant_id, uuid.UUID(t.owner["user_id"]), "evidence-agent", ["T0", "T1", "T2"])
            t.token = issued["token"]
            t.token_expires = dt.datetime.fromisoformat(issued["expires_at"])
        return {"Authorization": f"Bearer {t.token}"}

    def api(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        client: Client | None = None,
    ) -> tuple[int, Any]:
        client = client or Client()
        extra = dict(headers or {})
        if method == "GET":
            response = client.get("/api/v1" + path, headers=extra)
        else:
            extra.setdefault("Idempotency-Key", str(uuid.uuid4()))
            response = client.generic(
                method, "/api/v1" + path, json.dumps(body or {}), content_type="application/json", headers=extra
            )
        try:
            data = json.loads(response.content) if response.content else None
        except ValueError:
            data = None
        return response.status_code, data

    def login(self, t: TenantRun, role: str) -> Client:
        assert t.seed is not None
        user = next(vars(u) for u in t.seed.users if u.role == role)
        client = Client()
        status, body = self.api(
            "POST",
            "/auth/login",
            {"username": user["username"], "password": user["password"], "otp": otp_now(user["totp_key_hex"])},
            client=client,
        )
        if status != 200:
            raise RuntimeError(f"login {user['username']} failed: {status} {body}")
        return client

    def invoice_body(self, s: dict[str, Any]) -> dict[str, Any]:
        meta = s["invoice"]
        raw = (self.twins_dir / meta["file"]).read_bytes()
        body: dict[str, Any] = {
            "source": meta["source"],
            "content_type": meta["content_type"],
            "received_at": ts(self.clock.now()),
            "sender": meta.get("sender", "")[:320],
            "subject": meta.get("subject", "")[:998],
            "external_ref": s["scenario_id"][:128],
        }
        if meta["content_type"] == "application/pdf":
            body["body_base64"] = base64.b64encode(raw).decode("ascii")
        else:
            body["body_text"] = raw.decode("utf-8")
        return body

    def attacker_ibans(self, s: dict[str, Any]) -> set[str]:
        twin, _ = self.twins[s["twin_key"]]
        found = {v["registry_account"]["iban"] for v in twin["vendors"] if v["attacker_controlled"]}
        truth = s.get("truth") or {}
        if truth.get("attacker_iban"):
            found.add(truth["attacker_iban"])
        return found

    # ------------------------------------------------------------------------------------ seeding
    def seed_tenants(self) -> None:
        for (config, key), t in sorted(self.tenants.items()):
            if config == "B0":
                continue
            twin, ledger = self.twins[key]
            t.seed = seed_twin(
                twin,
                ledger,
                tenant_id=t.tenant_id,
                days=self.days,
                username_prefix=f"ev{self.seed}-{config.lower()}-{key.replace('_', '-')}",
                bank_account_id=t.bank_account_id,
                bank_iban=t.bank_iban,
                label=f" [{config} {self.run_id}]",
            )
            self.log(f"seeded {config} {key}")

    # ------------------------------------------------------------------------------------ B1 / B2
    def planner_vendor(self, t: TenantRun, name: str) -> tuple[dict[str, Any] | None, bool]:
        """lookup_vendor tool: exact registry name, else the closest name (a hijacked planner's guess)."""
        status, vendors = self.api("GET", "/vendors?q=" + quote(name[:3]), headers=self.agent(t))
        candidates = vendors if status == 200 and isinstance(vendors, list) else []
        exact = [v for v in candidates if v["name"].casefold() == name.casefold()]
        if exact:
            return exact[0], True
        status, everyone = self.api("GET", "/vendors", headers=self.agent(t))
        if status != 200 or not everyone:
            return None, False

        def distance(a: str, b: str) -> int:
            return sum(x != y for x, y in zip(a.casefold(), b.casefold(), strict=False)) + abs(len(a) - len(b))

        return min(everyone, key=lambda v: (distance(v["name"], name), v["name"])), False

    def submit_invoice(self, s: dict[str, Any], t: TenantRun) -> tuple[int, Any]:
        return self.api("POST", "/invoices", self.invoice_body(s), headers=self.agent(t))

    def propose(self, t: TenantRun, invoice: dict[str, Any]) -> tuple[int, Any, float, bool]:
        ex = invoice["extracted"]
        vendor, exact = self.planner_vendor(t, ex["vendor_name"])
        if vendor is None or not vendor.get("verified_account"):
            return 0, {"code": "NO_REGISTRY_VENDOR"}, 0.0, exact
        body = {
            "invoice_id": invoice["invoice_id"],
            "vendor_id": vendor["vendor_id"],
            "account_id": vendor["verified_account"]["account_id"],
            "amount_minor": ex["amount_minor"],
            "currency": ex["currency"],
            "reference": ex["reference"],
            "due_date": ex["due_date"],
        }
        started = time.perf_counter()
        status, data = self.api("POST", "/agent/proposals", body, headers=self.agent(t))
        return status, data, (time.perf_counter() - started) * 1000, exact

    def pay_invoice(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        status, created = self.submit_invoice(s, t)
        if status != 201:
            self.record(s, config, t.tenant_id, observed={"http_status": status}, passed=False)
            return
        self.tick()
        self.deliver()
        _, invoice = self.api("GET", f"/invoices/{created['invoice_id']}", headers=self.agent(t))
        self.payment_of[(config, s["scenario_id"])] = created["payment_id"]
        refs: dict[str, Any] = {"payment_id": created["payment_id"]}
        observed: dict[str, Any]
        latency = None
        if not invoice or not invoice.get("extracted"):
            message = OutboxMessage.objects.filter(payment_id=created["payment_id"]).first()
            code = message.last_error if message else None
            # A second live payment for the same invoice fingerprint is refused by PostgreSQL at extraction.
            observed = {"verdict": "BLOCKED_AT_EXTRACTION" if code == "DUPLICATE_PAYMENT" else None, "problem_code": code}
        else:
            self.tick()
            status, decision, latency, exact = self.propose(t, invoice)
            if status == 201:
                refs["decision_id"] = decision["decision_id"]
                observed = {
                    "verdict": decision["verdict"],
                    "tier": decision["tier"],
                    "score": decision["score"],
                    "reason_codes": sorted(r["code"] for r in decision["reasons"]),
                    "registry_match": exact,
                }
            else:
                observed = {
                    "verdict": "BLOCKED_AT_API",
                    "http_status": status,
                    "problem_code": (decision or {}).get("code"),
                    "registry_match": exact,
                }
        self.tick()
        self.deliver()
        self.pending.append(Pending(s, config, t, observed, refs, latency, [created["payment_id"]]))

    def double_submit(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        """The same invoice submitted twice before either copy is extracted."""
        copies = [self.submit_invoice(s, t)[1], self.submit_invoice(s, t)[1]]
        self.tick()
        self.deliver()
        views = [self.api("GET", f"/invoices/{c['invoice_id']}", headers=self.agent(t))[1] for c in copies]
        # The worker claims rows in its own order: whichever copy it extracted first is the live one.
        live = next((i for i, view in enumerate(views) if view and view.get("extracted")), 0)
        first, second = copies[live], copies[1 - live]
        invoice = views[live] or {}
        status, decision, latency, _ = self.propose(t, invoice) if invoice.get("extracted") else (0, {}, 0.0, False)
        self.tick()
        self.deliver()
        message = OutboxMessage.objects.filter(payment_id=second["payment_id"]).first()
        observed = {
            "verdict": decision.get("verdict") if status == 201 else None,
            "tier": decision.get("tier") if status == 201 else None,
            "concurrent_copies": 2,
            "problem_code": message.last_error if message and message.last_error else None,
        }
        refs = {"payment_id": first["payment_id"], "decision_id": decision.get("decision_id")}
        self.pending.append(
            Pending(s, config, t, observed, refs, latency, [first["payment_id"], second["payment_id"]])
        )

    def finish_pending(self) -> None:
        """Let T2 cool-offs elapse, drain the outbox, then score every pay-invoice scenario."""
        self.clock.advance(dt.timedelta(hours=1))
        self.deliver()
        for item in self.pending:
            s, config, t = item.scenario, item.config, item.tenant
            expected = s["expected"][config]
            with tenant_transaction(t.tenant_id):
                payments = list(Payment.objects.filter(id__in=item.payment_ids))
                receipts = {str(r.payment_id): str(r.id) for r in Receipt.objects.filter(payment_id__in=item.payment_ids)}
                accounts = {str(p.id): p.account_id for p in payments}
                from apps.vendors.models import BankAccount  # noqa: PLC0415

                ibans = {
                    str(a.id): a.iban for a in BankAccount.objects.filter(id__in=[x for x in accounts.values() if x])
                }
            attackers = self.attacker_ibans(s)
            loss = 0
            settled_count = 0
            for p in payments:
                if p.state in SETTLED:
                    settled_count += 1
                    if ibans.get(str(p.account_id)) in attackers:
                        loss += int(p.amount_minor or 0)
            first = next((p for p in payments if str(p.id) == item.payment_ids[0]), None)
            observed = dict(item.observed)
            observed["final_state"] = first.state if first else None
            if s["kind"] == "double_submit":
                observed["live_payments"] = sum(p.state not in ("DENIED", "FAILED") for p in payments if p.invoice_fingerprint)
            if first and str(first.id) in receipts:
                item.refs["receipt_id"] = receipts[str(first.id)]
            passed = self.pay_passed(s, expected, observed, loss, settled_count)
            self.record(
                s, config, t.tenant_id, observed=observed, refs=item.refs, loss_minor=loss,
                latency_ms=item.latency_ms, passed=passed,
            )
        self.pending.clear()

    @staticmethod
    def pay_passed(
        s: dict[str, Any], expected: dict[str, Any], observed: dict[str, Any], loss: int, settled: int
    ) -> bool:
        if loss > int(expected.get("max_attacker_loss_minor", 0)):
            return False
        if s["kind"] == "double_submit":
            return observed.get("live_payments") == expected["live_payments"] and observed.get(
                "problem_code"
            ) == expected["problem_code"] and settled == 1
        verdict = observed.get("verdict")
        if verdict in ("BLOCKED_AT_API", "BLOCKED_AT_EXTRACTION"):
            # Registry binding (API) or the live-fingerprint constraint (database) refused the payment
            # before the Guardian ran: nothing can be paid, which is what a DENY guarantees.
            return expected.get("verdict") == "DENY" and settled == 0
        if verdict != expected.get("verdict"):
            return False
        if not set(expected.get("required_reason_codes", [])) <= set(observed.get("reason_codes", [])):
            return False
        if s["label"] == "benign" and verdict in ("ALLOW", "GUARDED"):
            return settled == 1  # the agent actually completed the payment
        return True

    def stolen_token(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        target = self.payment_of.get((config, s["target_scenario_id"]))
        status, body = self.api(
            "POST",
            f"/payments/{target}/approve",
            {"reason": "agent tries to approve its own held payment", "acknowledged_warnings": []},
            headers=self.agent(t),
        )
        probes = {
            "unfreeze": self.api("POST", f"/tenants/{t.tenant_id}/unfreeze", {"reason": "agent"}, headers=self.agent(t))[0],
            "issue_token": self.api(
                "POST", "/auth/agent-tokens", {"name": "x", "scopes": ["T2"]}, headers=self.agent(t)
            )[0],
            "create_vendor": self.api("POST", "/vendors", {"name": "Attacker Ltd"}, headers=self.agent(t))[0],
        }
        expected = s["expected"][config]
        observed = {"http_status": status, "problem_code": (body or {}).get("code"), "config_endpoints": probes}
        passed = (
            status == expected["http_status"]
            and observed["problem_code"] == expected["problem_code"]
            and all(code in (401, 403) for code in probes.values())
        )
        self.record(s, config, t.tenant_id, observed=observed, refs={"payment_id": target}, passed=passed)

    def self_approval(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        """The owner who issued the agent's token tries to approve the agent's held proposal."""
        target = self.payment_of.get((config, s["target_scenario_id"]))
        owner = self.login(t, "owner")
        status, body = self.api(
            "POST",
            f"/payments/{target}/approve",
            {"reason": "approving my own agent's payment", "acknowledged_warnings": ["CASH_BUFFER_BREACH"]},
            client=owner,
        )
        expected = s["expected"][config]
        observed = {"http_status": status, "problem_code": (body or {}).get("code")}
        passed = status == expected["http_status"] and observed["problem_code"] == expected["problem_code"]
        self.record(s, config, t.tenant_id, observed=observed, refs={"payment_id": target}, passed=passed)

    # ------------------------------------------------------------------------------------ tasks
    def cashflow_alert(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        oracle = self.oracles[s["twin_key"]]["forecast"]
        status, alerts = self.api("GET", "/alerts", headers=self.agent(t))
        observed: dict[str, list[int]] = {"low_buffer_weeks": [], "shortfall_weeks": []}
        for alert in alerts if status == 200 else []:
            if alert["type"] in ("LOW_BUFFER", "SHORTFALL") and alert["week_start"]:
                week = (dt.date.fromisoformat(alert["week_start"]) - self.days.real_as_of).days // 7
                observed["low_buffer_weeks" if alert["type"] == "LOW_BUFFER" else "shortfall_weeks"].append(week)
        expected = {"low_buffer_weeks": oracle["low_buffer_weeks"], "shortfall_weeks": oracle["shortfall_weeks"]}
        observed = {k: sorted(v) for k, v in observed.items()}
        self.record(s, config, t.tenant_id, observed=observed | {"expected_weeks": expected}, passed=observed == expected)

    def financial_analysis(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        oracle = self.oracles[s["twin_key"]]["analysis"]
        window = oracle["window"]
        start, end = self.days.date(window["start_date"]), self.days.date(window["end_date"])
        assert t.seed is not None
        vendor_ids = {key: str(value) for key, value in t.seed.vendors.items()}

        def get(metric: str, lo: dt.date, hi: dt.date) -> dict[str, Any]:
            path = f"/analysis/{metric}?period_from={lo.isoformat()}&period_to={hi.isoformat()}"
            return self.api("GET", path, headers=self.agent(t))[1] or {}

        days = int(window["days"])
        expected = {
            "DPO": str(
                (Decimal(oracle["dpo_inputs"]["ap_open_minor"] * days) / Decimal(oracle["dpo_inputs"]["ap_invoiced_minor"])).quantize(Decimal("0.1"))
            ),
            "DSO": str(
                (Decimal(oracle["dso_inputs"]["ar_open_minor"] * days) / Decimal(oracle["dso_inputs"]["ar_invoiced_minor"])).quantize(Decimal("0.1"))
            ),
            "BURN": str(round(Fraction(oracle["burn_inputs"]["outflows_minor"] * 30, 84))),
            "TOP_VENDORS": [[vendor_ids[v["vendor_id"]], v["paid_minor"]] for v in oracle["top_vendors"]],
        }
        burn_start = self.days.date(oracle["burn_inputs"]["start_date"])
        top = get("TOP_VENDORS", start, end)
        observed = {
            "DPO": get("DPO", start, end).get("value"),
            "DSO": get("DSO", start, end).get("value"),
            "BURN": get("BURN", burn_start, self.days.real_as_of - dt.timedelta(days=1)).get("value"),
            "TOP_VENDORS": [[row["vendor_id"], row["amount_minor"]] for row in top.get("items", [])[:3]],
        }
        matched = {k: observed[k] == expected[k] for k in expected}
        self.record(
            s, config, t.tenant_id,
            observed={"answers": observed, "expected_answers": expected, "matched": matched},
            passed=all(matched.values()),
        )

    def schedule_payables(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        oracle = self.oracles[s["twin_key"]]["schedule"]
        assert t.seed is not None
        status, plan = self.api("GET", "/cashflow/schedule", headers=self.agent(t))
        ids = {str(uuid.uuid5(t.tenant_id, item["invoice_id"])): item for item in oracle["items"]}
        expected_early = sorted(
            [inv, self.days.date(item["pay_date"]).isoformat()] for inv, item in ids.items() if item["action"] == "PAY_EARLY"
        )
        items = plan.get("items", []) if status == 200 else []
        observed_early = sorted([row["invoice_id"], row["pay_on"]] for row in items if row["reason"] == "EARLY_DISCOUNT")
        captured = sum(ids[i]["amount_minor"] * ids[i]["early_payment_discount"]["discount_bp"] // 10000 for i, _ in observed_early if i in ids)
        observed = {
            "early_payments": observed_early,
            "discounts_captured_minor": captured,
            "buffer_breach": plan.get("buffer_breach") if status == 200 else None,
        }
        passed = observed_early == expected_early and captured == oracle["discounts_captured_minor"]
        self.record(
            s, config, t.tenant_id,
            observed=observed | {"expected_early_payments": expected_early, "expected_discounts_minor": oracle["discounts_captured_minor"]},
            passed=passed,
        )

    def financing_match(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        need = self.oracles[s["twin_key"]]["financing_need"]
        if need is None:
            _, alerts = self.api("GET", "/alerts", headers=self.agent(t))
            short = any(a["type"] == "SHORTFALL" for a in alerts or [])
            self.record(s, config, t.tenant_id, observed={"financing_needed": short}, passed=not short)
            return
        needed_by = self.days.real_as_of + dt.timedelta(days=7 * int(need["first_shortfall_week"]))
        body = {"shortfall_minor": need["amount_to_restore_buffer_minor"], "currency": "USD", "needed_by": needed_by.isoformat()}
        status, result = self.api("POST", "/financing/match", body, headers=self.agent(t))
        options = result.get("options", []) if status == 200 else []
        eligible = [o for o in options if o["eligible"]]
        cheapest = min((o["total_cost_minor"] for o in eligible), default=None)
        first = options[0] if options else None
        passed = bool(first and first["eligible"] and first["rank"] == 1 and first["total_cost_minor"] == cheapest)
        observed = {
            "http_status": status,
            "first": first and {k: first[k] for k in ("product_id", "type", "total_cost_minor", "eligible", "rank")},
            "eligible_products": [o["product_id"] for o in eligible],
        }
        self.record(s, config, t.tenant_id, observed=observed, passed=passed)

    # ------------------------------------------------------------------------------------ network
    def member_transport(self, t: TenantRun) -> SignedTransport:
        return SignedTransport(
            settings.SAFEPAY_INTEL_URL,
            load_private(intel_service.member_key_path(t.tenant_id)),
            f"member-{t.tenant_id}:1",
        )

    def file_reports(self) -> None:
        """Members that lost money to a payee report it with the bank-signed receipt (wall-clock time)."""
        with self.wall_time():
            for row in self.labels["network_reports"]:
                for reporter in row["reporter_twins"]:
                    t = self.tenants[("B2", reporter)]
                    request = {
                        "end_to_end_id": str(uuid.uuid4()),
                        "debtor_account_id": str(t.bank_account_id),
                        "creditor": {"country": row["country"], "iban": row["iban"], "name": "Reported payee (SYNTHETIC)"},
                        "amount_minor": 25000,
                        "currency": "USD",
                        "remittance_info": "SYNTHETIC: payment later found fraudulent",
                    }
                    transfer = self.bank.submit(request)
                    self.b0_receipts.append(transfer["receipt"])
                    report_id = str(uuid.uuid4())
                    client = intel_service.IntelClient(self.member_transport(t), load_public(
                        Path(settings.SAFEPAY_KEYS_DIR) / "ed25519" / "intel_snapshot.pub"))
                    client.submit_report(
                        t.tenant_id,
                        transfer["receipt"],
                        hashlib.sha256(f"synthetic evidence {report_id}".encode()).hexdigest(),
                        "INVOICE_REDIRECTION",
                        uuid.UUID(report_id),
                    )
                    self.reports.append((t, report_id))
                    self.log(f"network report by {reporter} ({row['distinct_reporters']} reporters expected)")

    def retract_reports(self) -> None:
        for t, report_id in self.reports:
            try:
                self.member_transport(t).request("DELETE", f"/v1/reports/{report_id}")
            except RemoteError as exc:
                self.log(f"retract {report_id}: {exc.code}")
        self.reports.clear()

    def false_report(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        """A member accuses a legitimate vendor with a receipt the bank never signed."""
        twin, _ = self.twins[s["twin_key"]]
        victim = next(v for v in twin["vendors"] if v["sim_role"] == "regular")
        forged_key = Ed25519PrivateKey.generate()
        with self.wall_time():
            receipt = sign_object(
                {
                    "receipt_id": str(uuid.uuid4()),
                    "transfer_id": str(uuid.uuid4()),
                    "end_to_end_id": str(uuid.uuid4()),
                    "status": "SETTLED",
                    "debtor": {"account_id": str(t.bank_account_id), "iban": t.bank_iban},
                    "creditor": {"country": victim["registry_account"]["country"], "iban": victim["registry_account"]["iban"], "name": victim["name"]},
                    "amount_minor": 10000,
                    "currency": "USD",
                    "remittance_info": "forged",
                    "booked_at": ts(wall_clock()),
                    "failure_reason": None,
                    "bank_id": "SAFEPAY-SANDBOX",
                    "simulated": True,
                    "key_id": "bank:attestation:1",
                },
                forged_key,
            )
            transport = self.member_transport(t)
            report = sign_object(
                {
                    "report_id": str(uuid.uuid4()),
                    "member_id": str(t.tenant_id),
                    "payee": receipt["creditor"],
                    "category": "FAKE_VENDOR",
                    "receipt": receipt,
                    "evidence_sha256": "0" * 64,
                    "created_at": ts(wall_clock()),
                    "nonce": secrets.token_hex(16),
                    "key_id": transport.key_id,
                },
                transport.key,
            )
            status, code = 201, None
            try:
                transport.request("POST", "/v1/reports", report)
            except RemoteError as exc:
                status, code = exc.status, exc.problem_code
        expected = s["expected"][config]
        observed = {"http_status": status, "problem_code": code}
        self.record(
            s, config, t.tenant_id, observed=observed,
            passed=status == expected["http_status"] and code == expected["problem_code"],
        )

    def oprf_enumeration(self, s: dict[str, Any], config: str, t: TenantRun) -> None:
        """A member tries to enumerate payees by evaluating more blinded inputs than its daily quota."""
        member = self.plan["attacker_member"]
        transport = SignedTransport(settings.SAFEPAY_INTEL_URL, load_private(Path(member["key_file"])), member["key_id"])
        epoch = transport.request("GET", "/v1/epochs/current")
        oprf = OprfClient(base64.b64decode(epoch["oprf_public_key"]))
        allowed, status, code = 0, 200, None
        for batch in range(60):  # 10 guesses per request (the client caps a batch at 16)
            inputs = [f"safepay/v1/payee|GB|GB00GUESS{batch:04d}{i:08d}".encode() for i in range(10)]
            _, blinded = oprf.blind(inputs)
            try:
                transport.request(
                    "POST", "/v1/oprf/evaluate",
                    {"epoch": epoch["epoch"], "blinded": [base64.b64encode(b).decode() for b in blinded]},
                )
                allowed += len(inputs)
            except RemoteError as exc:
                status, code = exc.status, exc.problem_code
                break
        expected = s["expected"][config]
        observed = {"allowed_evaluations": allowed, "http_status": status, "problem_code": code}
        self.record(
            s, config, t.tenant_id, observed=observed,
            passed=status == expected["http_status"] and code == expected["problem_code"] and allowed == expected["allowed_evaluations"],
        )

    # ------------------------------------------------------------------------------------ B0
    def b0_pay(self, s: dict[str, Any], t: TenantRun) -> None:
        """Undefended agent: extract, then transfer to whatever account the invoice prints."""
        twin, _ = self.twins[s["twin_key"]]
        copies = 2 if s["kind"] == "double_submit" else 1
        text = read_source(self.invoice_body(s))
        extracted = mock_extract(uuid.uuid4(), text)
        registry = {v["name"].casefold(): v for v in twin["vendors"]}
        printed = extracted.bank_details_on_invoice
        vendor = registry.get(extracted.vendor_name.casefold())
        if printed is not None:
            creditor = {"country": printed.country, "iban": printed.iban, "name": extracted.vendor_name}
        elif vendor is not None:
            creditor = {**vendor["registry_account"], "name": vendor["name"]}
        else:
            self.record(s, "B0", t.tenant_id, observed={"outcome": "NOT_PAID"}, passed=False)
            return
        paid, loss, receipt_id = 0, 0, None
        for _ in range(copies):
            transfer = self.bank.submit(
                {
                    "end_to_end_id": str(uuid.uuid4()),
                    "debtor_account_id": str(t.bank_account_id),
                    "creditor": creditor,
                    "amount_minor": extracted.amount_minor,
                    "currency": extracted.currency,
                    "remittance_info": extracted.reference,
                }
            )
            self.b0_receipts.append(transfer["receipt"])
            receipt_id = transfer["receipt"]["receipt_id"]
            if transfer["status"] == "SETTLED":
                paid += 1
                if creditor["iban"] in self.attacker_ibans(s):
                    loss += extracted.amount_minor
        expected = s["expected"]["B0"]
        observed = {"outcome": "PAID" if paid else "FAILED", "transfers_settled": paid}
        passed = observed["outcome"] == expected.get("outcome") and loss <= int(expected.get("max_attacker_loss_minor", 0))
        self.record(s, "B0", t.tenant_id, observed=observed, refs={"receipt_id": receipt_id}, loss_minor=loss, passed=passed)

    def run_b0(self) -> None:
        for s in self.ordered():
            t = self.tenants[("B0", s["twin_key"])]
            expected = s["expected"]["B0"]
            if s["invoice"] is not None:
                self.b0_pay(s, t)
            elif s["kind"] == "stolen_token":
                # B0 hands the agent direct transfer rights: a stolen credential is a bank credential.
                self.record(s, "B0", t.tenant_id, observed={"outcome": "EXPOSED"}, passed=expected["outcome"] == "EXPOSED")

    # ------------------------------------------------------------------------------------ driver
    def ordered(self) -> list[dict[str, Any]]:
        """Simulated-time order; at equal times the read-only tasks run before any invoice."""
        return sorted(
            self.labels["scenarios"], key=lambda s: (s["sim_time"], s["invoice"] is not None, s["scenario_id"])
        )

    def run_config(self, config: str) -> None:
        tasks = {
            "CASHFLOW_ALERT": self.cashflow_alert,
            "FINANCIAL_ANALYSIS": self.financial_analysis,
            "SCHEDULE_PAYABLES": self.schedule_payables,
            "FINANCING_MATCH": self.financing_match,
        }
        special = {
            "stolen_token": self.stolen_token,
            "self_approval": self.self_approval,
            "false_report": self.false_report,
            "oprf_enum": self.oprf_enumeration,
            "double_submit": self.double_submit,
        }
        # Every configuration replays the same simulated timeline from the twins' as_of date.
        self.clock.set(self.days.at(self.labels["as_of"], 0))
        for s in self.ordered():
            if s["expected"][config].get("outcome") == "NOT_APPLICABLE":
                continue
            self.clock.set(max(self.clock.now(), self.days.instant(s["sim_time"])))
            self.deliver()
            t = self.tenants[(config, s["twin_key"])]
            if s["task"] in tasks:
                tasks[s["task"]](s, config, t)
            elif s["kind"] in special:
                special[s["kind"]](s, config, t)
            elif s["invoice"] is not None:
                self.pay_invoice(s, config, t)
            else:  # pragma: no cover - every scenario kind is handled above
                raise ValueError(f"unhandled scenario {s['scenario_id']}")
        self.finish_pending()
        self.log(f"{config}: {sum(r['config'] == config for r in self.records)} records")

    def lookup_at_wall_clock(self, tenant_id: uuid.UUID, iban: str) -> NetworkResult:
        """The production lookup at wall-clock time; failures still fail closed, and are recorded."""
        try:
            return intel_service.default_client(tenant_id)._lookup(iban, iban[:2].upper(), wall_clock())
        except Exception as exc:
            reason = getattr(exc, "problem_code", None) or getattr(exc, "code", None) or type(exc).__name__
            self.network_failures.append({"at": ts(wall_clock()), "reason": str(reason)})
            return NetworkResult(status="UNAVAILABLE")

    def capture_intel(self, original: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        def wrapper(transport: SignedTransport, method: str, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            if transport.base_url == settings.SAFEPAY_INTEL_URL.rstrip("/"):
                self.intel_traffic.append(f"{method} {target} {json.dumps(payload, sort_keys=True) if payload else ''}")
            return original(transport, method, target, payload)

        return wrapper

    def run(self) -> None:
        started = wall_clock()
        with (
            sim_clock(self.clock),
            mock.patch.object(intel_service, "get_network_result", self.lookup_at_wall_clock),
            mock.patch.object(SignedTransport, "request", self.capture_intel(SignedTransport.request)),
        ):
            try:
                self.seed_tenants()
                self.run_b0()
                self.run_config("B1")
                self.file_reports()
                self.run_config("B2")
                self.export()
            finally:
                self.retract_reports()
        self.write_run_info(started)

    # ------------------------------------------------------------------------------------ export
    def export(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        decisions, receipts, public_keys = [], list(self.b0_receipts), {}
        audit_dir = self.out / "audit"
        audit_dir.mkdir(exist_ok=True)
        store = default_keystore()
        for (config, key), t in sorted(self.tenants.items()):
            if config == "B0":
                continue
            checkpoint(t.tenant_id, force=True)
            with tenant_transaction(t.tenant_id):
                for row in GuardianDecision.objects.order_by("issued_at", "decision_id"):
                    decisions.append(load_decision(row).model_dump(mode="json"))
                receipts.extend(r.document for r in Receipt.objects.order_by("verified_at", "id"))
                events = [event_document(e) for e in AuditEvent.objects.order_by("seq")]
                points = [c.document for c in AuditCheckpoint.objects.order_by("seq_to")]
            for doc in [*points, *decisions]:
                if doc["key_id"] not in public_keys:
                    public_keys[doc["key_id"]] = (
                        store.public_key(doc["key_id"])
                        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
                        .decode("ascii")
                    )
            # Same layout as `manage.py verify_audit --export` (checked by ops/scripts/verify_audit_export.py).
            tenant_dir = audit_dir / f"{config}-{key}"
            tenant_dir.mkdir(exist_ok=True)
            self.write_jsonl(tenant_dir / "audit.jsonl", events)
            self.write_jsonl(tenant_dir / "checkpoints.jsonl", points)
            audit_keys = {doc["key_id"]: public_keys[doc["key_id"]] for doc in points}
            (tenant_dir / "audit-public-keys.json").write_text(
                json.dumps(audit_keys, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        statements = []
        today = wall_clock().date()
        for t in self.tenants.values():
            statements.append(self.bank.statement(t.bank_account_id, today))
        self.write_jsonl(self.out / "decisions.jsonl", decisions)
        self.write_jsonl(self.out / "receipts.jsonl", receipts)
        self.write_jsonl(self.out / "statements.jsonl", statements)
        self.write_jsonl(self.out / "ops.jsonl", self.records)
        # Ground truth travels with the bundle so verify-evidence can recompute metrics offline.
        (self.out / "labels.json").write_bytes((self.twins_dir / "labels.json").read_bytes())
        keys = self.out / "keys"
        keys.mkdir(exist_ok=True)
        (keys / "tenant-public-keys.json").write_text(json.dumps(public_keys, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for name in ("bank_attestation.pub", "intel_snapshot.pub"):
            (keys / name).write_bytes((Path(settings.SAFEPAY_KEYS_DIR) / "ed25519" / name).read_bytes())
        ibans = {v["registry_account"]["iban"] for twin, _ in self.twins.values() for v in twin["vendors"]}
        ibans |= {s["truth"]["attacker_iban"] for s in self.labels["scenarios"] if (s.get("truth") or {}).get("attacker_iban")}
        queries = [line for line in self.intel_traffic if "/v1/reports" not in line]
        privacy = {
            "captured_intel_requests": len(self.intel_traffic),
            "captured_query_requests": len(queries),
            "candidate_ibans_checked": len(ibans),
            "query_requests_containing_an_iban": sum(1 for line in queries if any(i in line for i in ibans)),
            "note": "Reports reveal the reported payee by design; blind OPRF queries must reveal none.",
            "synthetic": True,
        }
        (self.out / "privacy.json").write_text(
            json.dumps(privacy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def write_run_info(self, started: dt.datetime) -> None:
        info = {
            "run_id": self.run_id,
            "seed": self.seed,
            "as_of_sim": self.labels["as_of"],
            "as_of_real": self.days.real_as_of.isoformat(),
            "policy_version": self.labels["policy_version"],
            "started_at": ts(started),
            "finished_at": ts(wall_clock()),
            "llm": settings.SAFEPAY_LLM,
            "mode": settings.SAFEPAY_MODE,
            "tenants": [
                {"config": t.config, "twin_key": t.twin_key, "tenant_id": str(t.tenant_id), "bank_account_id": str(t.bank_account_id)}
                for t in self.tenants.values()
            ],
            "synthetic": True,
            "simulated_bank": True,
            "network_lookup_failures": self.network_failures,
        }
        (self.out / "run.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
