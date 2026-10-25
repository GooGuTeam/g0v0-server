"""screenshot: add management indexes

Revision ID: f863e2da0115
Revises: 49dfd34240d0
Create Date: 2026-10-19 12:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f863e2da0115"
down_revision: str | Sequence[str] | None = "49dfd34240d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_screenshots_user_hash", "screenshots", ["user_id", "sha256_hash"], unique=False)
    op.create_index("ix_screenshots_hits_timestamp", "screenshots", ["hits", "timestamp"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_screenshots_hits_timestamp", table_name="screenshots")
    op.drop_index("ix_screenshots_user_hash", table_name="screenshots")
