from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import auth
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.models.identity import UserRole
from app.models.knowledge import KnowledgeImport, KnowledgeImportStatus
from app.modules.identity.principal import CurrentPrincipal
from app.modules.knowledge import retrieval
from app.modules.knowledge.schemas import KnowledgeSearchRequest
from app.modules.knowledge.worker import process_pending_imports


def _knowledge_fixture_content(filename: str) -> str:
    fixture_root = Path(__file__).resolve().parents[3] / "data/fixtures/knowledge"
    return (fixture_root / filename).read_text(encoding="utf-8")


def _import_document(
    client,
    db,
    *,
    operator: str,
    document_key: str,
    title: str,
    content: str,
    audience: str = "public",
    product: str = "demo-support",
    region: str = "cn-north",
    language: str = "zh-CN",
    version: str = "1.0.0",
    requested_status: str = "published",
    effective_at: str = "2026-01-01T00:00:00Z",
    expires_at: str | None = None,
    untrusted_content: bool = False,
) -> dict:
    payload = {
        "filename": f"{document_key}-{version}.md",
        "title": title,
        "content": content,
        "document_key": document_key,
        "owner": "demo-knowledge-operations",
        "audience": audience,
        "product": product,
        "region": region,
        "language": language,
        "version": version,
        "effective_at": effective_at,
        "requested_status": requested_status,
        "untrusted_content": untrusted_content,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    response = client.post(
        "/v1/admin/knowledge/imports",
        json=payload,
        headers=auth(operator),
    )
    assert response.status_code == 202
    process_pending_imports(db, limit=10)
    item = db.get(KnowledgeImport, response.json()["id"])
    assert item is not None
    assert item.status is KnowledgeImportStatus.published
    return response.json()


def _search(
    client,
    user_id: str,
    *,
    query: str,
    region: str,
    product: str = "demo-support",
    language: str = "zh-CN",
    limit: int = 10,
):
    return client.post(
        "/v1/knowledge/search",
        json={
            "query": query,
            "product": product,
            "region": region,
            "language": language,
            "limit": limit,
        },
        headers=auth(user_id),
    )


def test_search_is_server_acl_filtered_by_tenant_audience_and_customer_region(
    client, db
):
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="northwind-public-return",
        title="Northwind customer return policy",
        content="退款流程：Northwind 客户可在 30 天内申请退款。",
    )
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="northwind-internal-return",
        title="Northwind internal return escalation",
        content="退款流程：仅限坐席使用内部升级路径。",
        audience="internal",
    )
    _import_document(
        client,
        db,
        operator="user-ct-knowledge-operator-01",
        document_key="contoso-public-return",
        title="Contoso customer return policy",
        content="退款流程：Contoso 客户适用另一套规则。",
        region="all",
    )

    northwind_customer = _search(
        client, "user-nw-customer-01", query="退款流程", region="cn-north"
    )
    assert northwind_customer.status_code == 200
    customer_results = northwind_customer.json()["results"]
    assert [result["title"] for result in customer_results] == [
        "Northwind customer return policy"
    ]
    assert customer_results[0]["document_version"] == "1.0.0"
    assert customer_results[0]["matched_lexically"] is True
    assert "退款流程" in customer_results[0]["snippet"]

    northwind_agent = _search(
        client, "user-nw-agent-01", query="退款流程", region="cn-north"
    )
    assert northwind_agent.status_code == 200
    assert {result["title"] for result in northwind_agent.json()["results"]} == {
        "Northwind customer return policy",
        "Northwind internal return escalation",
    }

    contoso_customer = _search(
        client, "user-ct-customer-01", query="退款流程", region="cn-east"
    )
    assert contoso_customer.status_code == 200
    assert [result["title"] for result in contoso_customer.json()["results"]] == [
        "Contoso customer return policy"
    ]

    spoofed_region = _search(
        client, "user-nw-customer-01", query="退款流程", region="cn-south"
    )
    assert spoofed_region.status_code == 403


def test_search_excludes_expired_and_withdrawn_documents(client, db):
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="active-warranty",
        title="Active warranty policy",
        content="保修范围：当前政策仍然有效。",
    )
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="expired-warranty",
        title="Expired warranty policy",
        content="保修范围：旧政策已经失效。",
        requested_status="expired",
        effective_at="2025-01-01T00:00:00Z",
        expires_at="2026-01-01T00:00:00Z",
    )
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="withdrawn-warranty",
        title="Withdrawn warranty policy",
        content="保修范围：该政策已经撤回。",
        requested_status="withdrawn",
    )

    response = _search(
        client, "user-nw-customer-01", query="保修范围", region="cn-north"
    )
    assert response.status_code == 200
    assert [result["title"] for result in response.json()["results"]] == [
        "Active warranty policy"
    ]


