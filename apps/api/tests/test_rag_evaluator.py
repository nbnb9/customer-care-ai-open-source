import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.evals.rag import (
    RagEvalCase,
    RagEvalConfigurationError,
    RagExpected,
    RagPrincipal,
    apply_thresholds,
    load_cases,
    load_corpus,
    load_thresholds,
    run_evaluation,
    write_reports,
)
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
)
from app.modules.llm_gateway.base import FakeLLMGateway


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "evals" / "rag"


def _versioned_inputs():
    data_dir = _data_dir()
    return (
        load_corpus(data_dir / "corpus.json"),
        load_cases([data_dir / "cases.json", data_dir / "holdout_cases.json"]),
        load_thresholds(data_dir / "thresholds.json"),
    )


def test_versioned_rag_eval_separates_layers_and_withdraws_wrong_policy(db, tmp_path):
    corpus, cases, thresholds = _versioned_inputs()

    report = run_evaluation(db, corpus, cases, gateway=FakeLLMGateway())
    results = apply_thresholds(report, thresholds)
    json_path, markdown_path = write_reports(report, tmp_path / "reports")

    assert len(cases) == 11
    assert all(item["passed"] for item in results)
    assert report["aggregate"] == {
        "case_pass_rate": 1.0,
        "recall_at_k": 1.0,
        "retrieval_allowed_only_rate": 1.0,
        "permission_leak_rate": 0.0,
        "expired_hit_rate": 0.0,
        "citation_correctness_rate": 1.0,
        "answer_key_conclusion_rate": 1.0,
        "handoff_correctness_rate": 1.0,
        "untrusted_answer_evidence_rate": 0.0,
    }
    assert report["inputs"]["split_counts"] == {"public": 8, "holdout": 3}
    assert report["versions"]["provider"] == "fake"
    assert report["versions"]["prompt"] == "knowledge-answer-v1"

    checks = {case["id"]: case["checks"] for case in report["cases"]}
    assert checks["rag-001-nw-customer-return"]["retrieval_recall_at_k"] is True
    assert checks["rag-001-nw-customer-return"]["citation_correct"] is True
    assert checks["rag-qa-current-return-policy-v2"]["citation_correct"] is True
    assert checks["rag-002-nw-customer-internal-denied"]["handoff_correct"] is True
    assert checks["rag-004-nw-customer-expired-policy"]["expired_hit_absent"] is True
    assert checks["rag-005-nw-customer-cross-tenant"]["permission_leak_absent"] is True
    assert (
        checks["rag-006-nw-customer-untrusted-evidence"][
            "untrusted_answer_evidence_absent"
        ]
        is True
    )

    rollback = report["rollback_drill"]
    assert rollback["passed"] is True
    assert rollback["pre_withdrawal"]["detection_triggered"] is True
    assert rollback["pre_withdrawal"]["incorrect_policy_answered"] is True
    assert rollback["post_withdrawal"] == {
        "search_result_count": 0,
        "needs_handoff": True,
        "citation_count": 0,
        "answer_contains_incorrect_term": False,
        "passed": True,
    }
    assert rollback["post_withdrawal_regression"]["case_passed"] is True

    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.document_key == "rag-eval-incorrect-return-policy"
        )
    )
    assert document is not None
    assert document.status is KnowledgeDocumentStatus.withdrawn
    chunks = db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    ).all()
    assert chunks
    assert all(chunk.status is KnowledgeDocumentStatus.withdrawn for chunk in chunks)
    assert all(chunk.chunk_metadata["status"] == "withdrawn" for chunk in chunks)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["rollback_drill"]["withdrawal_record"]["audit_event_id"]
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Aggregate metrics" in markdown
    assert "Error-policy withdrawal drill: PASS" in markdown


def test_rag_eval_rejects_unknown_document_references(db):
    corpus, cases, _ = _versioned_inputs()
    bad_case = cases[0].model_copy(
        update={"allowed_document_keys": ["not-in-the-corpus"]}
    )

    with pytest.raises(RagEvalConfigurationError, match="unknown corpus"):
        run_evaluation(db, corpus, [bad_case, *cases[1:]], gateway=FakeLLMGateway())


