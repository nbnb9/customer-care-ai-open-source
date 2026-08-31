# Triage evaluation report

Provider/model: `fake` / `fake-deterministic-v1`

Prompt/schema: `triage-v2` / `triage-result-v1`
Evaluator: `triage-evaluator-v2`
Prompt SHA-256: **not applicable/unavailable for this gateway**
Runs per case: **3**
Cost: **USD 0**; tokens: **0**
Cases: **35** (**33** unique texts; public **25**, holdout **10**)
Case fingerprint: `4aede141543d6f8adc8b710418c37676e442083a4cf49a99a1a8960bc32c8154`

## Aggregate

| Metric | Value |
|---|---:|
| `R3_recall` | 1.0 |
| `case_pass_rate` | 1.0 |
| `final_schema_validity_rate` | 1.0 |
| `human_review_accuracy` | 1.0 |
| `identity_invariance_rate` | 1.0 |
| `intent_accuracy` | 1.0 |
| `normal_fallback_rate` | 0.0 |
| `p95_latency_ms` | 3.273 |
| `policy_floor_applied_rate` | 0.0 |
| `pre_policy_R3_recall` | 1.0 |
| `pre_policy_intent_accuracy` | 1.0 |
| `pre_policy_risk_accuracy` | 1.0 |
| `required_override_rate` | 0.0 |
| `risk_accuracy` | 1.0 |
| `safe_fallback_rate` | 1.0 |
| `schema_validity_rate` | 1.0 |
| `volatility_rate` | 0.0 |

## Threshold gate

Overall: **PASS**
Threshold fingerprint: `9e754c6b4246303e511b3b7cf35c82bfcc18c8237c3c37d196330f750570ed17`

| Metric | Rule | Actual | Result |
|---|---:|---:|---|
| `R3_recall` | min 1.0 | 1.0 | PASS |
| `case_pass_rate` | min 0.95 | 1.0 | PASS |
| `human_review_accuracy` | min 0.95 | 1.0 | PASS |
| `identity_invariance_rate` | min 1.0 | 1.0 | PASS |
| `intent_accuracy` | min 0.95 | 1.0 | PASS |
| `required_override_rate` | max 0.0 | 0.0 | PASS |
| `normal_fallback_rate` | max 0.0 | 0.0 | PASS |
| `p95_latency_ms` | max 50.0 | 3.273 | PASS |
| `risk_accuracy` | min 0.95 | 1.0 | PASS |
| `safe_fallback_rate` | min 1.0 | 1.0 | PASS |
| `schema_validity_rate` | min 1.0 | 1.0 | PASS |
| `volatility_rate` | max 0.0 | 0.0 | PASS |

## Verification-level slices

| Verification | Cases pass | Intent | Risk | Human review | Fallback |
|---|---:|---:|---:|---:|---:|
| `demo_verified` | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| `unspecified` | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| `unverified` | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |

## Diagnostic slices

| Dimension | Value | Cases | Runs | Case pass | Intent | Risk | Human review | Fallback | Required override |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `intent` | `account` | 2 | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `complaint` | 3 | 9 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `order_status` | 6 | 18 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `product_faq` | 3 | 9 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `return_request` | 9 | 27 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `safety` | 4 | 12 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `unknown` | 5 | 15 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `intent` | `warranty` | 3 | 9 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `language` | `en-US` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `language` | `mixed-language` | 2 | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `language` | `unspecified` | 25 | 75 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `language` | `zh-CN` | 7 | 21 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `risk` | `R0` | 3 | 9 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `risk` | `R1` | 13 | 39 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `risk` | `R2` | 12 | 36 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `risk` | `R3` | 7 | 21 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `split` | `holdout` | 10 | 30 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `split` | `public` | 25 | 75 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `ambiguous` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `complaint` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `en-US` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `identity-slice` | 2 | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `missing-context` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `missing-order-number` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `mixed-language` | 2 | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `order-status` | 2 | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `prompt_injection` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `qa-regression` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `r2` | 5 | 15 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `r3` | 2 | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `refund` | 3 | 9 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `replacement` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `routing` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `safety` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `untagged` | 24 | 72 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `warranty` | 1 | 3 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| `tag` | `zh-CN` | 7 | 21 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |

## Identity invariance

Rate: **1.0** across **6** repeated comparisons.

| Group | Run | Verification levels | Result | Candidate area |
|---|---:|---|---|---|
| `verification-refund-holdout-001` | 1 | demo_verified, unverified | PASS | - |
| `verification-refund-holdout-001` | 2 | demo_verified, unverified | PASS | - |
| `verification-refund-holdout-001` | 3 | demo_verified, unverified | PASS | - |
| `verification-refund-public-001` | 1 | demo_verified, unverified | PASS | - |
| `verification-refund-public-001` | 2 | demo_verified, unverified | PASS | - |
| `verification-refund-public-001` | 3 | demo_verified, unverified | PASS | - |

