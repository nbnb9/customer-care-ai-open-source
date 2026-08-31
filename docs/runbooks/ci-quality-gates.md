# Step 40 CI quality-gate runbook

## Required PR checks

Do not merge a normal pull request until these four checks are successful:

1. Backend / backend
2. Frontend / frontend
3. E2E / e2e
4. Eval and Security / eval-security

The current GitHub plan cannot enforce branch protection on this private
repository. Check completion is therefore a human rule until the plan changes.
Do not describe it as an enforced required-check policy.

## Local parity commands

Backend:

    cd apps/api
    uv sync --frozen --all-groups
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy app
    uv run alembic upgrade head
    uv run alembic check
    uv run pytest -q

Frontend:

    cd apps/web
    pnpm install --frozen-lockfile
    pnpm lint
    pnpm typecheck
    pnpm test:unit
    pnpm build

Browser and synthetic AI/security:

    cd apps/web
    pnpm test:e2e

    cd ../api
    uv run eval_triage
    uv run eval_rag --use-default-test-database
    uv run pytest -q tests/security

Local database commands still require an isolated loopback test database. CI
does not use these local instances; each database workflow receives a fresh
GitHub service container.

## Failure triage

- Dependency install failure: verify the committed lockfile changed with the
  manifest and that the runner/version pins still exist.
- Migration failure: reproduce against an empty _test database and inspect the
  Alembic upgrade/check result. Do not point the command at a shared database.
- Backend or unit failure: fix the product or test contract; do not rerun until
  green by chance.
- E2E failure: download the playwright-failure artifact. It has screenshots or
  traces only on failure and is retained for seven days.
- Eval failure: download synthetic-evals and compare provider/model, data,
  prompt/schema, knowledge, thresholds, and report fingerprints. CI must show
  fake/fake-deterministic-v1.
- Runner/platform incident: record the incident, approver, follow-up, and a
  deadline in the PR template. A failing application test is never a platform
  bypass.

Use the GitHub CLI for a compact status view:

    gh pr checks <number> --watch
    gh run view <run-id> --log-failed

## Red-to-green acceptance drill

For Step 40 only, add one isolated test whose assertion explicitly says it is
the deliberate CI failure probe. Push it to the feature PR and retain the
failed check in the PR history. Remove the probe in a second commit, push again,
and wait for all four checks. Squash merge so the known-failing probe is never
present on main.

Do not repeat this drill in routine product PRs. Normal red checks represent a
defect to diagnose.

## Step 40 executed evidence

The acceptance drill ran in
[PR #1](https://github.com/nbnb9/customer-care-ai/pull/1) on 2026-08-27.

- Deliberate pytest rejection: Backend run
  [33079007887](https://github.com/nbnb9/customer-care-ai/actions/runs/33079007887).
  The log names `test_step40_deliberate_red_gate_probe`; the probe was removed
  before the clean revision.
- Clean revision `4012502`: Backend
  [33080056648](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056648),
  Frontend
  [33080056782](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056782),
  E2E
  [33080056709](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056709),
  and Eval and Security
  [33080056681](https://github.com/nbnb9/customer-care-ai/actions/runs/33080056681)
  all passed.
- Local clean-revision parity: 230 backend tests, 12 Vitest tests, 14 Chromium
  Playwright tests, production Web build, and the targeted seed/CI contract
  tests all passed.

The first two red revisions also exposed a missing `next typegen` step, an
environment-dependent seed-safety test, and an ambiguous Playwright locator.
These were treated as defects and fixed; only the explicitly named probe was an
intentional failure.

## Enabling enforcement later

When the repository plan supports private branch protection, configure main to:

- require pull requests before merging;
- require the four exact status checks listed above to be up to date;
- dismiss stale approvals after new commits;
- block force pushes and deletion;
- require conversation resolution;
- limit bypass authority to an audited owner group.

After configuration, query the branch protection API and capture its response
as evidence. Until that succeeds, the project remains NO-GO for enforced
multi-person production delivery.
