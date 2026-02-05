"""add delivery preview check fields

Revision ID: 20260203_add_delivery_preview_checks
Revises: 20240914_create_delivery_outbox
Create Date: 2026-02-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "20260203_add_delivery_preview_checks"
down_revision = "20240914_create_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("delivery_outbox", sa.Column("preview_url", sa.String(), nullable=True))
    op.add_column(
        "delivery_outbox",
        sa.Column("site_check_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("delivery_outbox", sa.Column("site_check_next_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_outbox", "site_check_next_at")
    op.drop_column("delivery_outbox", "site_check_attempts")
    op.drop_column("delivery_outbox", "preview_url")
