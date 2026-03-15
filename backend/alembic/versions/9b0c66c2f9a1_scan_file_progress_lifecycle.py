"""scan_file_progress_lifecycle

Revision ID: 9b0c66c2f9a1
Revises: 1d97124cec74
Create Date: 2026-03-15 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b0c66c2f9a1"
down_revision: Union[str, Sequence[str], None] = "1d97124cec74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE processingstatus ADD VALUE IF NOT EXISTS 'queued'")
    op.execute("ALTER TYPE processingstatus ADD VALUE IF NOT EXISTS 'running'")

    op.add_column("scan_files", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("scan_files", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE scan_files SET processing_status = 'queued' WHERE processing_status IS NULL")


def downgrade() -> None:
    op.drop_column("scan_files", "completed_at")
    op.drop_column("scan_files", "started_at")

    op.execute("UPDATE scan_files SET processing_status = 'skipped' WHERE processing_status IN ('queued', 'running')")
    op.execute("ALTER TABLE scan_files ALTER COLUMN processing_status TYPE text USING processing_status::text")
    op.execute("DROP TYPE processingstatus")
    op.execute("CREATE TYPE processingstatus AS ENUM ('complete', 'failed', 'skipped')")
    op.execute(
        "ALTER TABLE scan_files ALTER COLUMN processing_status TYPE processingstatus USING processing_status::processingstatus"
    )
