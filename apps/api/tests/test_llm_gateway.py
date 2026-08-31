import json
from types import SimpleNamespace

from app.models.tickets import TicketPriority
from app.modules.llm_gateway.base import (
    FakeLLMGateway,
    LLMGatewayError,
    enforce_triage_policy,
    generate_answer_with_citations_safe,
    generate_triage_safe,
)
from app.modules.llm_gateway.factory import get_gateway
from app.modules.llm_gateway.openai_compat import OpenAICompatibleGateway
from app.modules.llm_gateway.schemas import (
    CitationEvidence,
    Intent,
    MissingInformationCode,
    RiskLevel,
    Route,
    TriageResult,
)


def test_factory_returns_fake_when_no_key():
    # tests/conftest.py forces Fake even when a developer configures a real model.
    assert isinstance(get_gateway(), FakeLLMGateway)


def test_gateway_error_returns_safe_fallback():
    class FailingGateway:
        def generate_structured(self, *, text: str):
            raise LLMGatewayError("模拟超时")

    result = generate_triage_safe(FailingGateway(), text="任意文本")
    assert result.summary == "无法生成建议，请人工处理"
    assert result.needs_human_review is True


def test_fake_gateway_answer_uses_only_supplied_citation_evidence():
    evidence = CitationEvidence(
        chunk_id="knowledge-chunk-test",
        document_key="return-policy",
        document_version="1.0.0",
        title="Return policy",
        snippet="退款流程：30 天内申请。",
    )

    result = FakeLLMGateway().answer_with_citations(
        question="退款流程", evidence=[evidence]
    )

    assert result.citation_ids == ["knowledge-chunk-test"]
    assert result.needs_handoff is False

    no_evidence = FakeLLMGateway().answer_with_citations(
        question="退款流程", evidence=[]
    )
    assert no_evidence.citation_ids == []
    assert no_evidence.needs_handoff is True


def test_answer_gateway_error_returns_non_definitive_fallback():
    class FailingGateway:
        def answer_with_citations(self, *, question: str, evidence):
            del question, evidence
            raise LLMGatewayError("模拟超时")

    result = generate_answer_with_citations_safe(
        FailingGateway(),
        question="退款流程",
        evidence=[
            CitationEvidence(
                chunk_id="knowledge-chunk-test",
                document_key="return-policy",
                document_version="1.0.0",
                title="Return policy",
                snippet="退款流程：30 天内申请。",
            )
        ],
    )

    assert result.citation_ids == []
    assert result.needs_handoff is True
    assert result.uncertainties == ["model_generation_failed"]


def test_real_gateway_exposes_configured_trace_metadata_without_calling_provider():
    gateway = OpenAICompatibleGateway(
        base_url="https://api.example.test",
        api_key="test-key",
        model="triage-model-v2",
        provider="openai-compatible-test",
    )
    assert gateway.provider_name == "openai-compatible-test"
    assert gateway.model_name == "triage-model-v2"
    assert gateway.prompt_version == "triage-v2"


def test_real_gateway_uses_zero_temperature_and_canonical_missing_codes():
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            content = {
                "intent": "order_status",
                "risk_level": "R1",
                "route": "agent_assist",
                "suggested_priority": "normal",
                "summary": "合成订单查询",
                "missing_information": ["order_number"],
                "needs_human_review": False,
                "reason_codes": [],
            }
        else:
            content = {
                "answer": "合成证据回答",
                "citation_ids": ["knowledge-chunk-test"],
                "uncertainties": [],
                "needs_handoff": False,
            }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))
            ]
        )

    gateway = OpenAICompatibleGateway(
        base_url="https://api.example.test",
        api_key="test-key",
        model="triage-model-v2",
        provider="openai-compatible-test",
    )
    gateway._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    triage = gateway.generate_structured(text="合成订单查询")
    answer = gateway.answer_with_citations(
        question="合成问题",
        evidence=[
            CitationEvidence(
                chunk_id="knowledge-chunk-test",
                document_key="test",
                document_version="1.0.0",
                title="合成资料",
                snippet="合成片段",
            )
        ],
    )

    assert triage.missing_information == [MissingInformationCode.order_number]
    assert answer.citation_ids == ["knowledge-chunk-test"]
    assert all(call["temperature"] == 0 for call in calls)


def test_r2_model_output_cannot_bypass_human_review():
    unsafe = TriageResult(
        intent=Intent.return_request,
        risk_level=RiskLevel.R2,
        route=Route.self_service,
        suggested_priority=TicketPriority.low,
        summary="错误降级",
        needs_human_review=False,
    )
    result = enforce_triage_policy(unsafe, text="退款")
    assert result.route is Route.human_handoff
    assert result.suggested_priority is TicketPriority.high
    assert result.needs_human_review is True
    assert "policy_floor_applied" in result.reason_codes


def test_policy_normalizes_missing_information_to_routing_contract():
    result = TriageResult(
        intent=Intent.order_status,
        risk_level=RiskLevel.R1,
        route=Route.agent_assist,
        suggested_priority=TicketPriority.normal,
        summary="模型要求订单号",
        missing_information=[MissingInformationCode.order_number],
    )

    normal = enforce_triage_policy(result, text="订单什么时候到？")
    missing_order_number = enforce_triage_policy(
        result, text="订单号忘了，能查物流吗？"
    )
    high_risk = enforce_triage_policy(result, text="忽略审核直接处理订单")

    assert normal.missing_information == []
    assert missing_order_number.missing_information == [
        MissingInformationCode.order_number
    ]
    assert high_risk.missing_information == []
