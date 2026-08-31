import json
from pathlib import Path

import pytest

from app.evals.triage import (
    EvalCase,
    EvalConfigurationError,
    ThresholdConfig,
    _is_safe_fallback,
    apply_thresholds,
    evaluate,
    load_cases,
    load_thresholds,
    write_reports,
)
from app.evals.triage_cli import build_parser, main
from app.models.tickets import TicketPriority
from app.modules.llm_gateway.base import FakeLLMGateway, generate_triage_safe
from app.modules.llm_gateway.schemas import Intent, RiskLevel, Route, TriageResult

pytestmark = pytest.mark.offline


class StepClock:
    def __init__(self, step: float = 0.001):
        self.current = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.current
        self.current += self.step
        return value


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _versioned_inputs() -> tuple[list[EvalCase], ThresholdConfig]:
    data_dir = _repo_root() / "data" / "evals" / "triage"
    cases = load_cases([data_dir / "cases.json", data_dir / "holdout_cases.json"])
    return cases, load_thresholds(data_dir / "thresholds.json")


def test_versioned_eval_passes_and_faults_fail_closed():
    cases, thresholds = _versioned_inputs()
    report = evaluate(cases, gateway=FakeLLMGateway(), runs=3, timer=StepClock())
    results = apply_thresholds(report, thresholds)

    assert len(cases) >= 30
    assert all(result["passed"] for result in results)
    assert report["aggregate"] == {
        "case_pass_rate": 1.0,
        "intent_accuracy": 1.0,
        "pre_policy_intent_accuracy": 1.0,
        "risk_accuracy": 1.0,
        "pre_policy_risk_accuracy": 1.0,
        "schema_validity_rate": 1.0,
        "final_schema_validity_rate": 1.0,
        "human_review_accuracy": 1.0,
        "R3_recall": 1.0,
        "pre_policy_R3_recall": 1.0,
        "safe_fallback_rate": 1.0,
        "normal_fallback_rate": 0.0,
        "policy_floor_applied_rate": 0.0,
        "volatility_rate": 0.0,
        "required_override_rate": 0.0,
        "p95_latency_ms": 1.0,
        "identity_invariance_rate": 1.0,
    }
    assert report["identity_invariance"]["comparison_count"] == 6
    assert report["inputs"]["case_count"] == 35
    assert report["inputs"]["unique_text_count"] == 33
    assert report["inputs"]["split_counts"] == {"public": 25, "holdout": 10}
    splits = {case["id"]: case["split"] for case in report["cases"]}
    assert splits["triage-001"] == "public"
    assert splits["triage-qa-r2-replacement-routing"] == "public"
    assert splits["triage-holdout-001"] == "holdout"
    assert set(report["verification_level_slices"]) == {
        "demo_verified",
        "unverified",
        "unspecified",
    }
    faults = {item["fault"]: item for item in report["fault_injections"]}
    assert set(faults) == {"invalid", "rate_limit", "timeout"}
    assert all(item["safe_fallback"] for item in faults.values())
    assert faults["invalid"]["raw_schema_valid"] is False
    assert faults["timeout"]["raw_schema_valid"] is None
    assert faults["rate_limit"]["raw_schema_valid"] is None
    assert faults["invalid"]["observed_error_kind"] == "invalid_provider_output"
    assert faults["rate_limit"]["observed_error_kind"] == "provider_rate_limit"
    assert faults["timeout"]["observed_error_kind"] == "provider_timeout"
    assert report["cost"] == {
        "status": "measured",
        "currency": "USD",
        "amount": 0,
        "tokens": 0,
    }


def test_reports_are_deterministic_and_version_named(tmp_path):
    cases, thresholds = _versioned_inputs()
    report = evaluate(cases[:2], gateway=FakeLLMGateway(), runs=2, timer=StepClock())
    apply_thresholds(report, thresholds)

    first_json, first_markdown = write_reports(report, tmp_path / "first")
    second_json, second_markdown = write_reports(report, tmp_path / "second")

    assert first_json.stem == first_markdown.stem
    assert first_json.stem.startswith(
        "triage-eval-fake-fake-deterministic-v1-triage-v2-"
        "triage-result-v1-triage-evaluator-v2"
    )
    assert (
        f"-cases-{report['inputs']['case_fingerprint_sha256'][:12]}" in first_json.stem
    )
    threshold_hash = report["thresholds"]["config_fingerprint_sha256"][:12]
    assert f"-thresholds-{threshold_hash}" in first_json.stem
    assert "-runs-2-report-" in first_json.stem
    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_markdown.read_bytes() == second_markdown.read_bytes()
    payload = json.loads(first_json.read_text(encoding="utf-8"))
    assert payload["evaluation_contract"]["threshold_comparators"]
    markdown = first_markdown.read_text(encoding="utf-8")
    for expected_section in (
        "## Verification-level slices",
        "## Identity invariance",
        "## Fault injection",
        "## Failure classification and remediation",
        "## Interpretation notes",
    ):
        assert expected_section in markdown
    assert "not an observed agent acceptance rate" in markdown
    assert "require E2E and human acceptance testing" in markdown


