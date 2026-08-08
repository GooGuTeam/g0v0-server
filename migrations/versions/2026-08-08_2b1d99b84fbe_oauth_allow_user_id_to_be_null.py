"""oauth: allow user_id to be null

Revision ID: 2b1d99b84fbe
Revises: 27eb30853d3d
Create Date: 2026-08-08 04:08:36.521347

"""

from collections.abc import Sequence

from app.const import BANCHOBOT_ID

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2b1d99b84fbe"
down_revision: str | Sequence[str] | None = "27eb30853d3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_oauth_tokens_user_id_type() -> sa.types.TypeEngine:
    for column in sa.inspect(op.get_bind()).get_columns("oauth_tokens"):
        if column["name"] == "user_id":
            return column["type"]
    msg = "oauth_tokens.user_id column not found"
    raise RuntimeError(msg)


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "oauth_tokens",
        "user_id",
        existing_type=_get_oauth_tokens_user_id_type(),
        existing_nullable=False,
        nullable=True,
    )
    op.execute(
        sa.text("""
        UPDATE oauth_tokens
        SET user_id = NULL
        WHERE user_id = :banchobot_id
        """).bindparams(banchobot_id=BANCHOBOT_ID)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text("""
        UPDATE oauth_tokens
        SET user_id = :banchobot_id
        WHERE user_id IS NULL
        """).bindparams(banchobot_id=BANCHOBOT_ID)
    )
    op.alter_column(
        "oauth_tokens",
        "user_id",
        existing_type=_get_oauth_tokens_user_id_type(),
        existing_nullable=True,
        nullable=False,
    )
