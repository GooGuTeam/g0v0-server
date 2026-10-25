"""score: change type from solo to lazer

Revision ID: 800ca2ea8eec
Revises: 299d0e9bba35
Create Date: 2026-10-25 15:47:05.572928

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "800ca2ea8eec"
down_revision: str | Sequence[str] | None = "299d0e9bba35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE score SET type='lazer' WHERE type='solo'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE score SET type='solo' WHERE type='lazer'")
