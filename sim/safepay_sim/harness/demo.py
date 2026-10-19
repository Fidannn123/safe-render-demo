"""Scripted walkthrough on the LIVE stack (make demo). SYNTHETIC tenants seeded by `make seed`.

Every step goes through the public HTTP API as the agent (bearer token) or as a human (session cookie,
password + TOTP, step-up), and through the running outbox worker, SIMULATED bank and intel network.
Attack payments are rejected as fraud at the end so the demo can be run again.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.cookies
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.twofactor.totp import TOTP

from .runner import REPO, Runner

STATE = REPO / "ops" / "dev-keys" / "demo" / "demo-state.json"


class Api:
    """Minimal JSON client. Cookies are kept by hand: the dev stack serves Secure cookies over plain HTTP."""

    def __init__(self, base: str, bearer: str | None = None) -> None:
        self.base, self.bearer = base.rstrip("/") + "/api/v1", bearer
        self.cookies: dict[str, str] = {}

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            headers["X-CSRFToken"] = self.cookies.get("csrftoken", "")
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Idempotency-Key"] = str(uuid.uuid4())
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (local dev URL)
                status, raw, received = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, raw, received = error.code, error.read(), error.headers
        for value in received.get_all("Set-Cookie") or []:
            jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
            jar.load(value)
            self.cookies.update({key: morsel.value for key, morsel in jar.items()})
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None


def code(body: Any) -> str:
    return str(body.get("code", "")) if isinstance(body, dict) else ""


def otp(user: dict[str, Any], used: set[str]) -> str:
    """Current TOTP code; waits for the next 30 s step if this user already spent the current one."""
    while True:
        code = TOTP(bytes.fromhex(user["totp_key_hex"]), 6, hashes.SHA1(), 30).generate(time.time()).decode()  # noqa: S303
        if f"{user['username']}:{code}" not in used:
            used.add(f"{user['username']}:{code}")
            return code
        time.sleep(2)


class Demo:
    def __init__(self, runner: Runner, credentials: Path, base: str) -> None:
        data = json.loads(credentials.read_text(encoding="utf-8"))
        self.tenants = {t["twin_key"]: t for t in data["tenants"]}
        self.runner, self.base = runner, base
        self.used_codes: set[str] = set()
        self.twins = REPO / "evidence" / "twins-42"
        self.labels = json.loads((self.twins / "labels.json").read_text(encoding="utf-8"))
        self.state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"used": []}
        self.step_no = 0

    # ------------------------------------------------------------------ output
    def step(self, title: str) -> None:
        self.step_no += 1
        print(f"\n[{self.step_no}] {title}")

    @staticmethod
    def say(text: str) -> None:
        print(f"    {text}")

    # ------------------------------------------------------------------ actors
    def user(self, twin: str, role: str) -> dict[str, Any]:
        return next(u for u in self.tenants[twin]["users"] if u["role"] == role)

    def human(self, twin: str, role: str) -> Api:
        user = self.user(twin, role)
        api = Api(self.base)
        api.call("GET", "/auth/csrf")
        status, body = api.call(
            "POST",
            "/auth/login",
            {"username": user["username"], "password": user["password"], "otp": otp(user, self.used_codes)},
        )
        if status != 200:
            raise SystemExit(f"login {user['username']} failed: {status} {body}")
        return api

    def agent(self, owner: Api, name: str) -> Api:
        status, token = owner.call("POST", "/auth/agent-tokens", {"name": name, "scopes": ["T0", "T1", "T2"]})
        if status != 201:
            raise SystemExit(f"agent token refused: {status} {token}")
        return Api(self.base, bearer=token["token"])

    # ------------------------------------------------------------------ agent work
    def scenario(self, scenario_id: str) -> dict[str, Any]:
        return next(s for s in self.labels["scenarios"] if s["scenario_id"] == scenario_id)

    def ingest(self, agent: Api, scenario_id: str) -> dict[str, Any]:
        meta = self.scenario(scenario_id)["invoice"]
        raw = (self.twins / meta["file"]).read_bytes()
        body: dict[str, Any] = {
            "source": meta["source"],
            "content_type": meta["content_type"],
            "received_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if meta["content_type"] == "application/pdf":
            body["body_base64"] = base64.b64encode(raw).decode("ascii")
        else:
            body["body_text"] = raw.decode("utf-8")
        status, created = agent.call("POST", "/invoices", body)
        if status != 201:
            raise SystemExit(f"ingest failed: {status} {created}")
        for _ in range(40):
            _, invoice = agent.call("GET", f"/invoices/{created['invoice_id']}")
            if invoice and invoice.get("extracted"):
                return dict(invoice)
            time.sleep(1)
        raise SystemExit(
            f"{scenario_id} was not extracted (is the worker running? duplicate of a live invoice?)"
        )

    def propose(self, agent: Api, invoice: dict[str, Any]) -> tuple[int, Any]:
        ex = invoice["extracted"]
        _, vendors = agent.call("GET", "/vendors?q=" + urllib.parse.quote(ex["vendor_name"]))
        vendor = next(
            (v for v in vendors or [] if v["name"].casefold() == ex["vendor_name"].casefold()), None
        )
        if vendor is None:
            return 0, {"code": "NO_REGISTRY_VENDOR"}
        self.say(
            f"planner: {ex['vendor_name']}, {ex['currency']} {ex['amount_minor'] / 100:,.2f}, "
            f"ref {ex['reference']}; payee from the registry ({vendor['verified_account']['iban_masked']}), "
            "never from the invoice"
        )
        return agent.call(
            "POST",
            "/agent/proposals",
            {
                "invoice_id": invoice["invoice_id"],
                "vendor_id": vendor["vendor_id"],
                "account_id": vendor["verified_account"]["account_id"],
                "amount_minor": ex["amount_minor"],
                "currency": ex["currency"],
                "reference": ex["reference"],
                "due_date": ex["due_date"],
            },
        )

    def show_decision(self, status: int, decision: Any) -> None:
        if status != 201:
            self.say(f"refused: HTTP {status} {decision.get('code') if decision else ''}")
            return
        reasons = ", ".join(f"{r['code']} +{r['weight']}" for r in decision["reasons"]) or "no risk signals"
        self.say(
            f"Guardian: {decision['verdict']} {decision['tier'] or ''} score {decision['score']} ({reasons})"
        )
        self.say(f"signed Decision {decision['decision_id']} (Ed25519, key {decision['key_id']})")

    def wait_state(self, api: Api, payment_id: str, states: tuple[str, ...], seconds: int = 45) -> str:
        state = "?"
        for _ in range(seconds):
            _, payment = api.call("GET", f"/payments/{payment_id}")
            state = payment["state"] if payment else "?"
            if state in states:
                break
            time.sleep(1)
        return state

    def next_benign(self, twin: str) -> str:
        options = sorted(
            s["scenario_id"]
            for s in self.labels["scenarios"]
            if s["twin_key"] == twin and s["kind"] == "benign" and s["expected"]["B1"]["verdict"] == "ALLOW"
        )
        unused: list[str] = [s for s in options if s not in self.state["used"]]
        if not unused:
            raise SystemExit("every benign demo invoice of this twin was paid already; reset and re-seed")
        return unused[0]

    # ------------------------------------------------------------------ the walkthrough
    def run(self) -> int:
        print("SafePay live demo. SYNTHETIC companies, SIMULATED bank, mock LLM. No real money moves.")
        retail = self.tenants["retail"]
        self.step(f"{retail['company']}: the owner signs in (password + TOTP) and issues a 1 h agent token")
        owner = self.human("retail", "owner")
        agent = self.agent(owner, "payables-agent")
        self.say("token scopes T0-T2; stored server-side only as HMAC-SHA256(pepper, token)")

        benign = self.next_benign("retail")
        self.step(f"Agent pays a routine invoice ({benign})")
        invoice = self.ingest(agent, benign)
        status, decision = self.propose(agent, invoice)
        self.show_decision(status, decision)
        state = self.wait_state(agent, invoice["payment_id"], ("SETTLED", "RECONCILED", "FAILED"))
        self.say(f"outbox -> SIMULATED bank -> Ed25519-signed receipt verified: payment {state}")
        if state in ("SETTLED", "RECONCILED"):
            self.state["used"].append(benign)

        held = []
        for scenario_id, title in (
            ("retail-swap-01", "Invoice from a known vendor asks to pay a NEW bank account"),
            ("retail-hidden-01", "Invoice hides instructions to the AI and prints an attacker account"),
        ):
            self.step(f"{title} ({scenario_id})")
            invoice = self.ingest(agent, scenario_id)
            status, decision = self.propose(agent, invoice)
            self.show_decision(status, decision)
            held.append(invoice["payment_id"])

        self.step("Hijacked agent: uses its token to approve its own held payment")
        status, body = agent.call(
            "POST", f"/payments/{held[0]}/approve", {"reason": "agent", "acknowledged_warnings": []}
        )
        self.say(f"HTTP {status} {code(body)} (agent tokens cannot reach human-only endpoints)")
        status, body = agent.call("POST", f"/tenants/{retail['tenant_id']}/unfreeze", {"reason": "agent"})
        self.say(f"unfreeze with the agent token: HTTP {status} {body.get('code') if body else ''}")

        self.step("The owner who delegated to the agent tries to approve the agent's payment")
        status, body = owner.call(
            "POST", f"/payments/{held[0]}/approve", {"reason": "looks fine", "acknowledged_warnings": []}
        )
        self.say(f"HTTP {status} {code(body)} (four-eyes: an independent approver is required)")

        self.step("An independent approver (password + TOTP) rejects both as fraud")
        approver = self.human("retail", "approver")
        for payment_id in held:
            status, body = approver.call(
                "POST",
                f"/payments/{payment_id}/reject",
                {
                    "reason": "bank details changed by email; called vendor",
                    "reject_reason": "fraud_or_invalid",
                },
            )
            self.say(
                f"{payment_id[:8]}: HTTP {status} -> {body.get('state') if body else ''}, payee reportable"
            )

        self.network_step()
        self.cashflow_step()

        self.step("T4 freeze: the owner stops all outflow")
        status, body = owner.call(
            "POST", f"/tenants/{retail['tenant_id']}/freeze", {"reason": "demo incident drill"}
        )
        self.say(f"HTTP {status}: frozen={body.get('frozen') if body else None}; every agent token revoked")
        status, body = agent.call("GET", "/payments?limit=1")
        self.say(f"the old agent token now gets HTTP {status} {body.get('code') if body else ''}")
        status, body = owner.call(
            "POST", "/auth/step-up", {"otp": otp(self.user("retail", "owner"), self.used_codes)}
        )
        status, body = owner.call(
            "POST", f"/tenants/{retail['tenant_id']}/unfreeze", {"reason": "drill over"}
        )
        self.say(
            f"unfreeze needs owner + fresh step-up code: HTTP {status}, "
            f"frozen={body.get('frozen') if body else None}"
        )

        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        print("\nDone. Open the console (http://127.0.0.1:8080 with Docker, :5173 natively) to inspect")
        print(
            "decisions, the approvals queue and evidence metrics. "
            "One-time codes: python -m safepay_sim.harness otp <user>"
        )
        return 0

    def queue_hold(self, twin: str) -> int:
        """Leave one HELD payment (a bank-detail swap) in a twin's approvals queue for the console."""
        scenario = next(
            (
                s["scenario_id"]
                for s in self.labels["scenarios"]
                if s["twin_key"] == twin and s["scenario_class"] == "BANK_DETAIL_SWAP"
            ),
            None,
        )
        if scenario is None:
            raise SystemExit(f"twin {twin!r} has no bank-detail-swap scenario")
        print(f"{self.tenants[twin]['company']}: the agent proposes {scenario} (SYNTHETIC)")
        owner = self.human(twin, "owner")
        agent = self.agent(owner, "console-check")
        invoice = self.ingest(agent, scenario)
        status, decision = self.propose(agent, invoice)
        self.show_decision(status, decision)
        approver = self.user(twin, "approver")["username"]
        print(f"payment {invoice['payment_id']} waits in the approvals queue; sign in as {approver}")
        return 0 if status == 201 and decision.get("verdict") == "HOLD" else 1

    def network_step(self) -> None:
        scenario = self.scenario("pharmacy-reported-2plus-01")
        report = next(
            r for r in self.labels["network_reports"] if r["scenario_id"] == scenario["scenario_id"]
        )
        self.step("Two other businesses report a payee they lost money to (bank-signed receipts attached)")
        filed = []
        try:
            for reporter in report["reporter_twins"]:
                out = self.runner.manage(
                    "core",
                    [
                        "report_payee",
                        "--tenant",
                        self.tenants[reporter]["tenant_id"],
                        "--iban",
                        report["iban"],
                    ],
                    live=True,
                )
                filed.append((reporter, json.loads(out.strip().splitlines()[-1])["report_id"]))
                self.say(f"{self.tenants[reporter]['company']}: report admitted by the network")
            pharmacy = self.tenants["pharmacy"]
            self.step(
                f"{pharmacy['company']}: its agent receives an invoice from a vendor "
                "whose account recently changed"
            )
            owner = self.human("pharmacy", "owner")
            agent = self.agent(owner, "payables-agent")
            invoice = self.ingest(agent, scenario["scenario_id"])
            status, decision = self.propose(agent, invoice)
            self.show_decision(status, decision)
            self.say("the lookup was blind (VOPRF): the network never saw which account was checked")
            approver = self.human("pharmacy", "approver")
            approver.call(
                "POST",
                f"/payments/{invoice['payment_id']}/reject",
                {"reason": "account reported by two network members", "reject_reason": "fraud_or_invalid"},
            )
        finally:
            for reporter, report_id in filed:  # leave the shared network as we found it
                self.runner.manage(
                    "core",
                    ["report_payee", "--tenant", self.tenants[reporter]["tenant_id"], "--retract", report_id],
                    live=True,
                )

    def cashflow_step(self) -> None:
        twin = "construction"
        tenant = self.tenants[twin]
        self.step(f"{tenant['company']}: cash-flow copilot (13-week forecast, alerts, analysis, financing)")
        owner = self.human(twin, "owner")
        agent = self.agent(owner, "copilot")
        _, forecast = agent.call("GET", "/cashflow/forecast")
        low = min(forecast["weeks"], key=lambda w: w["closing_minor"])
        self.say(
            f"buffer USD {forecast['buffer_minor'] / 100:,.2f}; lowest projected balance "
            f"USD {low['closing_minor'] / 100:,.2f} in the week of {low['week_start']}"
        )
        _, alerts = agent.call("GET", "/alerts")
        for alert in alerts or []:
            self.say(f"alert {alert['type']} {alert['severity']} week {alert['week_start']}")
        _, dpo = agent.call("GET", "/analysis/DPO")
        self.say(
            f"DPO over the last 90 days: {dpo['value']} days, from {len(dpo['source_rows'])} source rows"
        )
        shortfall = [w for w in forecast["weeks"] if w["closing_minor"] < 0]
        if shortfall:
            need = forecast["buffer_minor"] - low["closing_minor"]
            _, match = agent.call(
                "POST",
                "/financing/match",
                {"shortfall_minor": need, "currency": "USD", "needed_by": shortfall[0]["week_start"]},
            )
            best = (match or {}).get("options", [{}])[0]
            self.say(
                f"financing to restore the buffer (USD {need / 100:,.2f}): "
                f"best eligible {best.get('product_id')} "
                f"total cost USD {best.get('total_cost_minor', 0) / 100:,.2f} (synthetic catalog)"
            )


def run_demo(runner: Runner, credentials: Path, options: argparse.Namespace) -> int:
    if not credentials.exists():
        print("Seed the stack first: make seed")
        return 2
    demo = Demo(runner, credentials, options.core)
    return demo.queue_hold(options.twin) if options.command == "hold" else demo.run()