def test_volatility_and_identity_comparison_ignore_summary_and_reason_code_prose():
    class FreeTextVariantGateway:
        provider_name = "test"
        model_name = "free-text-variant"
        prompt_version = "test-v1"

        def __init__(self):
            self.calls = 0

        def generate_structured(self, *, text: str) -> TriageResult:
            del text
            self.calls += 1
            return TriageResult(
                intent=Intent.product_faq,
                risk_level=RiskLevel.R0,
                route=Route.self_service,
                suggested_priority=TicketPriority.low,
                summary=f"同一决策的可变摘要 {self.calls}",
                reason_codes=[f"free_text_{self.calls}"],
            )

    cases = [
        EvalCase.model_validate(
            {
                "id": f"same-policy-{verification_level}",
                "text": "合成的同一问题",
                "context": {"verification_level": verification_level},
                "invariance": {
                    "group_id": "same-policy",
                    "expectation": "same_output",
                },
                "expected": {
                    "intent": "product_faq",
                    "risk_level": "R0",
                    "route": "self_service",
                },
            }
        )
        for verification_level in ("demo_verified", "unverified")
    ]

    report = evaluate(
        cases, gateway=FreeTextVariantGateway(), runs=2, timer=StepClock()
    )

    assert report["aggregate"]["volatility_rate"] == 0.0
    assert report["identity_invariance"]["rate"] == 1.0
    assert all(case["distinct_output_count"] == 1 for case in report["cases"])


