# ADR-0014: Insert a productization gate before staging

- Status: Accepted
- Date: 2026-08-30

## Context

Steps 1–41 established a reviewable local/CI baseline for tenant isolation, ticket workflows, RAG ACL,
evidence-bound answers, customer conversations, human handoff, controlled after-sales workflows, QA,
security regression, observability, CI and complete local Compose.

The current Web application remains an engineering demonstration. It exposes synthetic identity switching,
organizes navigation by demo role, combines unrelated operational and technical modules on long pages, and has
no repository evidence of buyer discovery, task-based usability validation, a design system or a real external
order contract. Deploying that shape to staging would spend deployment effort before proving that the product
solves a valuable operator problem.

The general help-desk and Shopify ecommerce support markets already include vendors advertising unified
omnichannel workspaces, routing, SLAs, reporting, order context and autonomous order actions. A generic
"AI customer support" position is therefore not a defensible assumption.

## Decision

Insert Step 41.5, the Productization Phase, before the previously planned Step 42. Step 42 remains blocked until
the productization exit gate is explicitly approved.

The initial positioning hypothesis is a governed AI after-sales collaboration layer for higher-value consumer
electronics and smart-device brands that already operate a help desk and an OMS. The phase will validate or
reject that hypothesis; it is not treated as proven product-market fit.

The phase has five required workstreams:

1. Buyer/problem discovery with real after-sales leaders, agents, supervisors and security/integration owners.
2. Role-specific information architecture and task-testable customer, agent and supervisor prototypes.
3. A versioned design system and accessibility baseline before page-by-page migration.
4. A real Shopify Dev Store contract, starting read-only and keeping uncertain external writes fail-closed.
5. A baseline-controlled usability gate based on real-agent task completion, active handle time and severe errors.

The product will not initially replace a complete omnichannel help desk. It should integrate with existing systems
and concentrate its differentiation on warranty/RMA, policy evidence, confirmation/approval, audit and safe
external execution.

## Consequences

### Positive

- Staging work is deferred until there is evidence that the user experience and integration deserve deployment.
- Engineering strengths become visible product capabilities instead of developer-only controls.
- The team measures business tasks and errors rather than feature count or model calls.
- A real supplier contract exposes gaps that mocks cannot, while the default CI remains deterministic and secret-free.
- The design system reduces continued one-off utility styling and inconsistent state semantics.

### Costs and risks

- The roadmap grows and Step 42 is delayed.
- Real user recruitment and a Shopify development organization require external coordination.
- The positioning may be rejected, requiring another segment rather than cosmetic iteration.
- A standalone collaboration layer can add tool switching unless embedded help-desk integration is validated.
- Shopify `returnCreate` does not by itself prove a retry-safe provider idempotency contract; write integration may
  remain blocked after the read integration succeeds.

## Alternatives rejected

### Continue directly to staging

Rejected because deployment readiness does not establish product usefulness, usability or buyer demand.

### Apply a visual refresh only

Rejected because the primary defects are role architecture, task flow, content semantics, external context and
measurement—not only colors and spacing.

### Build a full Zendesk/Intercom replacement

Rejected for the initial productization phase because omnichannel ingestion, telephony, workforce management,
CRM depth, marketplace integrations and reporting would overwhelm the current differentiating assets.

### Position as a generic Shopify AI agent

Rejected as the default hypothesis because established ecommerce support products already advertise Shopify
order context and automated returns, cancellations, refunds and reships. The project must validate a narrower,
governed warranty/RMA and approval-oriented problem.

## Exit gate

Step 42 can resume only when:

- the buyer/problem evidence in `docs/product/productization-phase.md` is complete;
- critical prototypes and design-system components pass accessibility and task-flow review;
- a real Shopify read contract passes the dedicated sandbox suite;
- the real-agent gate in `docs/product/agent-usability-acceptance.md` passes without severe safety errors; and
- product, after-sales operations and security owners each authorize staging.

Engineering completion alone cannot satisfy the last condition.
