"""chore: merge heads

Revision ID: 001c4010ce30
Revises: 0de49c926cfa, e655329d2a00
Create Date: 2026-10-28 14:28:20.323560

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "001c4010ce30"
down_revision: str | Sequence[str] | None = ("0de49c926cfa", "e655329d2a00")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
