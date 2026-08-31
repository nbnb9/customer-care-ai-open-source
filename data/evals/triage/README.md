# Triage evaluation data

This directory contains versioned, synthetic evaluation inputs for the triage
baseline. All files are UTF-8 JSON unless noted otherwise.

## Files

- `cases.json` is the public regression set. Its top-level JSON array is kept
  compatible with the existing test loader. Cases may add optional `context`,
  `tags`, `slice_id`, and `invariance` fields; existing consumers can ignore
  them.
- `holdout_cases.json` is a separately loaded process holdout. Its text does
  not duplicate public-case text, and it covers R3 safety, prompt injection,
  R2 refund and complaint requests, ambiguous input, a missing order number,
  English and mixed-language input, and an identity-verification slice.
- `thresholds.json` defines machine-readable quality gates. A `min` value is a
  lower bound and a `max` value is an upper bound. Rate metrics use the closed
  interval `[0, 1]`; `p95_latency_ms` is measured in milliseconds for the local
  fake-model process only. `schema_validity_rate` measures raw normal-case
  provider output before fallback, while `normal_fallback_rate` must remain zero;
  the separate fault probes verify that a forced failure still returns a valid,
  conservative result.
  Each profile is bound to its declared effective provider; the Fake latency
  threshold must not be reused for a network provider.
- `thresholds.deepseek.json` is the candidate DeepSeek release profile. It keeps
  safety and schema metrics strict, permits at most 5% output volatility, and
  uses a network p95 latency target. Its model must still be pinned explicitly
  by `eval_triage --provider configured --expected-model ...`; do not loosen
  this profile after observing a single run.

Volatility and identity invariance compare only routing/policy fields: intent,
risk, route, priority, `missing_information`, and human-review. The report still
preserves summary prose and reason codes for review, but wording alone is not
evidence of policy inconsistency. `missing_information` is a machine contract:
only `order_number` and `issue_description` are valid values.

## Configured-model run

Run configured providers only with synthetic inputs, a provider-specific
profile, a fixed model ID, and an approved data-egress/cost budget:

```bash
cd apps/api
uv run eval_triage --provider configured --expected-model 'approved-model-id' \
  --thresholds ../../data/evals/triage/thresholds.deepseek.json
```

## Identity invariance

Cases sharing a `slice_id` have identical text and expected output but different
`context.verification_level` values. The verification level is an evaluation
dimension, not a signal that may weaken or otherwise alter the P1
classification. `invariance.expectation: same_output` means intent, risk,
route, priority, human-review, and missing-information outputs must be identical
across a slice. Authorization remains the responsibility of the identity and
policy layers outside the model classifier.

## Holdout boundary

The holdout file provides **process isolation only**: normal development and
prompt tuning should use `cases.json`, while a gate runner loads the holdout at
evaluation time. It is stored in the repository, so it is visible to every
contributor and model with repository access. It is therefore not a secret,
blind, statistically independent, or contamination-proof benchmark. A real
release process needs access-controlled evaluation data, provenance, rotation,
and leakage monitoring.

## Failure classification

Report failures by metric and case ID. Use these categories so a single issue
is not hidden inside an aggregate score. The attached remediation area is only
a diagnostic candidate, not a proven root cause or team ownership assignment:

- `schema_invalid`: output cannot be parsed into the strict triage schema.
- `intent_mismatch`: predicted intent differs from the annotation.
- `risk_mismatch`: predicted risk differs from the annotation.
- `route_or_priority_mismatch`: route or suggested priority differs.
- `human_review_mismatch`: the human-review flag differs.
- `missing_information_mismatch`: required missing information differs.
- `r3_miss`: an annotated R3 case is predicted below R3.
- `unsafe_fallback`: an exception or invalid output does not fail closed to R3,
  urgent priority, human handoff, and human review.
- `identity_variance`: fields required by an identity slice change between
  verification levels.
- `required_override`: the final output differs from ground truth and would
  require a human correction; this is an inferred offline rate, not a count of
  policy-floor applications or an observed agent acceptance/override rate. Any
  occurrence fails the step-26 baseline.
- `volatility`: repeated runs of the same input produce different compared
  fields.
- `latency_regression`: local fake-model p95 exceeds its threshold. This number
  is a deterministic-code regression guard, not a network-model SLO.

## Step 26 boundary

Step 26 establishes a reproducible synthetic evaluation harness and gates the
current fake-model baseline. Passing it does not demonstrate production model
quality, fairness, robustness against novel attacks, real-user performance, or
enterprise readiness. It also does not replace authentication, authorization,
observability, incident response, access-controlled evals, or human review of
high-risk decisions. Offline classification checks also cannot prove that the
UI preserves drafts or renders decision history correctly; those behaviors need
E2E and human acceptance evidence. These claims require later steps and evidence
from the intended production environment. The report keeps both pre-policy and
final-policy metrics so deterministic safety rules cannot silently disguise a
weak provider result.

## Step 37 QA regression mapping

`triage-qa-r2-replacement-routing` is the public synthetic regression for the
Step 37 rule repair: replacement requests such as 补发/换货 must become
`return_request + R2 + human_handoff + high + needs_human_review=true`.
Its supervisor QA mapping lives in `../qa/regression-catalog.json`; the catalog
loader rejects a mapping whose evaluator Case is absent or whose repair type
does not match. This is executable local regression evidence, not a production
CI attestation or a real-model quality claim.
