# COMPETITION.md: what the competition requires from this build

2026 Shenzhen International FinTech Competition (FinTechathon), International Track. SafePay enters **Topic E: SME Finance Copilot**.
Facts verified on 19 Oct 2026 unless marked **[unverified]**. The official site (fintechathon.g-ican.com) is JavaScript-only and could not be read directly; the main source is the organizer's rules PDF mirrored by ITS (see Sources).

## 1. Deadlines (all one deadline)

| Event | Beijing time (UTC+8) | Baku time (UTC+4) |
|---|---|---|
| Registration AND submission deadline | **20 Oct 2026, 23:59** | **20 Oct 2026, 19:59** |
| Our internal v1 submission | 20 Oct, 11:59 | **20 Oct, 07:59** (D-12 h) |
| Our internal final submission | 20 Oct, 21:59 | **20 Oct, 17:59** (D-2 h) |
| Compliance screening | 21-22 Oct | |
| Online review (reviewers read and run the package; no live pitch) | 23-28 Oct | |
| Finalists announced | 31 Oct | |
| Check-in + WeBank HQ visit | 20 Nov | |
| Grand final, Shenzhen (at least one member in person; travel and hotel covered) | 21-23 Nov | |

Submissions can be updated any time before the deadline; **the latest version received wins**. Nothing is accepted after the deadline. A bilingual reminder email arrives 24 h before.

## 2. Theme and topic

- Theme: **"AI as a Financial Participant"**. Each team builds an AI agent that completes real tasks in a financial scenario. All six topics share the same core capability requirements and scoring.
- **Topic E, official wording:** "A finance copilot for small-business owners, offering financing-option matching, cash-flow management with alerts, and business financial analysis."
- The rules never mention invoices, suppliers, payables or fraud. SafePay therefore frames supplier payments as the **outflow side of cash-flow management** and implements all three named capabilities (cash-flow alerts, financial analysis, financing matching). This framing must be visible in the README, deck and demo.
- Why not Topic F (Autonomous On-chain Wallet): its evidence must be "real on-chain transaction hashes" from a wallet agent that moves assets. Our mandate forbids money movement in smart contracts, so Topic F is out.

## 3. Judging criteria and weights

| Criterion | Weight | What earns points in SafePay |
|---|---|---|
| Task completion | **40%** | The 5 agent tasks (section 4) completed end to end, with success rates produced by the harness. |
| Security & compliance | **30%** | Permission tiers T0-T4 enforced in Guardian + DB; ~40 attacks with attacker loss per configuration; threat model; known-risk list; OWASP LLM Top 10 (2025) mapping. |
| Innovation & interaction | **30%** | Privacy-preserving cross-business fraud network (VOPRF blind lookup), measured detection lift B2 vs B1, privacy test; clear decision explanations in the console. |
| Challenge bonus (on-chain exploration) | 0-5 pts | Not pursued. Optional off-by-default audit-hash anchoring only. |

Weights come from the rules PDF and match what competing teams quote. No sub-rubric is published. Reviewers are WeBank experts and university scholars; the advisory board includes Qiang Yang (privacy-preserving collaborative learning), which suits the "share intelligence, not data" pitch.

## 4. The 5 measured agent tasks and how each is scored

| # | Task | Input | Deterministic core | LLM role | Metric (computed by `sim/harness`, never typed) | Target |
|---|---|---|---|---|---|---|
| 1 | Pay invoice | Invoice email/PDF | Vendor-registry binding, Guardian, ledger | Extract fields; plan | Benign success = correct payee, amount, reference, due date, and paid (or correctly HOLD for ambiguous); attacker loss = 0 in B2 | ≥95% benign success; 0 loss |
| 2 | Schedule payables | Open AP + forecast | Pay on due date; early only if discount APR > hurdle | Explain choice | Discounts captured vs oracle; buffer breaches | All profitable discounts taken; 0 breaches |
| 3 | Cash-flow alert | Ledger, AR, AP, recurring opex | 13-week forecast, buffer test | Phrase alert | Precision and recall of shortfall/low-buffer weeks vs ground truth from twins | ≥0.9 / ≥0.9 |
| 4 | Financial analysis | Ledger | SQL aggregates (DPO, DSO, burn, top vendors) | Answer in plain English, numbers cited | Exact numeric match vs oracle | 100% |
| 5 | Financing match | Projected shortfall | Rule match over synthetic catalog (amount, term, eligibility) | Explain trade-offs | Eligible lowest-cost option ranked first | 100% |

Security metrics reported alongside: attack success rate and attacker loss for B0 (undefended), B1 (Guardian + tiers), B2 (B1 + network); false-hold rate on benign payments; network detection lift; decision latency p50/p95; privacy test (0 recovered identifiers). Targets above are internal goals, not organizer thresholds; report only measured values.

## 5. Submission checklist (official, six items)

| # | Item | Official requirement | Our artifact |
|---|---|---|---|
| 01 | Technical documentation | System architecture, core algorithm description, security design | `docs/architecture.md` exported to PDF (+ generated numbers) |
| 02 | Presentation deck | Final presentation ≤10 minutes | ~10 slides: problem + 3 attacks; layers of defence; architecture; tiers; privacy network + honest threat model; loss-by-configuration chart; task metrics; known risks; roadmap |
| 03 | Demo video | "Optional", ≤5 minutes | **Mandatory for us** (item 06 requires it for non-on-chain topics). MP4 inside the package; mirror on Bilibili, not only YouTube |
| 04 | Source code | README, GitHub repository link, deployment instructions, test cases | Public GitHub repo; README quickstart; `docker compose up`; `make verify` |
| 05 | Security self-assessment report | Permission-tier implementation and known-risk list | Tier table + links to the tests proving each row; threat model; known risks; OWASP mapping |
| 06 | Execution evidence | Non-on-chain topics: simulated-account/sandbox operation logs **and demo video** | `evidence/run-<seed>/` bundle + `make verify-evidence` + generated EVIDENCE.md/PDF |

