"""beatmapset: remove play_count

Revision ID: ee13ad926584
Revises: 9556cd2ec11f
Create Date: 2025-10-04 10:48:00.985529

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ee13ad926584"
down_revision: str | Sequence[str] | None = "9556cd2ec11f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("beatmapsets", "play_count")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("beatmapsets", sa.Column("play_count", sa.Integer(), nullable=False))
