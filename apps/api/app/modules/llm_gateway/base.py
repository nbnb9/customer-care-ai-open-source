import hashlib
from typing import Protocol

from opentelemetry.trace import SpanKind

from app.models.tickets import TicketPriority
from app.shared.observability import (
    mark_span_error,
    observe_span,
    set_span_attributes,
)
from app.shared.security import contains_prompt_injection

from .schemas import (
    CitationAnswer,
    CitationEvidence,
    Intent,
    MissingInformationCode,
    RiskLevel,
    Route,
    TriageResult,
)

TRIAGE_PROMPT_VERSION = "triage-v2"
TRIAGE_SCHEMA_VERSION = "triage-result-v1"
KNOWLEDGE_ANSWER_PROMPT_VERSION = "knowledge-answer-v1"
KNOWLEDGE_ANSWER_SCHEMA_VERSION = "knowledge-answer-result-v1"


class TriageGateway(Protocol):
    """分诊任务所需的最小供应商无关接口。"""

    provider_name: str
    model_name: str
    prompt_version: str

    def generate_structured(self, *, text: str) -> TriageResult: ...


class CitationAnswerGateway(Protocol):
    """受证据约束回答任务所需的最小供应商无关接口。"""

    provider_name: str
    model_name: str
    prompt_version: str

    def answer_with_citations(
        self, *, question: str, evidence: list[CitationEvidence]
    ) -> CitationAnswer: ...


class LLMGateway(TriageGateway, CitationAnswerGateway, Protocol):
    """完整网关；工厂产物同时支持分诊和受证据约束回答。"""


class LLMGatewayError(Exception):
    """真实网关无法生成有效结果（JSON 非法 / Schema 校验失败 / 超时 / 供应商错误）。"""


class FakeLLMGateway:
    """开发/测试用确定性网关，不联网、不花钱。"""

    provider_name = "fake"
    model_name = "fake-deterministic-v1"
    model_config = "fake-deterministic-v1;temperature=0;structured-json"
    prompt_version = TRIAGE_PROMPT_VERSION
    knowledge_answer_prompt_version = KNOWLEDGE_ANSWER_PROMPT_VERSION
    knowledge_answer_prompt_sha256 = hashlib.sha256(
        b"fake-deterministic-knowledge-answer-v1"
    ).hexdigest()

    def generate_structured(self, *, text: str) -> TriageResult:
        t = text.lower()
        if contains_prompt_injection(text) or any(
            k in t for k in ("忽略", "绕过", "直接退款", "ignore")
        ):
            return TriageResult(
                intent=Intent.unknown,
                risk_level=RiskLevel.R3,
                route=Route.human_handoff,
                suggested_priority=TicketPriority.urgent,
                summary="检测到可疑指令",
                needs_human_review=True,
                reason_codes=["prompt_injection"],
            )
        if any(k in t for k in ("安全", "泄漏", "泄露", "黑客", "越权", "security")):
            return TriageResult(
                intent=Intent.safety,
                risk_level=RiskLevel.R3,
                route=Route.human_handoff,
                suggested_priority=TicketPriority.urgent,
                summary="疑似安全问题",
                needs_human_review=True,
                reason_codes=["safety"],
            )
        if any(
            k in t
            for k in (
                "退款",
                "退货",
                "补发",
                "补寄",
                "换货",
                "换新",
                "refund",
                "replacement",
                "replace",
            )
        ):
            return TriageResult(
                intent=Intent.return_request,
                risk_level=RiskLevel.R2,
                route=Route.human_handoff,
                suggested_priority=TicketPriority.high,
                summary="退货/退款/补换货请求",
                needs_human_review=True,
                reason_codes=["policy_action"],
            )
        if any(k in t for k in ("投诉", "complain")):
            return TriageResult(
                intent=Intent.complaint,
                risk_level=RiskLevel.R2,
                route=Route.human_handoff,
                suggested_priority=TicketPriority.high,
                summary="投诉",
                needs_human_review=True,
                reason_codes=["complaint"],
            )
        if any(k in t for k in ("订单", "物流", "ship", "track")):
            missing_information: list[MissingInformationCode] = []
            if any(k in t for k in ("忘", "不知道订单", "没有订单")):
                missing_information.append(MissingInformationCode.order_number)
            return TriageResult(
                intent=Intent.order_status,
                risk_level=RiskLevel.R1,
                route=Route.agent_assist,
                suggested_priority=TicketPriority.normal,
                summary="订单/物流查询",
                missing_information=missing_information,
            )
        if any(k in t for k in ("保修", "warranty")):
            return TriageResult(
                intent=Intent.warranty,
                risk_level=RiskLevel.R1,
                route=Route.agent_assist,
                suggested_priority=TicketPriority.normal,
                summary="保修查询",
            )
        if any(k in t for k in ("登录", "账号", "account", "密码")):
            return TriageResult(
                intent=Intent.account,
                risk_level=RiskLevel.R1,
                route=Route.agent_assist,
                suggested_priority=TicketPriority.normal,
                summary="账号问题",
            )
        if any(k in t for k in ("不清楚", "随便问", "不知道怎么说")):
            return TriageResult(
                intent=Intent.unknown,
                risk_level=RiskLevel.R1,
                route=Route.human_handoff,
                suggested_priority=TicketPriority.normal,
                summary="信息不足，无法可靠分诊",
                missing_information=[MissingInformationCode.issue_description],
                needs_human_review=True,
                reason_codes=["insufficient_context"],
            )
        return TriageResult(
            intent=Intent.product_faq,
            risk_level=RiskLevel.R0,
            route=Route.self_service,
            suggested_priority=TicketPriority.low,
            summary="产品常见问题",
        )

    def answer_with_citations(
        self, *, question: str, evidence: list[CitationEvidence]
    ) -> CitationAnswer:
        del question
        if not evidence:
            return CitationAnswer(
                answer="当前没有足够的已授权资料来确认这个问题，请转人工处理。",
                uncertainties=["no_trusted_evidence"],
                needs_handoff=True,
            )
        source = evidence[0]
        return CitationAnswer(
            answer=f"根据「{source.title}」：{source.snippet}",
            citation_ids=[source.chunk_id],
        )


