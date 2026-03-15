"""initial_schema

Revision ID: 1d97124cec74
Revises:
Create Date: 2026-03-12 21:55:35.717541

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d97124cec74"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Enum types used in columns ---
scan_status = sa.Enum("queued", "running", "complete", "failed", name="scanstatus")
stage1_result = sa.Enum("suspicious", "not_suspicious", "failed", name="stage1result")
processing_status = sa.Enum("queued", "running", "complete", "failed", "skipped", name="processingstatus")
severity = sa.Enum("low", "medium", "high", "critical", name="severity")
triage_status = sa.Enum("open", "false_positive", "resolved", name="triagestatus")


def upgrade() -> None:
    """Create all initial tables."""

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("clerk_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"], unique=True)

    # --- repositories ---
    op.create_table(
        "repositories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_repositories_user_id", "repositories", ["user_id"])

    # --- scans ---
    op.create_table(
        "scans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", UUID(as_uuid=True), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("status", scan_status, nullable=False, server_default="queued"),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scans_repository_id", "scans", ["repository_id"])
    op.create_index("ix_scans_status", "scans", ["status"])

    # --- scan_files ---
    op.create_table(
        "scan_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scan_id", UUID(as_uuid=True), sa.ForeignKey("scans.id"), nullable=False),
        sa.Column("file_path", sa.String(2048), nullable=False),
        sa.Column("stage1_result", stage1_result, nullable=True),
        sa.Column("stage2_attempted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("processing_status", processing_status, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_scan_files_scan_id", "scan_files", ["scan_id"])

    # --- finding_identities ---
    op.create_table(
        "finding_identities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", UUID(as_uuid=True), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("fingerprint", sa.String(512), nullable=False),
        sa.Column("canonical_vulnerability_type", sa.String(512), nullable=False),
        sa.Column("canonical_file_path", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_finding_identities_repository_id", "finding_identities", ["repository_id"])
    op.create_unique_constraint(
        "uq_finding_identity_repo_fingerprint", "finding_identities", ["repository_id", "fingerprint"]
    )

    # --- finding_occurrences ---
    op.create_table(
        "finding_occurrences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scan_id", UUID(as_uuid=True), sa.ForeignKey("scans.id"), nullable=False),
        sa.Column("finding_identity_id", UUID(as_uuid=True), sa.ForeignKey("finding_identities.id"), nullable=False),
        sa.Column("file_path", sa.String(2048), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("vulnerability_type", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("code_snippet", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_finding_occurrences_scan_id", "finding_occurrences", ["scan_id"])
    op.create_index("ix_finding_occurrences_finding_identity_id", "finding_occurrences", ["finding_identity_id"])

    # --- finding_triage ---
    op.create_table(
        "finding_triage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("finding_occurrence_id", UUID(as_uuid=True), sa.ForeignKey("finding_occurrences.id"), nullable=False),
        sa.Column("status", triage_status, nullable=False, server_default="open"),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_finding_triage_occurrence", "finding_triage", ["finding_occurrence_id"])


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("finding_triage")
    op.drop_table("finding_occurrences")
    op.drop_table("finding_identities")
    op.drop_table("scan_files")
    op.drop_table("scans")
    op.drop_table("repositories")
    op.drop_table("users")

    # Drop enum types
    triage_status.drop(op.get_bind(), checkfirst=True)
    severity.drop(op.get_bind(), checkfirst=True)
    processing_status.drop(op.get_bind(), checkfirst=True)
    stage1_result.drop(op.get_bind(), checkfirst=True)
    scan_status.drop(op.get_bind(), checkfirst=True)
