# Deck outline (deliverable 02, 10 minutes or less)

Ten slides, about one minute each. Every number on a slide comes from the generated evidence:
`README.md` "Results", `evidence/run-42/EVIDENCE.md` or `evidence/run-42/metrics.json` (written by
`make evidence SEED=42`, re-checked by `make verify-evidence SEED=42`). Placeholders below look like
`{EVIDENCE: Results / Attacker loss / B0}`: copy the value from that cell when building the slides, never
from memory. Put "SYNTHETIC data, SIMULATED bank" in the footer of every slide.

## 1. The problem

- Small businesses want an AI agent to handle supplier payments, the outflow side of cash flow.
- Three attacks that make that agent pay a criminal: an invoice with hidden instructions, an email that
  "updates" the supplier's bank details, a look-alike supplier.
- One line: "An agent that can pay can be talked into paying the wrong person."

Notes: Topic E framing. Payables sit next to cash-flow alerts, analysis and financing, all in the product.

## 2. What SafePay does

- The agent pays invoices only after three checks agree: deterministic rules (Guardian), a 13-week
  cash-buffer forecast, and a private fraud network shared across businesses.
- It also raises cash-flow alerts, answers analysis questions with cited numbers, and matches financing.
- The LLM only reads invoices and phrases answers. It never decides and never picks the payee.

## 3. Layers of defence

- Quarantined extractor (no tools) to typed fields to planner with one tool, `propose_payment`.
- Payee always from the vendor registry, never from the invoice.
- Guardian signs an Ed25519 Decision: ALLOW (T1), GUARDED (T2), HOLD (T3), DENY.
- PostgreSQL enforces the money rules: balanced entries, no overdraft, append-only ledger and audit, no
  double payment.

Visual: `docs/assets/architecture.svg` (the data flow with the tech stack).

## 4. Permission tiers

Table T0 to T4 from `docs/SECURITY.md` section 2: what the agent may do, who enforces it, the test that
proves it, and the maximum loss if the agent is fully hijacked.

Notes: "The agent cannot change its own tier. The database refuses even if the code is wrong."

## 5. The fraud network (the new part)

- Business A loses money to an account and reports it, with a bank-signed receipt as proof.
- Business B's agent is about to pay that account. SafePay asks the network blindly (VOPRF, RFC 9497)
  and checks the answer locally, so the network never learns whom B is paying.
- Network signals can force a HOLD, never a DENY, so false reports cannot block an honest supplier.

Visual: the sequence diagram from `docs/architecture.md` section 2.7.

## 6. Results: attacker loss by configuration

- Bar chart: attacker loss for B0 `{EVIDENCE: Results / Attacker loss / B0}`, B1 `{... / B1}`,
  B2 `{... / B2}`.
- Second row: attack success rate for B0, B1, B2 from the same table.
- Caption: "B0 undefended agent. B1 Guardian and tiers. B2 adds the network. Same seeds, same invoices."

## 7. The agent does real work

- Table "Agent tasks" from `EVIDENCE.md`: pay invoice, schedule payables, cash-flow alert, financial
  analysis, financing match, passed / executed.
- Benign task success and false holds on benign payments from "Results".
- Alert precision and recall and analysis accuracy from the lines under "Agent tasks".

## 8. Network lift and privacy

- Lift: `{EVIDENCE: Fraud network / detection lift}` (reported-payee scenarios stopped before payment, B2
  vs B1).
- Privacy attack: an operator with every request, every epoch key and
  `{EVIDENCE: Fraud network / Operator privacy attack / candidate IBANs}` candidates recovers
  `{... / identifiers recovered}` payees; the same attack on a plain SHA-256 lookup recovers
  `{... / Control}`. That control is the point: the attack is strong enough to catch a leak.
- Example: `pharmacy-reported-2plus-01` is paid in B1 and held in B2
  (`uv run python ops/scripts/show_scenarios.py pharmacy-reported-2plus-01`).

## 9. Honest limits

From `docs/SECURITY.md` section 7: the one attack that still moves money (first payment of a new-payee
split, from `EVIDENCE.md` "Attacks that still moved money"), the `voprf` binding (self-tested at every
start-up, still unaudited), keys on the same host, mock LLM in the published bundle (`make evidence-llm`
measures a real one), scripted attackers, synthetic data and simulated bank.

## 10. Verify it yourself, and what is next

- `make up && make seed && make demo`, then `make evidence SEED=42 && make verify-evidence SEED=42`.
- Next: threshold OPRF (no single key holder), KMS or HSM keys, a pilot with a real bank sandbox,
  measured runs with a real LLM.
