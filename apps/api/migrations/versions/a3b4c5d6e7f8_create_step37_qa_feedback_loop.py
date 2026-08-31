"""create Step 37 supervisor QA feedback-loop records

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-25 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    # Types are explicitly created above, so table creation must not emit a
    # second CREATE TYPE for any of these PostgreSQL enums.
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    categories = _enum(
        "qa_issue_category",
        "factual_error",
        "citation_unsupported",
        "outdated_policy",
        "incorrect_routing",
        "escalation_missing",
        "authorization_privacy",
        "tool_parameter",
        "ui_usability",
        "agent_misuse",
    )
    severities = _enum("qa_issue_severity", "low", "medium", "high", "critical")
    review_status = _enum(
        "qa_review_status",
        "open",
        "in_remediation",
        "verification_pending",
        "verified",
        "closed",
    )
    root_causes = _enum(
        "qa_root_cause",
        "knowledge_data",
        "retrieval",
        "routing_rule",
        "authorization",
        "tool_contract",
        "ui",
        "workflow",
        "model_behavior",
        "agent_process",
    )
    remediation_types = _enum(
        "qa_remediation_type",
        "knowledge",
        "rule",
        "code",
        "ui",
        "workflow",
        "process",
    )
    regression_status = _enum("qa_regression_status", "not_run", "passed", "failed")
    enums = (
        categories,
        severities,
        review_status,
        root_causes,
        remediation_types,
        regression_status,
    )
    for enum in enums:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "qa_review_issues",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_conversation_id", sa.String(), nullable=False),
        sa.Column(
            "category",
            _enum(
                "qa_issue_category",
                "factual_error",
                "citation_unsupported",
                "outdated_policy",
                "incorrect_routing",
                "escalation_missing",
                "authorization_privacy",
                "tool_parameter",
                "ui_usability",
                "agent_misuse",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            _enum("qa_issue_severity", "low", "medium", "high", "critical"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "qa_review_status",
                "open",
                "in_remediation",
                "verification_pending",
                "verified",
                "closed",
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column(
            "root_cause",
            _enum(
                "qa_root_cause",
                "knowledge_data",
                "retrieval",
                "routing_rule",
                "authorization",
                "tool_contract",
                "ui",
                "workflow",
                "model_behavior",
                "agent_process",
            ),
            nullable=False,
        ),
        sa.Column("problem_summary", sa.Text(), nullable=False),
        sa.Column(
            "remediation_type",
            _enum(
                "qa_remediation_type",
                "knowledge",
                "rule",
                "code",
                "ui",
                "workflow",
                "process",
            ),
            nullable=False,
        ),
        sa.Column("remediation_summary", sa.Text(), nullable=False),
        sa.Column("regression_case_id", sa.String(length=128), nullable=False),
        sa.Column("evaluation_suite", sa.String(length=32), nullable=False),
        sa.Column("evaluation_case_id", sa.String(length=128), nullable=False),
        sa.Column(
            "feature_flag_paused",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "feature_flag_snapshot",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "source_snapshot",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "regression_status",
            _enum("qa_regression_status", "not_run", "passed", "failed"),
            server_default="not_run",
            nullable=False,
        ),
        sa.Column("regression_evidence_ref", sa.String(length=255), nullable=True),
        sa.Column("reported_by", sa.String(), nullable=False),
        sa.Column("verified_by", sa.String(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_event_sequence", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reported_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_qa_review_issues_tenant_id", ["tenant_id"]),
        ("ix_qa_review_issues_source_conversation_id", ["source_conversation_id"]),
        ("ix_qa_review_issues_category", ["category"]),
        ("ix_qa_review_issues_severity", ["severity"]),
        ("ix_qa_review_issues_status", ["status"]),
        ("ix_qa_review_issues_owner_id", ["owner_id"]),
        ("ix_qa_review_issues_reported_by", ["reported_by"]),
        (
            "ix_qa_review_issues_tenant_status_severity_created",
            ["tenant_id", "status", "severity", "created_at"],
        ),
    ):
        op.create_index(index_name, "qa_review_issues", columns)

    op.create_table(
        "qa_review_issue_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("issue_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "summary", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["issue_id"], ["qa_review_issues.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issue_id", "sequence", name="uq_qa_review_issue_events_sequence"
        ),
    )
    op.create_index(
        "ix_qa_review_issue_events_issue_id", "qa_review_issue_events", ["issue_id"]
    )

    op.create_table(
        "qa_idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(length=180), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("issue_id", sa.String(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["qa_review_issues.id"]),
        sa.ForeignKeyConstraint(["principal_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "operation",
            "idempotency_key_hash",
            name="uq_qa_idempotency_scope",
        ),
    )
    for index_name, columns in (
        ("ix_qa_idempotency_records_tenant_id", ["tenant_id"]),
        ("ix_qa_idempotency_records_principal_id", ["principal_id"]),
        ("ix_qa_idempotency_records_issue_id", ["issue_id"]),
    ):
        op.create_index(index_name, "qa_idempotency_records", columns)


def downgrade() -> None:
    for index_name in (
        "ix_qa_idempotency_records_issue_id",
        "ix_qa_idempotency_records_principal_id",
        "ix_qa_idempotency_records_tenant_id",
    ):
        op.drop_index(index_name, table_name="qa_idempotency_records")
    op.drop_table("qa_idempotency_records")

    op.drop_index(
        "ix_qa_review_issue_events_issue_id", table_name="qa_review_issue_events"
    )
    op.drop_table("qa_review_issue_events")

    for index_name in (
        "ix_qa_review_issues_tenant_status_severity_created",
        "ix_qa_review_issues_reported_by",
        "ix_qa_review_issues_owner_id",
        "ix_qa_review_issues_status",
        "ix_qa_review_issues_severity",
        "ix_qa_review_issues_category",
        "ix_qa_review_issues_source_conversation_id",
        "ix_qa_review_issues_tenant_id",
    ):
        op.drop_index(index_name, table_name="qa_review_issues")
    op.drop_table("qa_review_issues")

    bind = op.get_bind()
    for name in (
        "qa_regression_status",
        "qa_remediation_type",
        "qa_root_cause",
        "qa_review_status",
        "qa_issue_severity",
        "qa_issue_category",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
