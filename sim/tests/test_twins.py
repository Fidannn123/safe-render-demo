"""M5 sim twins: determinism, schema, money as int minor units, IBAN mod-97, oracle sanity."""

from __future__ import annotations

import email
import hashlib
import json
import re
from email import policy as email_policy
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from safepay_sim.twins import iban as ib
from safepay_sim.twins.__main__ import main
from safepay_sim.twins.analysis import discount_apr
from safepay_sim.twins.generate import build, dumps
from safepay_sim.twins.pdf import PdfLine, render_pdf
from safepay_sim.twins.policy import load_policy, robust_z
from safepay_sim.twins.textutil import (
    damerau_levenshtein,
    decimal_str,
    money,
    names_lookalike,
    normalise_ref,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "safepay_sim" / "twins" / "labels.schema.json"
ATTACK_CLASSES = {
    "HIDDEN_INSTRUCTION",
    "BANK_DETAIL_SWAP",
    "LOOKALIKE_VENDOR_OR_ACCOUNT",
    "DUPLICATE_OR_NEAR_DUPLICATE",
    "SPLIT_PAYMENTS",
    "REPORTED_PAYEE",
    "STOLEN_TOKEN_DIRECT_API",
    "SELF_APPROVAL",
    "CONCURRENT_DOUBLE_SUBMIT",
    "FALSE_REPORT_POISONING",
    "OPRF_ENUMERATION",
    "CASH_BUFFER_BREACH",
}


@cache
def bundle(seed: int) -> dict[str, bytes]:
    return build(seed)


@cache
def labels(seed: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(bundle(seed)["labels.json"])
    return data


def tree_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + hashlib.sha256(files[path]).digest())
    return h.hexdigest()


def disk_tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def walk(obj: Any, key: str = "") -> list[tuple[str, Any]]:
    out = [(key, obj)]
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += walk(v, k)
    elif isinstance(obj, list):
        for v in obj:
            out += walk(v, key)
    return out


def json_files(seed: int) -> dict[str, Any]:
    return {p: json.loads(b) for p, b in bundle(seed).items() if p.endswith(".json")}


# ------------------------------------------------------------------------------- determinism
def test_same_seed_is_byte_identical_in_memory() -> None:
    assert tree_hash(build(42)) == tree_hash(bundle(42))


def test_cli_same_seed_byte_identical_on_disk(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["--seed", "42", "--out", str(a)]) == 0
    assert main(["--seed", "42", "--out", str(b)]) == 0
    files_a, files_b = disk_tree(a), disk_tree(b)
    assert tree_hash(files_a) == tree_hash(files_b) == tree_hash(bundle(42))


def test_cli_refuses_non_empty_out(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("x")
    with pytest.raises(SystemExit):
        main(["--seed", "1", "--out", str(tmp_path)])


def test_different_seeds_differ() -> None:
    a, b = bundle(42), bundle(43)
    assert tree_hash(a) != tree_hash(b)
    assert a["labels.json"] != b["labels.json"]
    assert a["twins/retail/ledger.json"] != b["twins/retail/ledger.json"]


def test_manifest_hashes_every_file() -> None:
    files = bundle(42)
    manifest = json.loads(files["manifest.json"])
    assert manifest["synthetic"] is True
    listed = manifest["files"]
    assert set(listed) == set(files) - {"manifest.json"}
    for path, digest in listed.items():
        assert hashlib.sha256(files[path]).hexdigest() == digest


def test_json_is_canonical_sorted_lf_and_float_free() -> None:
    for path, data in bundle(42).items():
        if path.endswith((".json", ".eml", ".txt")):
            assert b"\r" not in data, path
        if path.endswith(".json"):
            assert dumps(json.loads(data)) == data, path
            assert not any(isinstance(v, float) for _, v in walk(json.loads(data))), path


def test_dumps_rejects_floats() -> None:
    with pytest.raises(TypeError):
        dumps({"a": [1, {"b": 0.5}]})


# ------------------------------------------------------------------------------- schema
def test_labels_validate_against_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for seed in (42, 43):
        errors = sorted(validator.iter_errors(labels(seed)), key=lambda e: list(e.path))
        assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])


def test_schema_rejects_float_amount_and_bad_label() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    bad = json.loads(bundle(42)["labels.json"])
    bad["twins"][0]["forecast"]["opening_minor"] = 1.5
    bad["scenarios"][0]["label"] = "harmless"
    assert len(list(validator.iter_errors(bad))) >= 2