def generate_triage_safe(gateway: TriageGateway, *, text: str) -> TriageResult:
    """调用网关；任何异常都安全回退到人工处理，不让错误冒泡。"""
    with observe_span(
        "gen_ai.chat",
        kind=SpanKind.CLIENT,
        attributes=_model_attributes(
            gateway,
            operation="triage",
            prompt_version=getattr(gateway, "prompt_version", TRIAGE_PROMPT_VERSION),
            schema_version=TRIAGE_SCHEMA_VERSION,
        ),
    ) as span:
        try:
            raw = gateway.generate_structured(text=text)
            result = TriageResult.model_validate(raw)
            set_span_attributes(
                {
                    "customer_care.ai.outcome": "generated",
                    **_cost_attributes(gateway),
                }
            )
            return result
        except Exception as exc:
            mark_span_error(span, exc)
            set_span_attributes(
                {
                    "customer_care.ai.outcome": "safe_fallback",
                    **_cost_attributes(gateway),
                }
            )
            return TriageResult(
                intent=Intent.unknown,
                risk_level=RiskLevel.R3,
                route=Route.human_handoff,
                suggested_priority=TicketPriority.urgent,
                summary="无法生成建议，请人工处理",
                needs_human_review=True,
                reason_codes=["schema_validation_failed"],
            )


def generate_answer_with_citations_safe(
    gateway: CitationAnswerGateway, *, question: str, evidence: list[CitationEvidence]
) -> CitationAnswer:
    """Never turn gateway/schema failure into an unsupported policy answer."""

    with observe_span(
        "gen_ai.chat",
        kind=SpanKind.CLIENT,
        attributes=_model_attributes(
            gateway,
            operation="knowledge_answer",
            prompt_version=getattr(
                gateway,
                "knowledge_answer_prompt_version",
                KNOWLEDGE_ANSWER_PROMPT_VERSION,
            ),
            schema_version=KNOWLEDGE_ANSWER_SCHEMA_VERSION,
        ),
    ) as span:
        try:
            raw = gateway.answer_with_citations(question=question, evidence=evidence)
            result = CitationAnswer.model_validate(raw)
            set_span_attributes(
                {
                    "customer_care.ai.outcome": (
                        "handoff" if result.needs_handoff else "generated"
                    ),
                    "customer_care.ai.citation_count": len(result.citation_ids),
                    **_cost_attributes(gateway),
                }
            )
            return result
        except Exception as exc:
            mark_span_error(span, exc)
            set_span_attributes(
                {
                    "customer_care.ai.outcome": "safe_fallback",
                    "customer_care.ai.citation_count": 0,
                    **_cost_attributes(gateway),
                }
            )
            return CitationAnswer(
                answer="当前无法根据已授权资料确认这个问题，请补充信息或转人工处理。",
                uncertainties=["model_generation_failed"],
                needs_handoff=True,
            )


