# Contributing

Thank you for helping improve CustomerCare AI / CareOps.

## Before opening a change

- Use an issue to describe material features, architecture changes, or new
  external integrations before implementation.
- Keep examples and tests fully synthetic. Never submit customer data, private
  endpoints, credentials, tokens, `.env` files, or unredacted production logs.
- Security findings must follow [SECURITY.md](SECURITY.md), not a public issue.
- Preserve the documented fail-closed authorization, tenant-isolation,
  confirmation, approval, and idempotency boundaries.

## Development workflow

1. Fork the repository and create a focused branch.
2. Follow [the development conventions](docs/development-conventions.md).
3. Add or update tests for behavior changes.
4. Run the relevant local checks documented in
   [the CI runbook](docs/runbooks/ci-quality-gates.md).
5. Open a pull request and complete the repository template.

The required checks are Backend, Frontend, E2E, and Eval and Security. A green
synthetic test suite does not by itself establish production readiness.

## Contribution license

Unless you explicitly state otherwise, contributions intentionally submitted to
this project are licensed under the Apache License 2.0, consistent with section 5
of [LICENSE](LICENSE).

Be respectful, specific, and evidence-oriented in issues and reviews. Harassment,
personal attacks, exposure of private information, and unsafe demonstrations
using third-party systems are not acceptable.
