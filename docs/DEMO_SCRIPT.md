# Demo video script (deliverable 03, 5 minutes or less)

Follows `docs/COMPETITION.md` section 7. Record the screen at 1080p with narration. Every number shown
comes from the live run or from `evidence/run-42/`; read it off the screen, do not say numbers from
memory. Keep a "SYNTHETIC data, SIMULATED bank" caption on screen the whole time.

## Before recording

```bash
make down-volumes && make up && make seed      # fresh stack, 8 synthetic businesses
make evidence SEED=42 && make tech-doc          # bundle and PDF for beats 7 to 9 (bank and intel must be up)
make hold TWIN=logistics                        # one held payment in the console queue, beat 6
cd sim && uv run python -m safepay_sim.harness otp logistics.approver --password   # console login, beat 6
```

Open three windows: a terminal (large font), the console at http://127.0.0.1:8080, and
`evidence/run-42/EVIDENCE.md` in a Markdown viewer. To rehearse, run `make demo` and approve the held
payment once, then repeat the block above so the recorded run starts clean (`make down-volumes` also
removes the old demo users).

## Beats

| # | Time | Screen | Say (plain, short) |
|---|---|---|---|
| 1 | 0:00-0:15 | Title slide | "Small businesses want an AI agent to pay their suppliers. If the agent can pay, it can be tricked into paying a criminal. SafePay stops that." |
| 2 | 0:15-0:50 | Terminal: `uv run python ops/scripts/show_scenarios.py retail-swap-01 retail-hidden-01`, point at the B0 rows | "First, an agent with no protection. One invoice changes the supplier's bank account, another hides instructions to the AI. B0, the undefended agent, pays both. Here is the money that left." |
| 3 | 0:50-1:30 | Terminal: `make demo`, pause on steps 3 and 4 | "Same invoices under SafePay. The planner takes the payee from the vendor registry, never from the invoice. The Guardian holds both, with typed reasons: bank detail mismatch, injection markers. Each decision is signed." |
| 4 | 1:30-2:20 | Terminal: `make demo` steps 2 and 10 | "The agent still does the job. It pays a routine invoice end to end through the simulated bank with a signed receipt. For cash flow it builds a 13-week forecast, raises shortfall alerts, answers an analysis question with source rows, and picks the cheapest eligible financing option." |
| 5 | 2:20-3:05 | Terminal: `make demo` steps 8 and 9, then `show_scenarios.py pharmacy-reported-2plus-01` | "Two businesses report an account they lost money to, with bank-signed proof. A third business is about to pay it. It asks the network blindly. The network never sees which account was checked, but the payment is held. Without the network, B1 pays it. With it, B2 holds it." |
| 6 | 3:05-3:45 | Terminal: `make demo` steps 5, 6 and 11. Console: sign in as `logistics.approver`, open a held payment, explain decision | "Now a hijacked agent. Its token cannot approve its own payment. The owner who gave the agent its token cannot approve either: four eyes. A real approver signs in with password and one-time code and decides. The freeze button stops all outflow and revokes every agent token." |
| 7 | 3:45-4:20 | `EVIDENCE.md` Results table, or the console Metrics page | "The full run: every scenario, three configurations, same seeds. Attacker loss for B0, B1 and B2. Benign task success. False holds. Read across the row." |
| 8 | 4:20-4:40 | `EVIDENCE.md` "Fraud network" lines | "Privacy check: we captured every blind query the network received and searched them for any account number. None found." |
| 9 | 4:40-5:00 | Terminal: `make verify-evidence SEED=42` | "You do not have to trust us. One command re-checks every signature, the audit chains and every number. All data here is synthetic and the bank is simulated." |

## Notes

- If a beat runs long, cut beat 8 into beat 7 (same file).
- Say "simulated bank" and "synthetic businesses" at least once on camera (beats 4 and 9 do).
- Do not claim the privacy test proves more than it does: it checks captured queries for account numbers.
- Export MP4 (H.264). Put the file in the submission package and mirror it on Bilibili, not only YouTube.