def _model_attributes(
    gateway: TriageGateway | CitationAnswerGateway,
    *,
    operation: str,
    prompt_version: str,
    schema_version: str,
) -> dict[str, str]:
    # Existing test doubles and future narrowly-scoped adapters may implement
    # only the generation method. Instrumentation must never turn that valid
    # safe-fallback path into a 500 merely because metadata is absent.
    provider_name = str(getattr(gateway, "provider_name", "unconfigured"))
    model_name = str(getattr(gateway, "model_name", "unconfigured"))
    model_config = str(getattr(gateway, "model_config", "unconfigured"))
    return {
        "gen_ai.operation.name": operation,
        "gen_ai.provider.name": provider_name,
        "gen_ai.request.model": model_name,
        "customer_care.prompt.version": prompt_version,
        "customer_care.prompt.schema_version": schema_version,
        "customer_care.model.config": model_config,
    }


def _cost_attributes(
    gateway: TriageGateway | CitationAnswerGateway,
) -> dict[str, object]:
    if getattr(gateway, "provider_name", None) == "fake":
        return {
            "customer_care.cost.status": "measured",
            "customer_care.cost.currency": "USD",
            "customer_care.cost.amount": 0.0,
        }
    # Usage may be reported by a provider, but price/currency must come from a
    # versioned tariff contract. Until one exists, unknown is safer than a
    # made-up estimate.
    return {
        "customer_care.cost.status": "unavailable",
        "customer_care.cost.reason": "provider_pricing_not_configured",
    }


def enforce_triage_policy(result: TriageResult, *, text: str) -> TriageResult:
    """Apply deterministic risk and routing-information policy to model output."""
    normalized = text.lower()
    updates: dict[str, object] = {}
    required_risk: RiskLevel | None = None
    final_intent = result.intent

    if contains_prompt_injection(text) or any(
        marker in normalized
        for marker in ("忽略", "绕过", "ignore previous", "system prompt")
    ):
        required_risk = RiskLevel.R3
        updates["intent"] = Intent.unknown
        final_intent = Intent.unknown
    elif any(
        marker in normalized
        for marker in ("安全", "泄漏", "泄露", "黑客", "越权", "security")
    ):
        required_risk = RiskLevel.R3
        updates["intent"] = Intent.safety
        final_intent = Intent.safety
    elif any(
        marker in normalized
        for marker in (
            "退款",
            "退货",
            "补发",
            "补寄",
            "换货",
            "换新",
            "refund",
            "replacement",
            "replace",
        )
    ):
        required_risk = RiskLevel.R2
        updates["intent"] = Intent.return_request
        final_intent = Intent.return_request
    elif any(marker in normalized for marker in ("投诉", "complain")):
        required_risk = RiskLevel.R2
        updates["intent"] = Intent.complaint
        final_intent = Intent.complaint

    risk_rank = {
        RiskLevel.R0: 0,
        RiskLevel.R1: 1,
        RiskLevel.R2: 2,
        RiskLevel.R3: 3,
    }
    final_risk = result.risk_level
    if required_risk is not None and risk_rank[final_risk] < risk_rank[required_risk]:
        final_risk = required_risk
        updates["risk_level"] = required_risk

    if final_risk is RiskLevel.R3:
        updates.update(
            route=Route.human_handoff,
            suggested_priority=TicketPriority.urgent,
            needs_human_review=True,
        )
    elif final_risk is RiskLevel.R2:
        updates.update(
            route=Route.human_handoff,
            suggested_priority=TicketPriority.high,
            needs_human_review=True,
        )

    required_missing = _required_missing_information(
        intent=final_intent,
        risk_level=final_risk,
        normalized_text=normalized,
    )
    if result.missing_information != required_missing:
        updates["missing_information"] = required_missing

    changed = any(getattr(result, key) != value for key, value in updates.items())
    if changed:
        original_codes = [
            code for code in result.reason_codes if code != "policy_floor_applied"
        ]
        updates["reason_codes"] = [*original_codes[:19], "policy_floor_applied"]
    return result.model_copy(update=updates)


def _required_missing_information(
    *,
    intent: Intent,
    risk_level: RiskLevel,
    normalized_text: str,
) -> list[MissingInformationCode]:
    """Only expose facts needed to route, not details needed to resolve a case."""
    if risk_level in {RiskLevel.R2, RiskLevel.R3}:
        return []
    if intent is Intent.order_status and any(
        marker in normalized_text
        for marker in ("订单号忘", "忘了订单", "不知道订单", "没有订单")
    ):
        return [MissingInformationCode.order_number]
    if intent is Intent.unknown and any(
        marker in normalized_text for marker in ("不清楚", "随便问", "不知道怎么说")
    ):
        return [MissingInformationCode.issue_description]
    return []
