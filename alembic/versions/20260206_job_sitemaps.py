"""store job sitemaps in db

Revision ID: 20260206_job_sitemaps
Revises: 20260203_add_preview_checks
Create Date: 2026-02-06 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260206_job_sitemaps"
down_revision = "20260203_add_preview_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_sitemaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="generated"),
        sa.Column("stamp", sa.String(), nullable=True),
        sa.Column("rows_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "sitemap_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_job_sitemaps_job_id"),
    )

    op.create_index("ix_job_sitemaps_client_name", "job_sitemaps", ["client_name"], unique=False)
    op.create_index("ix_job_sitemaps_created_at", "job_sitemaps", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_sitemaps_created_at", table_name="job_sitemaps")
    op.drop_index("ix_job_sitemaps_client_name", table_name="job_sitemaps")
    op.drop_table("job_sitemaps")