def test_untrusted_document_is_retrieved_as_data_without_expanding_acl(client, db):
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="untrusted-instruction",
        title="Untrusted instruction test",
        content=_knowledge_fixture_content("untrusted-instruction.md"),
        untrusted_content=True,
    )

    response = _search(
        client,
        "user-nw-customer-01",
        query="忽略应用规则",
        region="cn-north",
        limit=1,
    )
    assert response.status_code == 200
    result = response.json()["results"]
    assert [item["title"] for item in result] == ["Untrusted instruction test"]
    assert result[0]["untrusted_content"] is True

    # The text cannot override server-side identity/region checks.
    blocked = _search(
        client,
        "user-nw-customer-01",
        query="忽略应用规则",
        region="cn-south",
    )
    assert blocked.status_code == 403


def test_knowledge_operator_cannot_use_reader_endpoint(client):
    response = _search(
        client,
        "user-nw-knowledge-operator-01",
        query="退款流程",
        region="cn-north",
    )
    assert response.status_code == 403


def test_search_uses_acl_filtered_vector_fallback_when_text_does_not_match(client, db):
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="vector-fallback",
        title="Vector fallback candidate",
        content="这份资料只用来验证允许范围内的向量候选查询。",
    )

    response = _search(
        client,
        "user-nw-customer-01",
        query="文档中不存在的查询词",
        region="cn-north",
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    result = results[0]
    assert result["chunk_id"].startswith("knowledge-chunk-")
    assert result["document_key"] == "vector-fallback"
    assert result["document_version"] == "1.0.0"
    assert result["title"] == "Vector fallback candidate"
    assert result["snippet"] == "这份资料只用来验证允许范围内的向量候选查询。"
    assert result["product"] == "demo-support"
    assert result["region"] == "cn-north"
    assert result["language"] == "zh-CN"
    assert result["effective_at"] == "2026-01-01T00:00:00Z"
    assert result["expires_at"] is None
    assert result["matched_lexically"] is False
    assert result["untrusted_content"] is False


def test_search_applies_product_region_and_language_scope_with_global_fallback(
    client, db
):
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="scoped-search",
        title="Scoped search policy",
        content="适用范围测试：只适用于标准产品。",
    )
    _import_document(
        client,
        db,
        operator="user-nw-knowledge-operator-01",
        document_key="global-search",
        title="Global search policy",
        content="适用范围测试：适用于所有产品和地区。",
        product="all",
        region="all",
        language="all",
    )

    scoped = _search(
        client,
        "user-nw-customer-01",
        query="适用范围测试",
        region="cn-north",
    )
    assert {result["title"] for result in scoped.json()["results"]} == {
        "Scoped search policy",
        "Global search policy",
    }

    different_product = _search(
        client,
        "user-nw-customer-01",
        query="适用范围测试",
        product="another-product",
        region="cn-north",
    )
    assert different_product.status_code == 200
    assert [result["title"] for result in different_product.json()["results"]] == [
        "Global search policy"
    ]


def test_customer_principal_must_bind_to_a_database_customer_record(db):
    request = KnowledgeSearchRequest(
        query="退款流程",
        product="demo-support",
        region="cn-north",
        language="zh-CN",
    )
    incomplete = CurrentPrincipal(
        user_id="user-nw-customer-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
    )
    invalid = incomplete.model_copy(update={"customer_id": "missing-customer"})

    for principal in (incomplete, invalid):
        with pytest.raises(HTTPException) as exc_info:
            retrieval.search_knowledge(db, principal, request)
        assert exc_info.value.status_code == 403


def test_acl_predicates_are_in_sql_before_lexical_and_vector_ranking():
    principal = CurrentPrincipal(
        user_id="user-nw-customer-01",
        tenant_id="tenant-northwind-demo",
        role=UserRole.customer,
        verification_level="demo_verified",
        customer_id="customer-nw-01",
    )
    request = KnowledgeSearchRequest(
        query="退款流程",
        product="demo-support",
        region="cn-north",
        language="zh-CN",
    )
    statement = retrieval.build_acl_filtered_search_statement(
        principal=principal,
        query=request.query,
        product=request.product,
        region=request.region,
        language=request.language,
        audiences=("public", "customer"),
        now=datetime.now(UTC),
        limit=request.limit,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "knowledge_chunks.embedding <->" in sql
    assert sql.index("WHERE") < sql.index("ORDER BY")
    for predicate in (
        "knowledge_chunks.tenant_id",
        "knowledge_chunks.audience",
        "knowledge_chunks.product",
        "knowledge_chunks.region",
        "knowledge_chunks.language",
        "knowledge_chunks.status",
        "knowledge_documents.status",
    ):
        assert predicate in sql
