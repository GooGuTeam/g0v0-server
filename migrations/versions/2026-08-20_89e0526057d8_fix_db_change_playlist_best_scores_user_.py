"""fix(db): change playlist_best_scores user_id from bigint to int

Revision ID: 89e0526057d8
Revises: 57a4930b6961
Create Date: 2026-08-20 19:05:11.091187

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "89e0526057d8"
down_revision: str | Sequence[str] | None = "57a4930b6961"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: playlist_best_scores.user_id bigint -> int."""
    # playlist_best_scores.user_id references lazer_users.id (playlist_best_scores_ibfk_3)
    op.drop_constraint("playlist_best_scores_ibfk_3", "playlist_best_scores", type_="foreignkey")
    op.alter_column(
        "playlist_best_scores",
        "user_id",
        existing_type=mysql.BIGINT(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.create_foreign_key(None, "playlist_best_scores", "lazer_users", ["user_id"], ["id"])


def downgrade() -> None:
    """Downgrade schema: playlist_best_scores.user_id int -> bigint."""
    op.drop_constraint(None, "playlist_best_scores", type_="foreignkey")
    op.alter_column(
        "playlist_best_scores",
        "user_id",
        existing_type=sa.Integer(),
        type_=mysql.BIGINT(),
        existing_nullable=True,
    )
    op.create_foreign_key("playlist_best_scores_ibfk_3", "playlist_best_scores", "lazer_users", ["user_id"], ["id"])
