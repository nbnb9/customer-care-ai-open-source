# RAG layered evaluation report

Provider/model: `deepseek` / `deepseek-chat`
Prompt/schema: `knowledge-answer-v1` / `knowledge-answer-result-v1`

## Aggregate metrics

| Metric | Value |
| --- | ---: |
| `answer_key_conclusion_rate` | 1.000000 |
| `case_pass_rate` | 1.000000 |
| `citation_correctness_rate` | 1.000000 |
| `expired_hit_rate` | 0.000000 |
| `handoff_correctness_rate` | 1.000000 |
| `permission_leak_rate` | 0.000000 |
| `recall_at_k` | 1.000000 |
| `retrieval_allowed_only_rate` | 1.000000 |
| `untrusted_answer_evidence_rate` | 0.000000 |

## Thresholds: PASS

| Metric | Actual | Requirement | Result |
| --- | ---: | --- | --- |
| `answer_key_conclusion_rate` | 1.000000 | min 0.900000 | PASS |
| `case_pass_rate` | 1.000000 | min 0.900000 | PASS |
| `citation_correctness_rate` | 1.000000 | min 1.000000 | PASS |
| `expired_hit_rate` | 0.000000 | max 0.000000 | PASS |
| `handoff_correctness_rate` | 1.000000 | min 1.000000 | PASS |
| `permission_leak_rate` | 0.000000 | max 0.000000 | PASS |
| `recall_at_k` | 1.000000 | min 1.000000 | PASS |
| `retrieval_allowed_only_rate` | 1.000000 | min 1.000000 | PASS |
| `untrusted_answer_evidence_rate` | 0.000000 | max 0.000000 | PASS |

## Error-policy withdrawal drill: PASS

The pre-release gate detected the intentionally incorrect policy: `True`.
After withdrawal: 0 retrieval result(s), handoff `True`, 0 citation(s).
Post-withdrawal regression cases passed: `True`.

## Failures

No case-level failures.

## Interpretation boundary

- Synthetic cases verify this repository's deterministic corpus and ACL behavior; they do not establish production retrieval recall.
- Lexical term support is a regression check, not semantic entailment or legal/policy approval of a model answer.
- Configured-model results remain dependent on the recorded provider, model, prompt, corpus, and threshold profile.
- A passing offline evaluation does not validate production identity providers, caches, human operations, or incident response timing.
