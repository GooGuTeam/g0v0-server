"""feat(db): add room source

Revision ID: 84cceb652fe5
Revises: 7b7027fa25b3

Create Date: 2026-10-25 09:11:08.507109

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "84cceb652fe5"
down_revision: str | Sequence[str] | None = "7b7027fa25b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Tag existing rooms as Realtime and persist ownership for new rooms."""
    op.add_column(
        "rooms",
        sa.Column("source", sa.String(length=32), server_default="Realtime", nullable=False),
    )
    op.create_index("ix_rooms_source", "rooms", ["source"], unique=False)


def downgrade() -> None:
    """Remove room ownership tracking."""
    op.drop_index("ix_rooms_source", table_name="rooms")
    op.drop_column("rooms", "source")