## Fault injection

| Fault | Error classification | Raw schema | Final schema | Safe fallback | Result |
|---|---|---|---|---|---|
| `invalid` | `invalid_provider_output` | invalid | valid | yes | PASS |
| `rate_limit` | `provider_rate_limit` | no output | valid | yes | PASS |
| `timeout` | `provider_timeout` | no output | valid | yes | PASS |

## Cases

| Case | Split | Intent | Risk | Language | Verification | Pass | Volatile | Failures → candidate area |
|---|---|---|---|---|---|---|---|---|
| `triage-001` | `public` | `order_status` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-002` | `public` | `warranty` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-003` | `public` | `return_request` | `R2` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-004` | `public` | `unknown` | `R3` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-005` | `public` | `safety` | `R3` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-006` | `public` | `product_faq` | `R0` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-007` | `public` | `product_faq` | `R0` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-008` | `public` | `product_faq` | `R0` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-009` | `public` | `order_status` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-010` | `public` | `complaint` | `R2` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-011` | `public` | `return_request` | `R2` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-012` | `public` | `safety` | `R3` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-013` | `public` | `warranty` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-014` | `public` | `account` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-015` | `public` | `account` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-016` | `public` | `safety` | `R3` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-017` | `public` | `order_status` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-018` | `public` | `unknown` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-019` | `public` | `unknown` | `R3` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-020` | `public` | `return_request` | `R2` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-021` | `public` | `order_status` | `R1` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-022` | `public` | `complaint` | `R2` | `unspecified` | `unspecified` | PASS | no | - |
| `triage-023` | `public` | `return_request` | `R2` | `unspecified` | `unverified` | PASS | no | - |
| `triage-024` | `public` | `return_request` | `R2` | `unspecified` | `demo_verified` | PASS | no | - |
| `triage-holdout-001` | `holdout` | `safety` | `R3` | `zh-CN` | `unspecified` | PASS | no | - |
| `triage-holdout-002` | `holdout` | `unknown` | `R3` | `mixed-language` | `unspecified` | PASS | no | - |
| `triage-holdout-003` | `holdout` | `return_request` | `R2` | `zh-CN` | `unspecified` | PASS | no | - |
| `triage-holdout-004` | `holdout` | `complaint` | `R2` | `zh-CN` | `unspecified` | PASS | no | - |
| `triage-holdout-005` | `holdout` | `unknown` | `R1` | `zh-CN` | `unspecified` | PASS | no | - |
| `triage-holdout-006` | `holdout` | `order_status` | `R1` | `zh-CN` | `unspecified` | PASS | no | - |
| `triage-holdout-007` | `holdout` | `warranty` | `R1` | `en-US` | `unspecified` | PASS | no | - |
| `triage-holdout-008` | `holdout` | `order_status` | `R1` | `mixed-language` | `unspecified` | PASS | no | - |
| `triage-holdout-009` | `holdout` | `return_request` | `R2` | `zh-CN` | `unverified` | PASS | no | - |
| `triage-holdout-010` | `holdout` | `return_request` | `R2` | `zh-CN` | `demo_verified` | PASS | no | - |
| `triage-qa-r2-replacement-routing` | `public` | `return_request` | `R2` | `unspecified` | `unspecified` | PASS | no | - |

## Failure classification and remediation

| Classification | Candidate remediation area | Affected cases |
|---|---|---:|
| none | - | 0 |

### Remediation guide

- `intent_mismatch` / `summary_mismatch`: review the Prompt and representative examples before adding brittle keyword rules.
- `missing_information_mismatch`: review annotation quality and coverage in the evaluation data.
- `risk_mismatch`, `r3_miss`, or route/priority/review mismatch: repair deterministic business rules and their tests first.
- `required_override`: the UI must preserve an explicit human accept/edit/reject path; offline evaluation cannot prove that UX.
- Schema, timeout, rate-limit, or volatility failures: repair the provider gateway, parsing, retry, or fallback boundary.

## Interpretation notes

- `required_override_rate` is inferred from ground-truth mismatches; it is not an observed agent acceptance rate.
- Candidate remediation areas are triage hints, not proven root causes or ownership assignments; a human must diagnose each failure.
- Fault injection is isolated from normal-case accuracy. Its `safe_fallback_rate` covers invalid output, timeout, and rate limiting.
- Fake provider cost is exactly USD 0. Real-provider cost and tokens are reported as unavailable unless collected.
- This synthetic repository holdout is not secret, blind, statistically independent, or contamination-proof.
- Offline results do not prove the UI override/review controls; those require E2E and human acceptance testing.
- Identity-invariance pairs are separate cases and therefore have separate weight in aggregate rates; unique-text counts are shown above.
- A pass does not establish production quality, fairness, robustness, or enterprise readiness.
