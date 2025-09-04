"""merge branches

Revision ID: 780138a542d7
Revises: 34a563187e47, d16e38b4060c
Create Date: 2025-08-26 05:57:09.622618

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "780138a542d7"
down_revision: str | Sequence[str] | None = ("34a563187e47", "d16e38b4060c")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
