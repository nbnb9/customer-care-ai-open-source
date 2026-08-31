"""Database-backed, evidence-layered evaluation for the knowledge answer flow.

The evaluator deliberately exercises the production import worker, ACL-first
retrieval, citation construction, answer guardrails, and withdrawal service.
It is intended for an isolated synthetic evaluation database only; it must not
be pointed at a shared development or production database.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.identity import User, UserRole
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocumentStatus,
    KnowledgeImport,
    KnowledgeImportStatus,
)
from app.modules.identity.principal import CurrentPrincipal
from app.modules.knowledge import answer, retrieval, service
from app.modules.knowledge.schemas import (
    KnowledgeAnswerRequest,
    KnowledgeDocumentWithdrawalCreate,
    KnowledgeImportCreate,
    KnowledgeSearchRequest,
)
from app.modules.knowledge.worker import process_pending_imports
from app.modules.llm_gateway.base import (
    KNOWLEDGE_ANSWER_PROMPT_VERSION,
    KNOWLEDGE_ANSWER_SCHEMA_VERSION,
    CitationAnswerGateway,
)

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
Version = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\d+\.\d+\.\d+$")
]
RAG_EVALUATOR_VERSION = "rag-evaluator-v1"

MINIMUM_METRICS = frozenset(
    {
        "case_pass_rate",
        "recall_at_k",
        "retrieval_allowed_only_rate",
        "citation_correctness_rate",
        "answer_key_conclusion_rate",
        "handoff_correctness_rate",
    }
)
MAXIMUM_METRICS = frozenset(
    {
        "permission_leak_rate",
        "expired_hit_rate",
        "untrusted_answer_evidence_rate",
    }
)
SUPPORTED_THRESHOLD_METRICS = MINIMUM_METRICS | MAXIMUM_METRICS


class RagEvalConfigurationError(ValueError):
    """Raised when the versioned corpus, cases, or thresholds are unusable."""


class RagPrincipal(BaseModel):
    """Synthetic identity recorded in each case, not a caller-controlled ACL."""

    model_config = ConfigDict(extra="forbid")

    user_id: ShortText
    tenant_id: ShortText
    role: Literal["customer", "agent", "supervisor"]
    verification_level: ShortText = "demo_verified"
    customer_id: ShortText | None = None

    @model_validator(mode="after")
    def validate_customer_binding(self) -> RagPrincipal:
        if self.role == "customer" and self.customer_id is None:
            raise ValueError("customer principal requires customer_id")
        if self.role != "customer" and self.customer_id is not None:
            raise ValueError("only customer principal may include customer_id")
        return self

    def as_current_principal(self) -> CurrentPrincipal:
        return CurrentPrincipal(
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            role=UserRole(self.role),
            verification_level=self.verification_level,
            customer_id=self.customer_id,
        )


class RagScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: ShortText
    region: ShortText
    language: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
    ]


class RagExpected(BaseModel):
    """Expected behavior split between retrieval, citations, and final answer."""

    model_config = ConfigDict(extra="forbid")

    expected_document_key: ShortText | None = None
    expected_document_version: Version | None = None
    forbidden_document_keys: list[ShortText] = Field(
        default_factory=list, max_length=50
    )
    expired_document_keys: list[ShortText] = Field(default_factory=list, max_length=50)
    required_answer_terms: list[ShortText] = Field(default_factory=list, max_length=20)
    forbidden_answer_terms: list[ShortText] = Field(default_factory=list, max_length=20)
    should_handoff: bool

    @model_validator(mode="after")
    def validate_expected_contract(self) -> RagExpected:
        if (self.expected_document_key is None) != (
            self.expected_document_version is None
        ):
            raise ValueError(
                "expected_document_key and expected_document_version must appear "
                "together"
            )
        _require_unique(self.forbidden_document_keys, "forbidden_document_keys")
        _require_unique(self.expired_document_keys, "expired_document_keys")
        _require_unique(self.required_answer_terms, "required_answer_terms")
        _require_unique(self.forbidden_answer_terms, "forbidden_answer_terms")
        if not set(self.expired_document_keys).issubset(self.forbidden_document_keys):
            raise ValueError("expired_document_keys must also be forbidden")
        if self.should_handoff:
            if self.required_answer_terms:
                raise ValueError(
                    "handoff case must not require a definitive answer term"
                )
        elif (
            self.expected_document_key is None
            or self.expected_document_version is None
            or not self.required_answer_terms
        ):
            raise ValueError(
                "non-handoff case needs expected document/version and answer terms"
            )
        return self


class RagEvalCase(BaseModel):
    """A versioned synthetic RAG evaluation case with explicit ACL expectations."""

    model_config = ConfigDict(extra="forbid")

    id: ShortText
    principal: RagPrincipal
    scope: RagScope
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]
    allowed_document_keys: list[ShortText] = Field(default_factory=list, max_length=50)
    expected: RagExpected
    split: Literal["public", "holdout"] | None = None
    category: ShortText | None = None
    rationale: (
        Annotated[str, StringConstraints(min_length=1, max_length=2_000)] | None
    ) = None
    tags: list[ShortText] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_acl_contract(self) -> RagEvalCase:
        _require_unique(self.allowed_document_keys, "allowed_document_keys")
        _require_unique(self.tags, "tags")
        allowed = set(self.allowed_document_keys)
        forbidden = set(self.expected.forbidden_document_keys)
        overlap = sorted(allowed & forbidden)
        if overlap:
            raise ValueError(f"allowed and forbidden documents overlap: {overlap}")
        if (
            self.expected.expected_document_key is not None
            and self.expected.expected_document_key not in allowed
        ):
            raise ValueError(
                "expected document must be listed in allowed_document_keys"
            )
        return self


class RagCorpusDocument(BaseModel):
    """Synthetic document imported through the production knowledge worker."""

    model_config = ConfigDict(extra="forbid")

    id: ShortText
    operator_user_id: ShortText
    tenant_id: ShortText
    filename: ShortText
    title: ShortText
    content: Annotated[str, StringConstraints(min_length=1, max_length=1_000_000)]
    document_key: ShortText
    owner: ShortText
    audience: Literal["public", "customer", "agent", "supervisor", "internal"]
    product: ShortText
    region: ShortText
    language: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=32),
    ]
    version: Version
    effective_at: datetime
    expires_at: datetime | None = None
    status: Literal["draft", "review", "published", "expired", "withdrawn"]
    untrusted_content: bool = False

    @field_validator("effective_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("corpus timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_expiry(self) -> RagCorpusDocument:
        if self.expires_at is not None and self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        return self


class RagCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rag-eval-corpus-v1"]
    source: Literal["synthetic"]
    documents: list[RagCorpusDocument] = Field(min_length=1)


class ThresholdBound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None


class RagThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rag-eval-thresholds-v1"]
    provider: ShortText
    metrics: dict[str, float | ThresholdBound]


def load_corpus(path: Path) -> RagCorpus:
    try:
        corpus = RagCorpus.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RagEvalConfigurationError(f"invalid RAG corpus {path}: {exc}") from exc
    document_ids = [item.id for item in corpus.documents]
    if len(document_ids) != len(set(document_ids)):
        raise RagEvalConfigurationError("corpus document ids must not duplicate")
    scopes = [
        (
            item.tenant_id,
            item.document_key,
            item.product,
            item.region,
            item.language,
            item.version,
        )
        for item in corpus.documents
    ]
    if len(scopes) != len(set(scopes)):
        raise RagEvalConfigurationError("duplicate corpus document version scope")
    return corpus


def load_cases(paths: Sequence[Path]) -> list[RagEvalCase]:
    adapter = TypeAdapter(list[RagEvalCase])
    cases: list[RagEvalCase] = []
    seen_ids: set[str] = set()
    seen_signatures: set[tuple[str, str, str, str, str, str]] = set()
    for source_index, path in enumerate(paths):
        try:
            loaded = adapter.validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RagEvalConfigurationError(
                f"invalid RAG case file {path}: {exc}"
            ) from exc
        if not loaded:
            raise RagEvalConfigurationError(f"RAG case file {path} must not be empty")
        for case in loaded:
            if case.id in seen_ids:
                raise RagEvalConfigurationError(f"duplicate RAG case id: {case.id}")
            seen_ids.add(case.id)
            signature = (
                case.principal.user_id,
                case.principal.tenant_id,
                case.scope.product,
                case.scope.region,
                case.scope.language,
                case.question.strip(),
            )
            if signature in seen_signatures:
                raise RagEvalConfigurationError(f"duplicate RAG case input: {case.id}")
            seen_signatures.add(signature)
            inferred_split = "public" if source_index == 0 else "holdout"
            cases.append(
                case
                if case.split is not None
                else case.model_copy(update={"split": inferred_split})
            )
    if not cases:
        raise RagEvalConfigurationError("RAG evaluation needs at least one case")
    split_counts = {
        split: sum(case.split == split for case in cases)
        for split in ("public", "holdout")
    }
    if not all(split_counts.values()):
        raise RagEvalConfigurationError(
            "RAG evaluation needs non-empty public and holdout splits"
        )
    return sorted(cases, key=lambda item: item.id)


def load_thresholds(path: Path) -> RagThresholdConfig:
    try:
        config = RagThresholdConfig.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RagEvalConfigurationError(
            f"invalid RAG thresholds file {path}: {exc}"
        ) from exc
    unknown = sorted(set(config.metrics) - SUPPORTED_THRESHOLD_METRICS)
    if unknown:
        raise RagEvalConfigurationError(f"unsupported RAG threshold metrics: {unknown}")
    missing = sorted(SUPPORTED_THRESHOLD_METRICS - set(config.metrics))
    if missing:
        raise RagEvalConfigurationError(f"missing required RAG metrics: {missing}")
    for metric, value in config.metrics.items():
        _threshold_rule(metric, value)
    return config


def install_corpus(db: Session, corpus: RagCorpus) -> dict[str, Any]:
    """Import a corpus into an otherwise clean synthetic evaluation database."""

    existing = db.scalar(select(KnowledgeImport.id).limit(1))
    if existing is not None:
        raise RagEvalConfigurationError(
            "RAG evaluation database already contains knowledge imports; reset it first"
        )

    queued_ids: list[str] = []
    for document in corpus.documents:
        _validate_operator(db, document)
        item, deduplicated = service.create_import(
            db,
            CurrentPrincipal(
                user_id=document.operator_user_id,
                tenant_id=document.tenant_id,
                role=UserRole.knowledge_operator,
                verification_level="rag_eval",
            ),
            KnowledgeImportCreate(
                filename=document.filename,
                title=document.title,
                content=document.content,
                document_key=document.document_key,
                owner=document.owner,
                audience=document.audience,
                product=document.product,
                region=document.region,
                language=document.language,
                version=document.version,
                effective_at=document.effective_at,
                expires_at=document.expires_at,
                requested_status=document.status,
                untrusted_content=document.untrusted_content,
            ),
        )
        if deduplicated:
            raise RagEvalConfigurationError(
                f"evaluation corpus import unexpectedly deduplicated: {document.id}"
            )
        queued_ids.append(item.id)

    processed = process_pending_imports(db, limit=len(queued_ids))
    processed_by_id = {item.id: item for item in processed}
    missing = sorted(set(queued_ids) - set(processed_by_id))
    if missing:
        raise RagEvalConfigurationError(
            f"knowledge worker did not process RAG corpus imports: {missing}"
        )
    failures = [
        item.id
        for item in processed_by_id.values()
        if item.status is not KnowledgeImportStatus.published
    ]
    if failures:
        raise RagEvalConfigurationError(
            f"knowledge worker failed RAG corpus imports: {sorted(failures)}"
        )
    return {
        "schema_version": corpus.schema_version,
        "source": corpus.source,
        "document_count": len(corpus.documents),
        "corpus_fingerprint_sha256": _sha256_json(corpus.model_dump(mode="json")),
    }


def evaluate(
    db: Session,
    cases: Sequence[RagEvalCase],
    *,
    gateway: CitationAnswerGateway,
    limit: int = 5,
) -> dict[str, Any]:
    """Evaluate production retrieval, citation, and answer layers separately."""

    if not cases:
        raise RagEvalConfigurationError("RAG evaluation needs at least one case")
    if not 1 <= limit <= 20:
        raise RagEvalConfigurationError("RAG evaluation limit must be between 1 and 20")

    reports = [
        _evaluate_case(db, case, gateway=gateway, limit=limit)
        for case in sorted(cases, key=lambda item: item.id)
    ]
    return {
        "report_schema_version": "rag-eval-report-v1",
        "evaluation_contract": {
            "pipeline": [
                "knowledge import worker",
                "ACL-first retrieval",
                "server-built citation validation",
                "evidence-constrained answer",
            ],
            "retrieval_metric_definition": (
                "Recall@k is the fraction of cases with an expected document/version "
                "whose exact version appears in the first k ACL-filtered results."
            ),
            "permission_metric_definition": (
                "permission_leak_rate is the fraction of cases with forbidden "
                "documents that return at least one forbidden document key."
            ),
            "expired_metric_definition": (
                "expired_hit_rate is the fraction of cases with explicitly expired "
                "documents that return one of those keys."
            ),
            "citation_metric_definition": (
                "Citation correctness requires a current retrieved citation, the "
                "expected document/version, no forbidden citation, and lexical "
                "support for each annotated key conclusion in cited excerpts."
            ),
            "handoff_metric_definition": (
                "Handoff correctness requires the expected handoff decision; a "
                "handoff must carry no citation and no forbidden answer term."
            ),
            "evidence_boundaries": [
                "Synthetic cases verify this repository's deterministic corpus and "
                "ACL behavior; they do not establish production retrieval recall.",
                "Lexical term support is a regression check, not semantic entailment "
                "or legal/policy approval of a model answer.",
                "Configured-model results remain dependent on the recorded provider, "
                "model, prompt, corpus, and threshold profile.",
                "A passing offline evaluation does not validate production identity "
                "providers, caches, human operations, or incident response timing.",
            ],
        },
        "versions": _gateway_versions(gateway),
        "inputs": _input_metadata(cases),
        "limit": limit,
        "aggregate": _aggregate(reports),
        "slices": _slices(reports),
        "cases": reports,
    }


def run_evaluation(
    db: Session,
    corpus: RagCorpus,
    cases: Sequence[RagEvalCase],
    *,
    gateway: CitationAnswerGateway,
    limit: int = 5,
    run_rollback_drill: bool = True,
) -> dict[str, Any]:
    """Install corpus, execute the layered suite, then optionally drill rollback."""

    _validate_case_document_references(cases, corpus)
    corpus_metadata = install_corpus(db, corpus)
    report = evaluate(db, cases, gateway=gateway, limit=limit)
    report["corpus"] = corpus_metadata
    if run_rollback_drill:
        rollback = run_rollback_drill_case(db, gateway=gateway)
        regression = evaluate(db, cases, gateway=gateway, limit=limit)
        regression_passed = all(bool(case["passed"]) for case in regression["cases"])
        rollback["post_withdrawal_regression"] = {
            "case_passed": regression_passed,
            "aggregate": regression["aggregate"],
            "case_fingerprint_sha256": regression["inputs"]["case_fingerprint_sha256"],
        }
        rollback["passed"] = bool(rollback["passed"] and regression_passed)
        report["rollback_drill"] = rollback
    return report


def run_rollback_drill_case(
    db: Session, *, gateway: CitationAnswerGateway
) -> dict[str, Any]:
    """Publish a deliberately wrong policy, detect it, withdraw it, and verify it.

    The drill uses a product scope reserved for this exercise so it cannot alter
    the normal corpus cases.  It does not claim a cache clear or vector rebuild
    when no such cache/index mutation exists in the application.
    """

    operator = CurrentPrincipal(
        user_id="user-nw-knowledge-operator-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.knowledge_operator,
        verification_level="rag_eval",
    )
    document_key = "rag-eval-incorrect-return-policy"
    version = "1.0.0"
    scope = RagScope(
        product="rag-eval-drill",
        region="cn-north",
        language="zh-CN",
    )
    _validate_operator_user(db, operator)
    item, deduplicated = service.create_import(
        db,
        operator,
        KnowledgeImportCreate(
            filename="rag-eval-incorrect-return-policy-1.0.0.md",
            title="错误退款政策演练",
            content="错误政策演练：客户可在 90 天内申请退款。",
            document_key=document_key,
            owner="rag-eval-operations",
            audience="public",
            product=scope.product,
            region=scope.region,
            language=scope.language,
            version=version,
            effective_at=datetime(2025, 1, 1, tzinfo=UTC),
            requested_status="published",
        ),
    )
    if deduplicated:
        raise RagEvalConfigurationError(
            "rollback drill policy unexpectedly deduplicated"
        )
    processed = process_pending_imports(db, limit=1)
    if len(processed) != 1 or processed[0].id != item.id:
        raise RagEvalConfigurationError("rollback drill policy was not processed")
    if processed[0].status is not KnowledgeImportStatus.published:
        raise RagEvalConfigurationError("rollback drill policy failed to publish")

    reader = CurrentPrincipal(
        user_id="user-nw-customer-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
        customer_id="customer-nw-01",
    )
    request = KnowledgeAnswerRequest(
        query="错误政策演练",
        product=scope.product,
        region=scope.region,
        language=scope.language,
        limit=5,
    )
    before = answer.answer_knowledge(db, reader, request, gateway=gateway)
    expected_correct_term = "30 天"
    incorrect_term = "90 天"
    pre_release_gate_passed = (
        not before.needs_handoff and expected_correct_term in before.answer
    )
    detection_triggered = not pre_release_gate_passed
    incorrect_policy_answered = (
        not before.needs_handoff and incorrect_term in before.answer
    )

    document, audit_event, already_withdrawn = service.withdraw_document(
        db,
        operator,
        KnowledgeDocumentWithdrawalCreate(
            document_key=document_key,
            version=version,
            product=scope.product,
            region=scope.region,
            language=scope.language,
            reason="RAG rollback drill: intentionally incorrect 90-day policy",
        ),
    )
    if already_withdrawn or audit_event is None:
        raise RagEvalConfigurationError(
            "rollback drill did not create withdrawal audit"
        )

    search_request = KnowledgeSearchRequest(
        query=request.query,
        product=scope.product,
        region=scope.region,
        language=scope.language,
        limit=5,
    )
    after_search = retrieval.search_knowledge(db, reader, search_request)
    after = answer.answer_knowledge(db, reader, request, gateway=gateway)
    chunks = db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    ).all()
    audit_exists = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.id == audit_event.id,
            AuditEvent.action == "knowledge.document_withdrawn",
            AuditEvent.resource_id == document.id,
        )
    )
    post_withdrawal_passed = (
        document.status is KnowledgeDocumentStatus.withdrawn
        and bool(chunks)
        and all(chunk.status is KnowledgeDocumentStatus.withdrawn for chunk in chunks)
        and not after_search.results
        and after.needs_handoff
        and not after.citations
        and incorrect_term not in after.answer
        and audit_exists == audit_event.id
    )
    return {
        "drill_schema_version": "knowledge-withdrawal-drill-v1",
        "incorrect_policy": {
            "document_key": document_key,
            "version": version,
            "scope": scope.model_dump(mode="json"),
            "incorrect_key_conclusion": incorrect_term,
            "expected_key_conclusion": expected_correct_term,
        },
        "pre_withdrawal": {
            "release_gate_passed": pre_release_gate_passed,
            "detection_triggered": detection_triggered,
            "incorrect_policy_answered": incorrect_policy_answered,
            "needs_handoff": before.needs_handoff,
            "citation_count": len(before.citations),
            "answer_contains_incorrect_term": incorrect_term in before.answer,
        },
        "withdrawal_record": {
            "document_status": document.status.value,
            "audit_event_id": audit_event.id,
            "audit_action": audit_event.action,
            "chunk_count": len(chunks),
            "all_chunk_statuses": sorted({chunk.status.value for chunk in chunks}),
        },
        "cache_and_index_action": {
            "application_answer_cache": "not_implemented; no cache key to purge",
            "vector_rebuild": "not_required; status predicates exclude withdrawn rows",
            "verification": "reran ACL retrieval and cited-answer flow after commit",
        },
        "post_withdrawal": {
            "search_result_count": len(after_search.results),
            "needs_handoff": after.needs_handoff,
            "citation_count": len(after.citations),
            "answer_contains_incorrect_term": incorrect_term in after.answer,
            "passed": post_withdrawal_passed,
        },
        "passed": detection_triggered and post_withdrawal_passed,
    }


def apply_thresholds(
    report: dict[str, Any], config: RagThresholdConfig
) -> list[dict[str, Any]]:
    effective_provider = str(report["versions"]["provider"])
    if config.provider != effective_provider:
        raise RagEvalConfigurationError(
            "RAG threshold provider mismatch: "
            f"profile={config.provider}, effective={effective_provider}"
        )
    aggregate = report["aggregate"]
    results: list[dict[str, Any]] = []
    for metric in sorted(config.metrics):
        comparator, target = _threshold_rule(metric, config.metrics[metric])
        actual = float(aggregate[metric])
        passed = actual >= target if comparator == "min" else actual <= target
        results.append(
            {
                "metric": metric,
                "comparator": comparator,
                "target": target,
                "actual": actual,
                "passed": passed,
            }
        )
    report["thresholds"] = {
        "schema_version": config.schema_version,
        "config_fingerprint_sha256": _sha256_json(config.model_dump(mode="json")),
        "passed": all(item["passed"] for item in results),
        "results": results,
    }
    return results


def write_reports(report: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Write JSON and concise Markdown reports with reproducible input hashes."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    )
    report_fingerprint = hashlib.sha256(json_text.encode("utf-8")).hexdigest()
    versions = report["versions"]
    corpus = report.get("corpus", {})
    thresholds = report.get("thresholds", {})
    stem = "rag-eval-{}-{}-{}-{}-{}-corpus-{}-cases-{}-thresholds-{}-report-{}".format(
        *(
            _slug(str(versions[key]))
            for key in ("provider", "model", "prompt", "schema", "evaluator")
        ),
        str(corpus.get("corpus_fingerprint_sha256", "uninstalled"))[:12],
        str(report["inputs"]["case_fingerprint_sha256"])[:12],
        str(thresholds.get("config_fingerprint_sha256", "unthresholded"))[:12],
        report_fingerprint[:16],
    )
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(json_text + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _evaluate_case(
    db: Session,
    case: RagEvalCase,
    *,
    gateway: CitationAnswerGateway,
    limit: int,
) -> dict[str, Any]:
    principal = case.principal.as_current_principal()
    search_request = KnowledgeSearchRequest(
        query=case.question,
        product=case.scope.product,
        region=case.scope.region,
        language=case.scope.language,
        limit=limit,
    )
    search_response = retrieval.search_knowledge(db, principal, search_request)
    answer_response = answer.answer_knowledge(
        db,
        principal,
        KnowledgeAnswerRequest(**search_request.model_dump()),
        gateway=gateway,
    )
    retrieved = search_response.results
    result_by_id = {item.chunk_id: item for item in retrieved}
    retrieved_keys = {item.document_key for item in retrieved}
    retrieved_versions = {
        (item.document_key, item.document_version) for item in retrieved
    }
    expected = case.expected
    expected_version = (
        (expected.expected_document_key, expected.expected_document_version)
        if expected.expected_document_key is not None
        and expected.expected_document_version is not None
        else None
    )
    unexpected_documents = sorted(retrieved_keys - set(case.allowed_document_keys))
    forbidden_documents = sorted(retrieved_keys & set(expected.forbidden_document_keys))
    expired_documents = sorted(retrieved_keys & set(expected.expired_document_keys))
    citations = answer_response.citations
    citation_chunk_ids = [item.chunk_id for item in citations]
    citation_document_keys = {item.document_key for item in citations}
    citation_versions = {
        (item.document_key, item.document_version) for item in citations
    }
    citation_text = "\n".join(item.excerpt for item in citations)
    citation_in_current_retrieval = all(
        chunk_id in result_by_id for chunk_id in citation_chunk_ids
    )
    citation_allowed = citation_document_keys.issubset(case.allowed_document_keys)
    citation_forbidden_absent = not (
        citation_document_keys & set(expected.forbidden_document_keys)
    )
    citation_terms_supported = all(
        _contains(citation_text, term) for term in expected.required_answer_terms
    )
    if expected.should_handoff:
        citation_correct: bool | None = not citations
    else:
        citation_correct = (
            bool(citations)
            and expected_version in citation_versions
            and citation_in_current_retrieval
            and citation_allowed
            and citation_forbidden_absent
            and citation_terms_supported
        )
    answer_key_conclusion = (
        not any(
            _contains(answer_response.answer, term)
            for term in expected.forbidden_answer_terms
        )
        if expected.should_handoff
        else all(
            _contains(answer_response.answer, term)
            for term in expected.required_answer_terms
        )
    )
    handoff_correct = (
        answer_response.needs_handoff is expected.should_handoff
        and (not answer_response.citations if expected.should_handoff else True)
        and (
            not any(
                _contains(answer_response.answer, term)
                for term in expected.forbidden_answer_terms
            )
            if expected.should_handoff
            else True
        )
    )
    untrusted_answer_evidence = any(
        result_by_id[citation.chunk_id].untrusted_content
        for citation in citations
        if citation.chunk_id in result_by_id
    )
    checks: dict[str, bool | None] = {
        "retrieval_recall_at_k": (
            expected_version in retrieved_versions
            if expected_version is not None
            else None
        ),
        "retrieval_allowed_only": not unexpected_documents,
        "permission_leak_absent": not forbidden_documents,
        "expired_hit_absent": not expired_documents,
        "citation_correct": citation_correct,
        "answer_key_conclusion": answer_key_conclusion,
        "handoff_correct": handoff_correct,
        "untrusted_answer_evidence_absent": not untrusted_answer_evidence,
    }
    failures = _failure_classifications(checks)
    return {
        "id": case.id,
        "split": case.split or "unspecified",
        "category": case.category,
        "tags": sorted(case.tags),
        "principal": case.principal.model_dump(mode="json"),
        "scope": case.scope.model_dump(mode="json"),
        "question": case.question,
        "allowed_document_keys": sorted(case.allowed_document_keys),
        "expected": expected.model_dump(mode="json"),
        "retrieval": {
            "result_count": len(retrieved),
            "documents": sorted(
                [
                    {
                        "document_key": item.document_key,
                        "document_version": item.document_version,
                        "title": item.title,
                        "matched_lexically": item.matched_lexically,
                        "untrusted_content": item.untrusted_content,
                    }
                    for item in retrieved
                ],
                key=lambda item: (
                    str(item["document_key"]),
                    str(item["document_version"]),
                    str(item["title"]),
                ),
            ),
            "unexpected_document_keys": unexpected_documents,
            "forbidden_document_keys": forbidden_documents,
            "expired_document_keys": expired_documents,
        },
        "citation": {
            "count": len(citations),
            "documents": sorted(
                [
                    {
                        "document_key": item.document_key,
                        "document_version": item.document_version,
                        "title": item.title,
                        "excerpt": item.excerpt,
                    }
                    for item in citations
                ],
                key=lambda item: (
                    str(item["document_key"]),
                    str(item["document_version"]),
                    str(item["title"]),
                ),
            ),
            "belongs_to_current_retrieval": citation_in_current_retrieval,
            "allowed": citation_allowed,
            "forbidden_absent": citation_forbidden_absent,
            "key_terms_supported_by_excerpt": citation_terms_supported,
        },
        "answer": {
            "needs_handoff": answer_response.needs_handoff,
            "citation_count": len(citations),
            "contains_required_terms": all(
                _contains(answer_response.answer, term)
                for term in expected.required_answer_terms
            ),
            "contains_forbidden_terms": any(
                _contains(answer_response.answer, term)
                for term in expected.forbidden_answer_terms
            ),
        },
        "checks": checks,
        "passed": all(value is not False for value in checks.values()),
        "failures": failures,
    }


def _aggregate(case_reports: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not case_reports:
        raise RagEvalConfigurationError("RAG evaluation produced no cases")

    def rate(check: str, *, only: str | None = None) -> float:
        selected = [
            report
            for report in case_reports
            if only is None or report["checks"][only] is not None
        ]
        if not selected:
            return 0.0
        return _rounded(
            sum(bool(report["checks"][check]) for report in selected) / len(selected)
        )

    forbidden_cases = [
        report
        for report in case_reports
        if report["expected"]["forbidden_document_keys"]
    ]
    expired_cases = [
        report for report in case_reports if report["expected"]["expired_document_keys"]
    ]
    non_handoff_cases = [
        report for report in case_reports if not report["expected"]["should_handoff"]
    ]

    def failure_rate(selected: Sequence[Mapping[str, Any]], check: str) -> float:
        if not selected:
            return 0.0
        return _rounded(
            sum(not bool(report["checks"][check]) for report in selected)
            / len(selected)
        )

    return {
        "case_pass_rate": _rounded(
            sum(bool(report["passed"]) for report in case_reports) / len(case_reports)
        ),
        "recall_at_k": rate("retrieval_recall_at_k", only="retrieval_recall_at_k"),
        "retrieval_allowed_only_rate": rate("retrieval_allowed_only"),
        "permission_leak_rate": failure_rate(forbidden_cases, "permission_leak_absent"),
        "expired_hit_rate": failure_rate(expired_cases, "expired_hit_absent"),
        "citation_correctness_rate": (
            _rounded(
                sum(
                    bool(report["checks"]["citation_correct"])
                    for report in non_handoff_cases
                )
                / len(non_handoff_cases)
            )
            if non_handoff_cases
            else 0.0
        ),
        "answer_key_conclusion_rate": (
            _rounded(
                sum(
                    bool(report["checks"]["answer_key_conclusion"])
                    for report in non_handoff_cases
                )
                / len(non_handoff_cases)
            )
            if non_handoff_cases
            else 0.0
        ),
        "handoff_correctness_rate": rate("handoff_correct"),
        "untrusted_answer_evidence_rate": failure_rate(
            case_reports, "untrusted_answer_evidence_absent"
        ),
    }


def _slices(
    case_reports: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, float | int]]]:
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = {
        "split": {},
        "role": {},
        "category": {},
    }
    for report in case_reports:
        dimensions = {
            "split": str(report["split"]),
            "role": str(report["principal"]["role"]),
            "category": str(report["category"] or "uncategorized"),
        }
        for dimension, value in dimensions.items():
            grouped[dimension].setdefault(value, []).append(report)
    return {
        dimension: {
            value: {
                "case_count": len(reports),
                "case_pass_rate": _rounded(
                    sum(bool(report["passed"]) for report in reports) / len(reports)
                ),
                "permission_leak_rate": _rounded(
                    sum(
                        not bool(report["checks"]["permission_leak_absent"])
                        for report in reports
                    )
                    / len(reports)
                ),
                "handoff_correctness_rate": _rounded(
                    sum(bool(report["checks"]["handoff_correct"]) for report in reports)
                    / len(reports)
                ),
            }
            for value, reports in sorted(groups.items())
        }
        for dimension, groups in sorted(grouped.items())
    }


def _validate_operator(db: Session, document: RagCorpusDocument) -> None:
    user = db.get(User, document.operator_user_id)
    if (
        user is None
        or user.tenant_id != document.tenant_id
        or user.role is not UserRole.knowledge_operator
    ):
        raise RagEvalConfigurationError(
            f"corpus document {document.id} has no matching knowledge operator"
        )


def _validate_operator_user(db: Session, principal: CurrentPrincipal) -> None:
    user = db.get(User, principal.user_id)
    if (
        user is None
        or user.tenant_id != principal.tenant_id
        or user.role is not UserRole.knowledge_operator
    ):
        raise RagEvalConfigurationError(
            "rollback drill knowledge operator is unavailable"
        )


def _validate_case_document_references(
    cases: Sequence[RagEvalCase], corpus: RagCorpus
) -> None:
    known_document_keys = {item.document_key for item in corpus.documents}
    for case in cases:
        expected = case.expected
        referenced = {
            *case.allowed_document_keys,
            *expected.forbidden_document_keys,
            *expected.expired_document_keys,
        }
        if expected.expected_document_key is not None:
            referenced.add(expected.expected_document_key)
        unknown = sorted(referenced - known_document_keys)
        if unknown:
            raise RagEvalConfigurationError(
                f"case {case.id} references unknown corpus document keys: {unknown}"
            )


def _gateway_versions(gateway: CitationAnswerGateway) -> dict[str, str | None]:
    prompt = getattr(
        gateway, "knowledge_answer_prompt_version", KNOWLEDGE_ANSWER_PROMPT_VERSION
    )
    prompt_sha256 = getattr(gateway, "knowledge_answer_prompt_sha256", None)
    return {
        "provider": gateway.provider_name,
        "model": gateway.model_name,
        "prompt": str(prompt),
        "prompt_sha256": str(prompt_sha256) if prompt_sha256 is not None else None,
        "schema": KNOWLEDGE_ANSWER_SCHEMA_VERSION,
        "evaluator": RAG_EVALUATOR_VERSION,
    }


def _input_metadata(cases: Sequence[RagEvalCase]) -> dict[str, Any]:
    canonical = [
        case.model_dump(mode="json") for case in sorted(cases, key=lambda item: item.id)
    ]
    return {
        "case_count": len(cases),
        "split_counts": {
            split: sum(case.split == split for case in cases)
            for split in ("public", "holdout")
        },
        "case_fingerprint_sha256": _sha256_json(canonical),
    }


def _threshold_rule(
    metric: str, configured: float | ThresholdBound
) -> tuple[Literal["min", "max"], float]:
    comparator: Literal["min", "max"] = "min" if metric in MINIMUM_METRICS else "max"
    if isinstance(configured, ThresholdBound):
        target = configured.min if comparator == "min" else configured.max
        opposite = configured.max if comparator == "min" else configured.min
        if target is None or opposite is not None:
            raise RagEvalConfigurationError(
                f"threshold {metric} must define only {comparator}"
            )
    else:
        target = configured
    if not math.isfinite(target) or not 0 <= target <= 1:
        raise RagEvalConfigurationError(
            f"RAG rate threshold {metric} must be within [0, 1]"
        )
    return comparator, float(target)


def _failure_classifications(
    checks: Mapping[str, bool | None],
) -> list[dict[str, str]]:
    mapping = {
        "retrieval_recall_at_k": ("retrieval_miss", "retrieval"),
        "retrieval_allowed_only": ("unexpected_authorized_document", "eval_data"),
        "permission_leak_absent": ("permission_leak", "acl"),
        "expired_hit_absent": ("expired_document_hit", "lifecycle"),
        "citation_correct": ("citation_incorrect", "answer_guardrail"),
        "answer_key_conclusion": ("answer_key_conclusion_mismatch", "provider"),
        "handoff_correct": ("handoff_mismatch", "answer_guardrail"),
        "untrusted_answer_evidence_absent": ("untrusted_answer_evidence", "acl"),
    }
    return [
        {
            "classification": classification,
            "candidate_remediation_area": area,
        }
        for check, (classification, area) in mapping.items()
        if checks.get(check) is False
    ]


def _contains(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


def _require_unique(values: Sequence[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")


def _rounded(value: float) -> float:
    return round(value, 6)


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned[:80] or "unknown"


def _render_markdown(report: Mapping[str, Any]) -> str:
    versions = report["versions"]
    aggregate = report["aggregate"]
    lines = [
        "# RAG layered evaluation report",
        "",
        f"Provider/model: `{versions['provider']}` / `{versions['model']}`",
        f"Prompt/schema: `{versions['prompt']}` / `{versions['schema']}`",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    lines.extend(
        f"| `{metric}` | {value:.6f} |" for metric, value in sorted(aggregate.items())
    )
    thresholds = report.get("thresholds")
    if thresholds is not None:
        lines.extend(
            [
                "",
                f"## Thresholds: {'PASS' if thresholds['passed'] else 'FAIL'}",
                "",
                "| Metric | Actual | Requirement | Result |",
                "| --- | ---: | --- | --- |",
            ]
        )
        lines.extend(
            (
                "| `{metric}` | {actual:.6f} | {comparator} {target:.6f} | {result} |"
            ).format(
                metric=item["metric"],
                actual=float(item["actual"]),
                comparator=item["comparator"],
                target=float(item["target"]),
                result="PASS" if item["passed"] else "FAIL",
            )
            for item in thresholds["results"]
        )
    rollback = report.get("rollback_drill")
    if rollback is not None:
        post = rollback["post_withdrawal"]
        regression = rollback.get("post_withdrawal_regression", {})
        lines.extend(
            [
                "",
                (
                    "## Error-policy withdrawal drill: "
                    f"{'PASS' if rollback['passed'] else 'FAIL'}"
                ),
                "",
                (
                    "The pre-release gate detected the intentionally incorrect "
                    f"policy: `{rollback['pre_withdrawal']['detection_triggered']}`."
                ),
                (
                    "After withdrawal: "
                    f"{post['search_result_count']} retrieval result(s), "
                    f"handoff `{post['needs_handoff']}`, "
                    f"{post['citation_count']} citation(s)."
                ),
                (
                    "Post-withdrawal regression cases passed: "
                    f"`{regression.get('case_passed', False)}`."
                ),
            ]
        )
    failures = [
        (case["id"], failure)
        for case in report["cases"]
        for failure in case["failures"]
    ]
    lines.extend(["", "## Failures", ""])
    if failures:
        lines.extend(
            "- `{}`: `{}` (candidate: `{}`)".format(
                case_id,
                failure["classification"],
                failure["candidate_remediation_area"],
            )
            for case_id, failure in failures
        )
    else:
        lines.append("No case-level failures.")
    lines.extend(["", "## Interpretation boundary", ""])
    lines.extend(
        f"- {item}" for item in report["evaluation_contract"]["evidence_boundaries"]
    )
    return "\n".join(lines) + "\n"