# ------------------------------------------------------------------------------- money and IBANs
def test_all_minor_amounts_are_ints() -> None:
    seen = 0
    for path, doc in json_files(42).items():
        for key, value in walk(doc):
            if key.endswith("_minor") and not isinstance(value, list | dict):
                assert isinstance(value, int), (path, key, value)
                assert not isinstance(value, bool), (path, key, value)
                seen += 1
            if key == "line_items_minor" and isinstance(value, list):
                assert all(isinstance(v, int) for v in value)
    assert seen > 1000


def test_rates_are_decimal_strings() -> None:
    for twin in labels(42)["twins"]:
        assert re.fullmatch(r"\d+\.\d{2}", twin["analysis"]["dpo_days"])
        assert re.fullmatch(r"\d+\.\d{2}", twin["analysis"]["dso_days"])
        for item in twin["schedule"]["items"]:
            assert item["apr"] is None or re.fullmatch(r"\d+\.\d{6}", item["apr"])


def test_every_iban_passes_mod97() -> None:
    ibans: set[str] = set()
    for _, doc in json_files(42).items():
        for key, value in walk(doc):
            if key in ("iban", "attacker_iban", "creditor_iban") and value is not None:
                ibans.add(value)
    pattern = re.compile(rb"IBAN ([A-Z]{2}[0-9]{2}(?: [A-Z0-9]{1,4})+)")
    for path, data in bundle(42).items():
        if path.endswith((".eml", ".pdf")):
            for m in pattern.finditer(data):
                ibans.add(m.group(1).decode().replace(" ", ""))
        if path.endswith(".eml"):
            for m in re.finditer(rb"IBAN ([A-Z]{2}[0-9]{2}[A-Z0-9]{11,30})", data):
                ibans.add(m.group(1).decode())
    assert len(ibans) > 80
    for value in ibans:
        assert ib.is_valid(value), value
        assert re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", value)


def test_iban_known_vectors_and_tamper() -> None:
    assert ib.is_valid("GB82WEST12345698765432")
    assert ib.is_valid("DE89 3704 0044 0532 0130 00")
    assert not ib.is_valid("GB82WEST12345698765433")
    assert ib.make_iban("GB", "WEST12345698765432") == "GB82WEST12345698765432"
    assert ib.grouped("DE89370400440532013000") == "DE89 3704 0044 0532 0130 00"


def test_benign_registry_accounts_have_unique_prefix_and_suffix() -> None:
    for twin in (json.loads(bundle(42)[f"twins/{k}/twin.json"]) for k in ("retail", "pharmacy", "logistics")):
        accounts = [v["registry_account"]["iban"][4:] for v in twin["vendors"]]
        lookalikes = [v for v in twin["vendors"] if v["sim_role"] == "lookalike_account"]
        if not lookalikes:
            assert len({a[:4] for a in accounts}) == len(accounts)
            assert len({a[-4:] for a in accounts}) == len(accounts)


# ------------------------------------------------------------------------------- scenario targets
def test_scenario_mix_meets_targets() -> None:
    lab = labels(42)
    by_label: dict[str, int] = lab["counts"]["by_label"]
    assert 55 <= by_label["benign"] <= 70
    assert 35 <= by_label["attack"] <= 45
    assert 8 <= by_label["ambiguous"] <= 12
    classes = {s["scenario_class"] for s in lab["scenarios"] if s["label"] == "attack"}
    assert classes == ATTACK_CLASSES
    assert len({s["scenario_id"] for s in lab["scenarios"]}) == len(lab["scenarios"])
    sectors = {t["twin_key"] for t in lab["twins"]}
    assert sectors == {
        "retail",
        "construction",
        "restaurant",
        "logistics",
        "pharmacy",
        "agriculture",
        "it_services",
        "textiles",
    }


def test_ambiguous_must_hold_and_benign_pay_invoices_allow() -> None:
    for s in labels(42)["scenarios"]:
        if s["label"] == "ambiguous":
            assert s["expected"]["B1"]["verdict"] == s["expected"]["B2"]["verdict"] == "HOLD"
        if s["label"] == "benign" and s["task"] == "PAY_INVOICE":
            assert s["expected"]["B1"]["verdict"] == "ALLOW"
            assert s["expected"]["B1"]["reason_codes"] == []
            truth = s["truth"]
            assert truth["attacker_iban"] is None
            assert not truth["has_hidden_instruction"]