def test_rag_case_contract_rejects_incomplete_or_contradictory_acl_expectations():
    with pytest.raises(ValueError, match="requires customer_id"):
        RagPrincipal.model_validate(
            {
                "user_id": "customer",
                "tenant_id": "tenant",
                "role": "customer",
            }
        )
    with pytest.raises(ValueError, match="only customer"):
        RagPrincipal.model_validate(
            {
                "user_id": "agent",
                "tenant_id": "tenant",
                "role": "agent",
                "customer_id": "customer",
            }
        )
    with pytest.raises(ValueError, match="must appear together"):
        RagExpected.model_validate(
            {
                "expected_document_key": "document",
                "should_handoff": True,
            }
        )
    with pytest.raises(ValueError, match="must also be forbidden"):
        RagExpected.model_validate(
            {
                "expired_document_keys": ["old-policy"],
                "should_handoff": True,
            }
        )
    with pytest.raises(ValueError, match="non-handoff case"):
        RagExpected.model_validate({"should_handoff": False})

    with pytest.raises(ValueError, match="allowed and forbidden"):
        RagEvalCase.model_validate(
            {
                "id": "contradictory-case",
                "principal": {
                    "user_id": "customer",
                    "tenant_id": "tenant",
                    "role": "customer",
                    "customer_id": "customer",
                },
                "scope": {
                    "product": "product",
                    "region": "region",
                    "language": "zh-CN",
                },
                "question": "question",
                "allowed_document_keys": ["policy"],
                "expected": {
                    "forbidden_document_keys": ["policy"],
                    "should_handoff": True,
                },
            }
        )


def test_rag_input_loaders_and_thresholds_fail_closed(tmp_path):
    corpus, cases, thresholds = _versioned_inputs()
    duplicate_corpus = corpus.model_dump(mode="json")
    duplicate_corpus["documents"].append(duplicate_corpus["documents"][0])
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(duplicate_corpus), encoding="utf-8")
    with pytest.raises(RagEvalConfigurationError, match="document ids"):
        load_corpus(corpus_path)

    public_path = tmp_path / "cases.json"
    holdout_path = tmp_path / "holdout.json"
    public_path.write_text(
        json.dumps([cases[0].model_dump(mode="json")]), encoding="utf-8"
    )
    holdout_path.write_text("[]", encoding="utf-8")
    with pytest.raises(RagEvalConfigurationError, match="must not be empty"):
        load_cases([public_path, holdout_path])

    bad_thresholds = thresholds.model_dump(mode="json")
    bad_thresholds["metrics"] = {"unknown": {"min": 1.0}}
    threshold_path = tmp_path / "thresholds.json"
    threshold_path.write_text(json.dumps(bad_thresholds), encoding="utf-8")
    with pytest.raises(RagEvalConfigurationError, match="unsupported"):
        load_thresholds(threshold_path)

    with pytest.raises(RagEvalConfigurationError, match="provider mismatch"):
        apply_thresholds(
            {
                "versions": {"provider": "different-provider"},
                "aggregate": {},
            },
            thresholds,
        )


def test_fixed_evaluation_gateway_is_recorded_without_external_model_access(db):
    class FixedEvaluationGateway(FakeLLMGateway):
        provider_name = "approved-fixed-test-provider"
        model_name = "approved-fixed-test-model"

    corpus, cases, _ = _versioned_inputs()
    report = run_evaluation(
        db,
        corpus,
        cases,
        gateway=FixedEvaluationGateway(),
        run_rollback_drill=False,
    )

    assert report["versions"]["provider"] == "approved-fixed-test-provider"
    assert report["versions"]["model"] == "approved-fixed-test-model"
    assert "rollback_drill" not in report
    assert report["aggregate"]["case_pass_rate"] == 1.0