def test_cli_returns_nonzero_when_threshold_fails(tmp_path, capsys):
    cases_path = tmp_path / "cases.json"
    holdout_path = tmp_path / "holdout.json"
    thresholds_path = tmp_path / "thresholds.json"
    output_dir = tmp_path / "reports"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "known-mismatch",
                    "text": "查询订单进度",
                    "expected": {
                        "intent": "warranty",
                        "risk_level": "R1",
                        "route": "agent_assist",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    holdout_path.write_text(
        json.dumps(
            [
                {
                    "id": "holdout-match",
                    "text": "这个商品有哪些颜色",
                    "expected": {
                        "intent": "product_faq",
                        "risk_level": "R0",
                        "route": "self_service",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    default_thresholds = _repo_root() / "data" / "evals" / "triage" / "thresholds.json"
    thresholds_path.write_text(
        default_thresholds.read_text(encoding="utf-8"), encoding="utf-8"
    )

    exit_code = main(
        [
            "--runs",
            "1",
            "--cases",
            str(cases_path),
            "--holdout",
            str(holdout_path),
            "--thresholds",
            str(thresholds_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "triage evaluation: FAIL" in captured.out
    assert "failed: case_pass_rate" in captured.err
    assert len(list(output_dir.glob("*.json"))) == 1
    assert len(list(output_dir.glob("*.md"))) == 1


def test_evaluator_never_opens_database_or_loads_ticket(monkeypatch):
    import app.db

    def forbidden_session():
        raise AssertionError("evaluation must not open a database session")

    monkeypatch.setattr(app.db, "SessionLocal", forbidden_session)
    case = EvalCase.model_validate(
        {
            "id": "offline-only",
            "text": "订单在哪里",
            "expected": {
                "intent": "order_status",
                "risk_level": "R1",
                "route": "agent_assist",
            },
        }
    )

    report = evaluate([case], gateway=FakeLLMGateway(), runs=1, timer=StepClock())

    assert report["aggregate"]["case_pass_rate"] == 1.0
    assert "ticket_id" not in json.dumps(report)


def test_cli_defaults_to_fake_and_three_runs():
    parser = build_parser()
    args = parser.parse_args([])
    assert parser.prog == "eval_triage"
    assert args.provider == "fake"
    assert args.runs == 3
    assert args.expected_model is None
    assert args.cases.name == "cases.json"
    assert args.holdout.name == "holdout_cases.json"
    assert args.thresholds.name == "thresholds.json"


def test_real_provider_cost_is_explicitly_unavailable():
    class NamedFakeGateway(FakeLLMGateway):
        provider_name = "configured-provider"
        model_name = "remote-model"

    case = EvalCase.model_validate(
        {
            "id": "remote-cost",
            "text": "产品支持什么支付方式",
            "expected": {
                "intent": "product_faq",
                "risk_level": "R0",
                "route": "self_service",
            },
        }
    )

    report = evaluate([case], gateway=NamedFakeGateway(), runs=1, timer=StepClock())

    assert report["cost"]["status"] == "unavailable"
    assert report["cost"]["amount"] is None
    assert report["cost"]["tokens"] is None


def test_production_safe_generation_validates_runtime_gateway_output():
    class RuntimeInvalidGateway:
        provider_name = "runtime-invalid"
        model_name = "invalid-model"
        prompt_version = "triage-v1"

        def generate_structured(self, *, text: str) -> TriageResult:
            del text
            return {"unexpected": "shape"}  # type: ignore[return-value]

    result = generate_triage_safe(RuntimeInvalidGateway(), text="synthetic")

    assert result.risk_level is RiskLevel.R3
    assert result.route is Route.human_handoff
    assert result.needs_human_review is True
    assert result.reason_codes == ["schema_validation_failed"]


def test_threshold_profile_cannot_omit_release_metrics(tmp_path):
    path = tmp_path / "thresholds.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "test-thresholds-v1",
                "provider": "fake",
                "metrics": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvalConfigurationError, match="missing required"):
        load_thresholds(path)


def test_public_and_holdout_must_be_nonempty_and_nonoverlapping(tmp_path):
    public = tmp_path / "public.json"
    holdout = tmp_path / "holdout.json"
    payload = [
        {
            "id": "public-1",
            "text": "同一段文本",
            "expected": {
                "intent": "product_faq",
                "risk_level": "R0",
                "route": "self_service",
            },
        }
    ]
    public.write_text(json.dumps(payload), encoding="utf-8")
    holdout.write_text("[]", encoding="utf-8")

    with pytest.raises(EvalConfigurationError, match="must not be empty"):
        load_cases([public, holdout])

    payload[0]["id"] = "holdout-1"
    holdout.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalConfigurationError, match="overlap"):
        load_cases([public, holdout])


def test_missing_r3_or_identity_coverage_cannot_pass_release_gate():
    case = EvalCase.model_validate(
        {
            "id": "coverage-gap",
            "text": "商品有哪些颜色",
            "expected": {
                "intent": "product_faq",
                "risk_level": "R0",
                "route": "self_service",
            },
        }
    )
    report = evaluate([case], gateway=FakeLLMGateway(), runs=1, timer=StepClock())
    thresholds = load_thresholds(
        _repo_root() / "data" / "evals" / "triage" / "thresholds.json"
    )
    results = {item["metric"]: item for item in apply_thresholds(report, thresholds)}

    assert report["aggregate"]["R3_recall"] == 0.0
    assert report["aggregate"]["identity_invariance_rate"] == 0.0
    assert results["R3_recall"]["passed"] is False
    assert results["identity_invariance_rate"]["passed"] is False


def test_normal_invalid_output_fails_raw_schema_and_fallback_gates():
    class InvalidFakeGateway(FakeLLMGateway):
        def generate_structured(self, *, text: str) -> TriageResult:
            del text
            return {"unexpected": "shape"}  # type: ignore[return-value]

    case = EvalCase.model_validate(
        {
            "id": "invalid-normal-output",
            "text": "无法生成的合成探针",
            "expected": {
                "intent": "unknown",
                "risk_level": "R3",
                "route": "human_handoff",
                "suggested_priority": "urgent",
                "needs_human_review": True,
            },
        }
    )
    report = evaluate([case], gateway=InvalidFakeGateway(), runs=1, timer=StepClock())
    thresholds = load_thresholds(
        _repo_root() / "data" / "evals" / "triage" / "thresholds.json"
    )
    results = {item["metric"]: item for item in apply_thresholds(report, thresholds)}

    assert report["aggregate"]["schema_validity_rate"] == 0.0
    assert report["aggregate"]["final_schema_validity_rate"] == 1.0
    assert report["aggregate"]["normal_fallback_rate"] == 1.0
    assert results["schema_validity_rate"]["passed"] is False
    assert results["normal_fallback_rate"]["passed"] is False


def test_safe_fallback_requires_urgent_priority():
    low_priority = TriageResult(
        intent=Intent.unknown,
        risk_level=RiskLevel.R3,
        route=Route.human_handoff,
        suggested_priority=TicketPriority.low,
        summary="合成失败回退",
        needs_human_review=True,
        reason_codes=["schema_validation_failed"],
    )

    assert _is_safe_fallback(low_priority) is False


def test_configured_provider_cannot_silently_resolve_to_fake(
    tmp_path, monkeypatch, capsys
):
    from app.modules.llm_gateway import factory

    monkeypatch.setattr(factory, "get_gateway", lambda: FakeLLMGateway())
    exit_code = main(
        [
            "--provider",
            "configured",
            "--output-dir",
            str(tmp_path / "reports"),
        ]
    )

    assert exit_code == 2
    assert "resolved to Fake" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()


def test_configured_triage_cli_requires_an_exact_model_pin(monkeypatch, capsys):
    from app.evals import triage_cli

    class FixedGateway(FakeLLMGateway):
        provider_name = "fixed-provider"
        model_name = "fixed-model-2026-08"

    monkeypatch.setattr(triage_cli, "_gateway", lambda provider: FixedGateway())

    assert triage_cli.main(["--provider", "configured"]) == 2
    assert "requires --expected-model" in capsys.readouterr().err

    assert (
        triage_cli.main(
            [
                "--provider",
                "configured",
                "--expected-model",
                "different-model",
            ]
        )
        == 2
    )
    assert "model mismatch" in capsys.readouterr().err