def test_attacks_never_allowed_to_attacker_accounts() -> None:
    for s in labels(42)["scenarios"]:
        if s["label"] != "attack" or s["invoice"] is None:
            continue
        for cfg in ("B1", "B2"):
            exp = s["expected"][cfg]
            assert exp["verdict"] != "ALLOW" or not s["truth"]["payee_attacker_controlled"], s["scenario_id"]
            assert set(exp["required_reason_codes"]) <= set(exp["reason_codes"])
        assert s["expected"]["B0"]["outcome"] == "PAID"


def test_network_raises_reported_payee_to_hold_in_b2() -> None:
    lab = labels(42)
    rep = [s for s in lab["scenarios"] if s["kind"] == "reported_2plus"]
    assert rep
    assert all(s["expected"]["B1"]["verdict"] == "GUARDED" for s in rep)
    assert all(s["expected"]["B2"]["verdict"] == "HOLD" for s in rep)
    reported = {r["iban"] for r in lab["network_reports"]}
    for s in rep:
        assert s["truth"]["bank_details_on_invoice"]["iban"] in reported


def test_invoice_files_contain_truth_fields() -> None:
    files = bundle(42)
    for s in labels(42)["scenarios"]:
        if s["invoice"] is None:
            continue
        data = files[s["invoice"]["file"]]
        assert hashlib.sha256(data).hexdigest() == s["invoice"]["sha256"]
        truth = s["truth"]
        text = data.decode("utf-8") if s["invoice"]["source"] == "email" else data.decode("latin-1")
        assert truth["reference"] in text
        assert money(truth["amount_minor"]) in text
        assert truth["due_date"] in text
        assert "SYNTHETIC" in text
        assert sum(truth["line_items_minor"]) == truth["amount_minor"]


def test_email_invoices_parse() -> None:
    for path, data in bundle(42).items():
        if path.endswith(".eml"):
            msg = email.message_from_bytes(data, policy=email_policy.default)
            assert msg["X-SafePay-Synthetic"] == "true"
            body = msg.get_body(preferencelist=("plain",))
            assert body is not None
            assert "Invoice number:" in body.get_content()


def test_pdf_structure_and_hidden_text() -> None:
    files = bundle(42)
    pdfs = {p: b for p, b in files.items() if p.endswith(".pdf")}
    assert pdfs
    for path, data in pdfs.items():
        assert data.startswith(b"%PDF-1.4\n"), path
        assert data.endswith(b"%%EOF\n"), path
        xref_at = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
        assert data[xref_at:].startswith(b"xref\n")
        offsets = re.findall(rb"(\d{10}) 00000 n ", data[xref_at:])
        for n, off in enumerate(offsets, start=1):
            assert data[int(off) :].startswith(f"{n} 0 obj".encode()), (path, n)
    hidden = [s for s in labels(42)["scenarios"] if s["kind"] == "hidden" and s["invoice"]["source"] == "pdf"]
    for s in hidden:
        assert b"1 1 1 rg" in files[s["invoice"]["file"]]


def test_pdf_writer_escapes_and_paginates() -> None:
    lines = [PdfLine(f"line (x) {i} \\ end") for i in range(120)]
    pdf = render_pdf(lines, "t")
    assert b"/Count 3" in pdf
    assert b"(line \\(x\\) 0 \\\\ end) Tj" in pdf


# ------------------------------------------------------------------------------- forecast and oracles
def test_forecast_alert_ground_truth() -> None:
    for twin in labels(42)["twins"]:
        fc = twin["forecast"]
        weeks = fc["weeks"]
        assert [w["week"] for w in weeks] == list(range(13))
        opening = fc["opening_minor"]
        for w in weeks:
            opening += w["inflow_minor"] - w["outflow_minor"]
            assert w["closing_minor"] == opening
            expected = "SHORTFALL" if opening < 0 else "LOW_BUFFER" if opening < fc["buffer_minor"] else "OK"
            assert w["status"] == expected
        if twin["stress"] == "NONE":
            assert fc["low_buffer_weeks"] == fc["shortfall_weeks"] == []
            assert twin["financing_need"] is None
        elif twin["stress"] == "LOW_BUFFER":
            assert fc["low_buffer_weeks"]
            assert not fc["shortfall_weeks"]
        else:
            assert fc["shortfall_weeks"]
            assert twin["financing_need"]["max_deficit_minor"] == -fc["min_closing_minor"]
        assert fc["buffer_minor"] >= load_policy().buffer_floor


