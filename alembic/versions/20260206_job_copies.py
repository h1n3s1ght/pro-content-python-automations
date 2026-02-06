"""store job copy payloads in db

Revision ID: 20260206_job_copies
Revises: 20260206_job_sitemaps
Create Date: 2026-02-06 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "20260206_job_copies"
down_revision = "20260206_job_sitemaps"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = inspect(op.get_bind()).get_indexes(table_name)
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    if not _table_exists("job_copies"):
        op.create_table(
            "job_copies",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("client_name", sa.String(), nullable=False),
            sa.Column(
                "copy_data",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("job_id", name="uq_job_copies_job_id"),
        )

    if not _index_exists("job_copies", "ix_job_copies_client_name"):
        op.create_index("ix_job_copies_client_name", "job_copies", ["client_name"], unique=False)
    if not _index_exists("job_copies", "ix_job_copies_created_at"):
        op.create_index("ix_job_copies_created_at", "job_copies", ["created_at"], unique=False)

    if not _table_exists("recently_deleted_job_copies"):
        op.create_table(
            "recently_deleted_job_copies",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("client_name", sa.String(), nullable=False),
            sa.Column(
                "copy_data",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("destroy_after", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("job_id", name="uq_recently_deleted_job_copies_job_id"),
        )

    if not _index_exists("recently_deleted_job_copies", "ix_recently_deleted_job_copies_destroy_after"):
        op.create_index(
            "ix_recently_deleted_job_copies_destroy_after",
            "recently_deleted_job_copies",
            ["destroy_after"],
            unique=False,
        )
    if not _index_exists("recently_deleted_job_copies", "ix_recently_deleted_job_copies_deleted_at"):
        op.create_index(
            "ix_recently_deleted_job_copies_deleted_at",
            "recently_deleted_job_copies",
            ["deleted_at"],
            unique=False,
        )


def downgrade() -> None:
    if _table_exists("recently_deleted_job_copies"):
        if _index_exists("recently_deleted_job_copies", "ix_recently_deleted_job_copies_deleted_at"):
            op.drop_index("ix_recently_deleted_job_copies_deleted_at", table_name="recently_deleted_job_copies")
        if _index_exists("recently_deleted_job_copies", "ix_recently_deleted_job_copies_destroy_after"):
            op.drop_index("ix_recently_deleted_job_copies_destroy_after", table_name="recently_deleted_job_copies")
        op.drop_table("recently_deleted_job_copies")

    if _table_exists("job_copies"):
        if _index_exists("job_copies", "ix_job_copies_created_at"):
            op.drop_index("ix_job_copies_created_at", table_name="job_copies")
        if _index_exists("job_copies", "ix_job_copies_client_name"):
            op.drop_index("ix_job_copies_client_name", table_name="job_copies")
        op.drop_table("job_copies")

