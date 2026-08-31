"""Evidence-constrained knowledge answers.

This module treats retrieval, citation validity and escalation as server-side
controls.  A model can draft a response, but it cannot widen the evidence set,
mint a citation, or turn a no-evidence result into a policy answer.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Final
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from app.models.identity import UserRole
from app.modules.identity.principal import CurrentPrincipal
from app.modules.llm_gateway.base import (
    CitationAnswerGateway,
    generate_answer_with_citations_safe,
)
from app.modules.llm_gateway.factory import get_gateway
from app.modules.llm_gateway.schemas import CitationEvidence
from app.shared.observability import set_span_attributes
from app.shared.redaction import sanitize_for_model
from app.shared.security import contains_prompt_injection

from . import retrieval
from .schemas import (
    KnowledgeAnswerRequest,
    KnowledgeAnswerResponse,
    KnowledgeCitation,
    KnowledgeSearchResult,
)

MAX_MODEL_QUESTION_CHARS: Final = 500
MAX_MODEL_EVIDENCE_CHARS: Final = 500
SAFE_FALLBACK_ANSWER: Final = (
    "目前无法根据可验证的已授权资料确认这个问题。"
    "请补充产品、地区或具体情况，或转交人工客服处理。"
)


def answer_knowledge(
    db: Session,
    principal: CurrentPrincipal,
    data: KnowledgeAnswerRequest,
    *,
    gateway: CitationAnswerGateway | None = None,
    audiences: tuple[str, ...] | None = None,
) -> KnowledgeAnswerResponse:
    """Return a cited answer only when the server has trusted lexical evidence."""

    # Treat every caller's question as untrusted, including a staff member's.
    # This avoids forwarding an explicit instruction override to a model even
    # when authorized evidence happens to match a query term.
    if contains_prompt_injection(data.query):
        set_span_attributes(
            {"customer_care.safety.outcome": "prompt_injection_handoff"}
        )
        return safe_handoff_response(principal, "prompt_injection")

    retrieval_response = retrieval.search_knowledge(
        db, principal, data, audiences=audiences
    )
    trusted_lexical_results = [
        result
        for result in retrieval_response.results
        if result.matched_lexically and not result.untrusted_content
    ]
    if not trusted_lexical_results:
        set_span_attributes({"customer_care.ai.outcome": "no_trusted_evidence"})
        return safe_handoff_response(principal, "no_trusted_lexical_evidence")
    if _has_conflicting_versions(trusted_lexical_results):
        set_span_attributes(
            {"customer_care.ai.outcome": "conflicting_knowledge_versions"}
        )
        return safe_handoff_response(principal, "conflicting_document_versions")

    evidence = [_to_model_evidence(result) for result in trusted_lexical_results]
    draft = generate_answer_with_citations_safe(
        gateway if gateway is not None else get_gateway(),
        question=sanitize_for_model(data.query, max_chars=MAX_MODEL_QUESTION_CHARS),
        evidence=evidence,
    )
    evidence_by_id = {result.chunk_id: result for result in trusted_lexical_results}
    citation_ids = draft.citation_ids

    # A handoff-shaped model response is never forwarded verbatim: it may still
    # contain an unsupported conclusion. The server returns a controlled,
    # non-definitive response instead.
    if draft.needs_handoff or draft.uncertainties:
        set_span_attributes({"customer_care.ai.outcome": "model_handoff"})
        return safe_handoff_response(
            principal,
            (
                "model_requested_handoff"
                if draft.needs_handoff
                else "model_reported_uncertainty"
            ),
        )
    if not citation_ids:
        set_span_attributes({"customer_care.ai.outcome": "missing_citations"})
        return safe_handoff_response(principal, "missing_citations")
    if len(citation_ids) != len(set(citation_ids)):
        set_span_attributes({"customer_care.ai.outcome": "duplicate_citations"})
        return safe_handoff_response(principal, "duplicate_citations")
    if any(citation_id not in evidence_by_id for citation_id in citation_ids):
        set_span_attributes({"customer_care.ai.outcome": "invalid_citations"})
        return safe_handoff_response(principal, "invalid_citation_id")

    set_span_attributes(
        {
            "customer_care.ai.outcome": "cited_answer",
            "customer_care.ai.citation_count": len(citation_ids),
        }
    )
    return KnowledgeAnswerResponse(
        answer=draft.answer,
        citations=[
            _to_citation(evidence_by_id[citation_id], data)
            for citation_id in citation_ids
        ],
        needs_handoff=False,
    )


def _to_model_evidence(result: KnowledgeSearchResult) -> CitationEvidence:
    return CitationEvidence(
        chunk_id=result.chunk_id,
        document_key=sanitize_for_model(result.document_key, max_chars=255),
        document_version=sanitize_for_model(result.document_version, max_chars=32),
        title=sanitize_for_model(result.title, max_chars=255),
        snippet=sanitize_for_model(result.snippet, max_chars=MAX_MODEL_EVIDENCE_CHARS),
    )


def _to_citation(
    result: KnowledgeSearchResult, data: KnowledgeAnswerRequest
) -> KnowledgeCitation:
    locator_query = urlencode(
        {
            "product": data.product,
            "region": data.region,
            "language": data.language,
        }
    )
    source_locator = (
        f"/v1/knowledge/chunks/{quote(result.chunk_id, safe='')}?{locator_query}"
    )
    return KnowledgeCitation(
        chunk_id=result.chunk_id,
        document_key=result.document_key,
        document_version=result.document_version,
        title=result.title,
        excerpt=result.snippet,
        product=result.product,
        region=result.region,
        language=result.language,
        effective_at=result.effective_at,
        expires_at=result.expires_at,
        source_locator=source_locator,
    )


def _has_conflicting_versions(results: list[KnowledgeSearchResult]) -> bool:
    versions_by_document_key: dict[str, set[str]] = defaultdict(set)
    for result in results:
        versions_by_document_key[result.document_key].add(result.document_version)
    return any(len(versions) > 1 for versions in versions_by_document_key.values())


def safe_handoff_response(
    principal: CurrentPrincipal, uncertainty_code: str
) -> KnowledgeAnswerResponse:
    return KnowledgeAnswerResponse(
        answer=SAFE_FALLBACK_ANSWER,
        needs_handoff=True,
        uncertainties=(
            [uncertainty_code]
            if principal.role in (UserRole.agent, UserRole.supervisor)
            else []
        ),
    )
