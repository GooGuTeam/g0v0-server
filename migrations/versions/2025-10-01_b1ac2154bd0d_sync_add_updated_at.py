"""sync: add updated_at

Revision ID: b1ac2154bd0d
Revises: 2885978490dc
Create Date: 2025-10-01 14:56:08.539694

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = "b1ac2154bd0d"
down_revision: str | Sequence[str] | None = "2885978490dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("beatmapsync", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute(sqlmodel.text("UPDATE beatmapsync SET updated_at = NOW() WHERE updated_at IS NULL"))
    op.alter_column("beatmapsync", "updated_at", nullable=False, type_=sa.DateTime())
    op.create_index(op.f("ix_beatmapsync_updated_at"), "beatmapsync", ["updated_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_beatmapsync_updated_at"), table_name="beatmapsync")
    op.drop_column("beatmapsync", "updated_at")
