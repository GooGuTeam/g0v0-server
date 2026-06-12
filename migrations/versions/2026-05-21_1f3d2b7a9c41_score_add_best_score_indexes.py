"""score add best score indexes

Revision ID: 1f3d2b7a9c41
Revises: 5e5f052d0fe2
Create Date: 2026-05-21 23:45:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f3d2b7a9c41"
down_revision: str | Sequence[str] | None = "5e5f052d0fe2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_best_scores_user_mode_pp_score",
        "best_scores",
        ["user_id", "gamemode", "pp", "score_id"],
        unique=False,
    )
    op.create_index(
        "ix_best_scores_mode_pp_score",
        "best_scores",
        ["gamemode", "pp", "score_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_best_scores_mode_pp_score", table_name="best_scores")
    op.drop_index("ix_best_scores_user_mode_pp_score", table_name="best_scores")