Also: everything in **English**. Open-source components must be properly licensed (keep a licence list / SBOM). Teams keep their IP. No rule restricts AI coding tools.

## 6. Disqualification and failure risks

| Risk | Consequence | Action |
|---|---|---|
| Any member not a full-time student at a university outside Mainland China, Hong Kong, Macao, Chinese Taipei | Team invalid | Confirm each member's student status. Non-students (mentors, staff) cannot be members. |
| Team size outside 2-5, or <60% non-Chinese nationals | Team invalid | Check passports before registering. |
| A person on two teams (either track) | **All** teams involved voided | Confirm nobody registered elsewhere. |
| Eligibility by country **[unverified]**: a June IEEE post limited entry to the 21 APEC economies; the October rules say only "overseas" institutions. Azerbaijan is not in APEC | Possible rejection | Email the organizer today and keep the reply. |
| Registration incomplete (form + every member's passport bio page) | No entry | Send before the deadline; confirmation comes within 48 h; organizers may close early. |
| Wrong channel **[unverified]**: a Chiang Mai University notice says registration and deliverables go to WANG Yiran, wangyr@pbcsf.tsinghua.edu.cn; the PDF lists both email and website | Lost submission | Confirm channel and exact subject format from the official site. |
| Missing item, non-English content, wrong evidence type, repo that does not open | Fails compliance screening (21-22 Oct) | Run the pre-submit checklist in BUILD_PLAN.md. |
| Reviewers in mainland China cannot reach links (measured by another team: vercel.app ~blocked, YouTube, Google Fonts, Telegram blocked; supabase.co, run.app unreliable) | Reviewers cannot verify | Ship MP4, PDFs and evidence inside the package; bundle fonts; no CDN; one-command local run. |
| Overclaiming (e.g. "audited crypto", typed metrics, invented hashes) | Credibility loss in review | All numbers from scripts; synthetic data and simulation labelled everywhere. |

## 7. What the demo (≤5 min) must prove

Each beat maps to a criterion; all numbers on screen come from the evidence bundle.

1. **Problem** (15 s): an SME delegates payables to an AI agent.
2. **Undefended agent loses money (B0)**: hidden-instruction invoice and bank-detail-swap invoice; money leaves in the sandbox. (Security)
3. **Same invoices under SafePay (B1)**: DENY/HOLD with typed reason codes and the signed Decision. (Security)
4. **Agent does real work**: pays benign invoices end to end, schedules payables, raises a cash-flow SHORTFALL alert, proposes deferrals and a financing option, answers an analysis question with cited numbers. (Task completion)
5. **Network catches what rules miss (B2)**: a payee reported by business A (with a bank-signed receipt) is escalated to HOLD at business B before payment; the network never saw business B's query. (Innovation)
6. **Hijacked agent is bounded**: stolen agent token calls the API directly; caps and tiers hold; T4 freeze. (Security)
7. **Results chart**: attacker loss B0 vs B1 vs B2; benign success; false-hold rate; detection lift. (All)
8. **Privacy test**: adversary with server transcript + key + 10^6 candidate IBANs recovers 0 identifiers. (Innovation / compliance)
9. **Verifiability**: `make verify-evidence` passing on the bundle. (Evidence)

## 8. Competitive landscape (public repos, 19 Oct 2026; teams' own claims)

- Top entries converged on "LLM proposes, deterministic engine decides": tiers, maker-checker, hash-chained audit, counted attack suites, evidence files. A Guardian alone is table stakes.
- **Fiona (Topic E)**: Guardian with 12 rules over AI-proposed supplier payments (bank-detail change, duplicates, 3-way match, cash buffer). Closest to SafePay's Check 1; runs locally only.
- **DuitDuit (Topic E)**: 57 eval tasks (30 adversarial), OWASP Agentic mapping, maker-checker, documented mainland-China reachability plan.
- **Agent Credit Bureau (Topic F)**: public, recomputable counterparty reputation; no privacy for reporters or queries.
- **No public entry** does privacy-preserving cross-business fraud sharing (searched for OPRF/VOPRF/PSI/consortium/shared fraud). Untested attack classes elsewhere: payee bank-account substitution, network poisoning, blind-lookup enumeration, proof the lookup server learns nothing. SafePay must test all four.

## Sources

- Rules PDF, "PBCSF Appendix 2 – Problem Description and Competition Rules for International Track" (ITS mirror): https://www.its.ac.id/international/wp-content/uploads/sites/66/2026/10/PBCSF-Appendix-2-Problem-Description-and-Competition-Rules-for-International-Track.pdf
- Official site (JS-only): https://fintechathon.g-ican.com/#/
- Submission checklist screenshot supplied by the team (six items, deadline, time-zone table).
- Chiang Mai University notice (registration steps, email): https://ird.oou.cmu.ac.th/2026-shenzhen-international-fintech-competition/
- ifeng Tech, 10 Sep 2026 (track structure): https://tech.ifeng.com/c/8wJG9eH2KFs
- Competitors: DuitDuit https://github.com/tanhongsheng050204-code/2026-SHENZHEN-FINTECHATHON-INTERNATIONAL-TRACK · Fiona https://github.com/Asharib19khan/fiona · Agent Credit Bureau https://github.com/taivnaaiwnl/shenzhen-comp · Leash https://github.com/detinatran/finbase · TreasuryPilot https://github.com/Nostidus4/TreasuryPilot · Agentic Wallet https://github.com/sucsage/agentic-wallet · FundBun https://github.com/odripads/fundbun
