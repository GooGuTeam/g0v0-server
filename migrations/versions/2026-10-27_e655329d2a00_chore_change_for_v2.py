"""score: add classic_total_score_without_mods column and index for type, processed, id

Revision ID: e655329d2a00
Revises: 6ef25df266b2
Create Date: 2026-10-27 15:15:58.981196

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e655329d2a00"
down_revision: str | Sequence[str] | None = "6ef25df266b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scores",
        sa.Column(
            "classic_total_score_without_mods",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_scores_type_processed_id",
        "scores",
        ["type", "processed", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_scores_type_processed_id", table_name="scores")
    op.drop_column("scores", "classic_total_score_without_mods")
