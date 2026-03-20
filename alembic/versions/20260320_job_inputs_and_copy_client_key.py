"""add job_inputs and job_copies.client_key

Revision ID: 20260320_job_inputs_client_key
Revises: 20260302_express_tables
Create Date: 2026-03-20 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

revision = "20260320_job_inputs_client_key"
down_revision = "20260302_express_tables"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    cols = inspect(op.get_bind()).get_columns(table_name)
    return any(c.get("name") == column_name for c in cols)


def _index_exists(table_name: str, index_name: str) -> bool:
    indexes = inspect(op.get_bind()).get_indexes(table_name)
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    if not _table_exists("job_inputs"):
        op.create_table(
            "job_inputs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("client_name", sa.String(), nullable=False, server_default=""),
            sa.Column("business_domain", sa.String(), nullable=False, server_default=""),
            sa.Column("client_key", sa.String(), nullable=False, server_default=""),
            sa.Column(
                "input_payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("job_id", name="uq_job_inputs_job_id"),
        )

    if not _index_exists("job_inputs", "ix_job_inputs_client_key"):
        op.create_index("ix_job_inputs_client_key", "job_inputs", ["client_key"], unique=False)
    if not _index_exists("job_inputs", "ix_job_inputs_created_at"):
        op.create_index("ix_job_inputs_created_at", "job_inputs", ["created_at"], unique=False)

    if _table_exists("job_copies") and not _column_exists("job_copies", "client_key"):
        op.add_column("job_copies", sa.Column("client_key", sa.String(), nullable=False, server_default=""))

    if _table_exists("job_copies"):
        # Backfill for legacy rows using normalized client_name.
        op.execute(
            text(
                """
                UPDATE job_copies
                SET client_key = regexp_replace(lower(COALESCE(client_name, '')), '[^a-z0-9]+', '', 'g')
                WHERE COALESCE(client_key, '') = ''
                """
            )
        )
        if not _index_exists("job_copies", "ix_job_copies_client_key"):
            op.create_index("ix_job_copies_client_key", "job_copies", ["client_key"], unique=False)


def downgrade() -> None:
    if _table_exists("job_copies"):
        if _index_exists("job_copies", "ix_job_copies_client_key"):
            op.drop_index("ix_job_copies_client_key", table_name="job_copies")
        if _column_exists("job_copies", "client_key"):
            op.drop_column("job_copies", "client_key")

    if _table_exists("job_inputs"):
        if _index_exists("job_inputs", "ix_job_inputs_created_at"):
            op.drop_index("ix_job_inputs_created_at", table_name="job_inputs")
        if _index_exists("job_inputs", "ix_job_inputs_client_key"):
            op.drop_index("ix_job_inputs_client_key", table_name="job_inputs")
        op.drop_table("job_inputs")
