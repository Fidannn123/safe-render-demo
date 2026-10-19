# KICKOFF_PROMPT.md

Copy everything inside the block below into Claude Code (VS Code), opened at the root of the empty repo that contains `CLAUDE.md` and `docs/`.

```
You are the lead engineer for SafePay, our FinTechathon 2026 entry (Topic E, SME Finance Copilot).
Hard deadline: 20 Oct 2026 19:59 Baku time. Internal v1 submission: 20 Oct 07:59 Baku.

1. Read, in full and in this order: CLAUDE.md, docs/architecture.md, docs/COMPETITION.md, docs/BUILD_PLAN.md.
   These are the complete source of truth. You have no other context. Do not redesign; the
   non-negotiable decisions in CLAUDE.md override anything you know or find online.

2. Before writing files, reply with:
   - a 10-line summary of what we are building and how it is scored,
   - any contradictions or gaps you find between the four documents (with your proposed resolution),
   - the exact list of files you will create in Phase 0.

3. Then execute Phase 0 of docs/BUILD_PLAN.md ("Spec freeze and skeleton") completely:
   - repo skeleton, uv + pnpm workspaces,
   - all spec/ files (three OpenAPI specs, JSON Schemas, state_machine.yaml, ledger_accounts.yaml,
     policy.yaml with the default values and the policy resolution, errors.yaml, vectors/),
   - three bootable Django 5.2 projects (core, intel, bank_sandbox) with Argon2 configured and /healthz,
   - ops/docker-compose.yml with PostgreSQL 18 (three databases, three roles), .env.example,
     dev-key generator (gitignored output),
   - Makefile targets from CLAUDE.md (stubs allowed), CI workflow running make verify + gitleaks +
     pip-audit + pnpm audit,
   - docs/DECISIONS.md.
   Write no business logic in Phase 0. No Solidity, no Celery/Redis, no npm.

4. Verify Gate G0 yourself: docker compose up healthy, make verify green, schemas and OpenAPI valid.
   For the RFC 9497 vectors: download them only from the official CFRG/IETF sources named in
   BUILD_PLAN.md; if you cannot reach the network, create spec/vectors/README.md saying exactly which
   file is needed and from where, and tell me.

5. Stop at Gate G0. Report: what passes, what fails, every decision you made (also in DECISIONS.md),
   anything I must provide (keys, accounts), and the spec files I should review before we freeze them.
   Do not start Phase 1 until I reply "spec frozen".

Commit in small steps with conventional commit messages.
```

## After G0

When the human replies "spec frozen", start Phase 1 using the per-session prompt template at the end of `docs/BUILD_PLAN.md`, one session per module (M1-M5), or sequentially in one session in the listed order.
