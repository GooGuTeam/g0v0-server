"""merge room source auth and screenshot heads

Revision ID: 299d0e9bba35
Revises: f863e2da0115, 8196cbd84c5e, 84cceb652fe5
Create Date: 2026-10-25 09:11:54.797829

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "299d0e9bba35"
down_revision: str | Sequence[str] | None = ("f863e2da0115", "8196cbd84c5e", "84cceb652fe5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
