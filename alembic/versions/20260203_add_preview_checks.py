"""add delivery preview check fields

Revision ID: 20260203_add_preview_checks
Revises: 20240914_create_delivery_outbox
Create Date: 2026-02-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260203_add_preview_checks"
down_revision = "20240914_create_delivery_outbox"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    cols = inspect(op.get_bind()).get_columns(table_name)
    return any(c.get("name") == column_name for c in cols)


def upgrade() -> None:
    if not _column_exists("delivery_outbox", "preview_url"):
        op.add_column("delivery_outbox", sa.Column("preview_url", sa.String(), nullable=True))

    if not _column_exists("delivery_outbox", "site_check_attempts"):
        op.add_column(
            "delivery_outbox",
            sa.Column("site_check_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )

    if not _column_exists("delivery_outbox", "site_check_next_at"):
        op.add_column(
            "delivery_outbox",
            sa.Column("site_check_next_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("delivery_outbox", "site_check_next_at"):
        op.drop_column("delivery_outbox", "site_check_next_at")
    if _column_exists("delivery_outbox", "site_check_attempts"):
        op.drop_column("delivery_outbox", "site_check_attempts")
    if _column_exists("delivery_outbox", "preview_url"):
        op.drop_column("delivery_outbox", "preview_url")