def test_schedule_discount_decisions() -> None:
    hurdle = load_policy().hurdle_apr
    early = skipped = 0
    for twin in labels(42)["twins"]:
        sched = twin["schedule"]
        assert sched["discounts_captured_minor"] == sum(i["discount_captured_minor"] for i in sched["items"])
        for item in sched["items"]:
            if item["early_payment_discount"] is None:
                assert item["action"] == "PAY_ON_DUE"
                continue
            apr = Fraction(item["apr"])
            if item["action"] == "PAY_EARLY":
                early += 1
                assert apr > hurdle
                assert item["pay_amount_minor"] + item["discount_captured_minor"] == item["amount_minor"]
                assert item["pay_date"] >= labels(42)["as_of"]
            elif twin["stress"] == "NONE":
                skipped += 1
                assert apr <= hurdle
    assert early >= 5
    assert skipped >= 1


def test_analysis_oracle_recomputed_from_ledger() -> None:
    for twin in labels(42)["twins"]:
        ledger = json.loads(bundle(42)[f"twins/{twin['twin_key']}/ledger.json"])
        ap_open = sum(i["amount_minor"] for i in ledger["ap_invoices"] if i["status"] == "OPEN")
        ap_all = sum(i["amount_minor"] for i in ledger["ap_invoices"])
        a = twin["analysis"]
        assert a["dpo_inputs"] == {"ap_open_minor": ap_open, "ap_invoiced_minor": ap_all}
        assert a["dpo_days"] == decimal_str(Fraction(ap_open * 90, ap_all), 2)
        ar_open = sum(i["amount_minor"] for i in ledger["ar_invoices"] if i["status"] == "OPEN")
        assert a["dso_inputs"]["ar_open_minor"] == ar_open
        paid: dict[str, int] = {}
        for ev in ledger["events"]:
            if ev["templates"] == ["reserve", "submit", "settle"]:
                paid[ev["vendor_id"]] = paid.get(ev["vendor_id"], 0) + ev["amount_minor"]
        top = sorted(paid.values(), reverse=True)[:3]
        assert [v["paid_minor"] for v in a["top_vendors"]] == top


def test_ledger_balance_never_negative_and_matches_forecast_opening() -> None:
    for twin in labels(42)["twins"]:
        ledger = json.loads(bundle(42)[f"twins/{twin['twin_key']}/ledger.json"])
        balance = 0
        for ev in ledger["events"]:
            t = ev["templates"]
            if t in (["opening_balance"], ["customer_receipt"]):
                balance += ev["amount_minor"]
            elif t in (["direct_debit_opex"], ["reserve", "submit", "settle"]):
                balance -= ev["amount_minor"]
            assert balance >= 0
        assert balance == ledger["closing_bank_balance_minor"] == twin["forecast"]["opening_minor"]


# ------------------------------------------------------------------------------- helpers
def test_text_helpers() -> None:
    assert money(123456) == "USD 1,234.56"
    assert money(5) == "USD 0.05"
    assert normalise_ref("inv-1042/a") == "INV1042A"
    assert damerau_levenshtein("ab", "ba") == 1
    assert damerau_levenshtein("kitten", "sitting") == 3
    assert names_lookalike("Harbour Wholesale", "Harbour Who1esale", 2)
    assert names_lookalike("Medway", "M\u0435dway", 0)
    assert not names_lookalike("Harbour Wholesale", "Crestline Beverages", 2)
    assert decimal_str(Fraction(1, 8), 2) == "0.12"
    assert decimal_str(Fraction(3, 8), 2) == "0.38"


def test_discount_apr_formula() -> None:
    # 2/10 net 30: (0.02 / 0.98) * (365 / 20) = 0.372448...
    assert decimal_str(discount_apr(200, 30, 10, 365), 6) == "0.372449"
    assert discount_apr(50, 30, 10, 365) < Fraction(12, 100)


def test_robust_z() -> None:
    assert robust_z([10, 10, 10, 10, 10], 11) is None
    z = robust_z([100, 102, 98, 101, 99], 130)
    assert z is not None
    assert z > Fraction(35, 10)


@pytest.mark.parametrize("seed", [0, 1, 7, 2026])
def test_other_seeds_build_and_validate(seed: int) -> None:
    """The generator re-derives every designed verdict with the policy oracle and raises on mismatch."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    lab = json.loads(build(seed)["labels.json"])
    assert not list(Draft202012Validator(schema).iter_errors(lab))
    assert lab["seed"] == seed
    assert {s["scenario_class"] for s in lab["scenarios"] if s["label"] == "attack"} == ATTACK_CLASSES
