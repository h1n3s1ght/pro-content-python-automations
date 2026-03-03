"""register express tables revision in this repo

Revision ID: 20260302_express_tables
Revises: 20260206_job_copies
Create Date: 2026-03-02 00:00:00.000000

This is intentionally a no-op migration. Some deployed/shared databases are
already stamped at revision ``20260302_express_tables`` from another service's
migration history. Including the same revision ID here allows this repository's
Alembic graph to resolve and continue upgrades cleanly.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "20260302_express_tables"
down_revision: Union[str, Sequence[str], None] = "20260206_job_copies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op by design; see module docstring.
    pass


def downgrade() -> None:
    # No-op by design; this revision only bridges revision history.
    pass
