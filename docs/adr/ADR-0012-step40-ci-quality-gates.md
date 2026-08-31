# ADR-0012: Step 40 uses four isolated GitHub Actions quality gates

- Status: Accepted
- Date: 2026-08-27
- Scope: Local/CI synthetic delivery baseline

## Context

The repository already had repeatable local commands, but a developer could
merge code without an independent runner proving the same result. Step 40 must
separate fast diagnostics from browser and evaluation work, must not depend on
a developer database, and must never load a personal model or external-system
credential.

The repository is private and the current GitHub plan returns HTTP 403 for the
main branch protection API. Pull-request checks can therefore be executed and
reviewed, but GitHub cannot yet enforce required checks for this private
repository. This platform limitation is not evidence that protection exists.

## Decision

Every pull request and push to main runs four short workflows:

| Workflow | Required check | Responsibility |
|---|---|---|
| backend.yml | Backend / backend | Ruff, format, mypy, Alembic and pytest |
| frontend.yml | Frontend / frontend | frozen pnpm install, lint, type check, Vitest and production build |
| e2e.yml | E2E / e2e | production Web build, isolated API and PostgreSQL, Chromium Playwright |
| eval-security.yml | Eval and Security / eval-security | Fake triage Eval, RAG ACL/withdrawal Eval and security regression |

The database jobs use an ephemeral pgvector PostgreSQL service with synthetic
credentials and a database name ending in _test. They set LLM_PROVIDER=fake and
provide no usable model key. E2E starts only the dependencies currently used by
the application. Redis and MinIO are intentionally omitted because no tested
path consumes them; the full Compose topology belongs to Step 41.

All reusable Actions are pinned to immutable commit SHAs, the pgvector image is
pinned to a manifest digest, Python/Node/pnpm/uv versions are explicit, and
dependency installation is frozen against uv.lock and pnpm-lock.yaml. Failed
browser traces/screenshots and versioned synthetic Eval reports are uploaded
with bounded retention.

## Consequences

- A PR exposes independent backend, frontend, browser, and AI/security results
  instead of one opaque script.
- CI cannot silently use a developer .env file because ignored files are absent
  from checkout and the workflows define Fake/test settings explicitly.
- The workflows duplicate some installation and test time. This is deliberate
  isolation: one failure does not hide the status of unrelated gates.
- Until the GitHub plan supports private-repository protection, waiting for all
  four checks is a documented human merge rule, not a technically enforced
  control. Production or multi-person collaboration remains NO-GO on this
  point.

## Rejected alternatives

- One giant workflow: slower feedback and ambiguous failure ownership.
- Real DeepSeek or another configured provider in PR CI: nondeterministic,
  billable, and requires a secret/data-egress decision.
- Reusing the developer database or API: can pass against stale state and can
  mutate non-test data.
- Making the repository public only to unlock free branch protection: changes
  confidentiality and is not authorized by a CI implementation task.

## Verification

The Step 40 acceptance PR must first contain one explicit failing probe and show
a failed required check. The probe is then removed, the same PR is rerun, and
all four checks must pass before merge. The PR URL and final check run are
recorded in the Step 40 development log and CI runbook.

This acceptance was executed on 2026-08-27 in
[PR #1](https://github.com/nbnb9/customer-care-ai/pull/1):

- Backend run
  [33079007887](https://github.com/nbnb9/customer-care-ai/actions/runs/33079007887)
  reached pytest and rejected the named deliberate probe. It also exposed an
  environment-dependent seed-safety test, which was fixed rather than waived.
- The clean code revision `4012502` then passed Backend
  [33080056648](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056648),
  Frontend
  [33080056782](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056782),
  E2E
  [33080056709](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056709),
  and Eval and Security
  [33080056681](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056681).
- The drill also found two non-probe defects hidden by prior local state: route
  types were not generated before a fresh TypeScript check, and one Playwright
  locator was ambiguous. Both were reproduced and fixed before the green run.
