"""score: add nsmall_tick_miss

Revision ID: b312725e4acb
Revises: 33b85fd3771b
Create Date: 2026-06-18 06:53:23.627903

"""

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b312725e4acb"
down_revision: str | Sequence[str] | None = "33b85fd3771b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "scores",
        sa.Column("nsmall_tick_miss", sa.Integer(), nullable=True),
    )

    conn = op.get_bind()
    r = conn.execute(sa.text("SELECT id, nsmall_tick_hit, maximum_statistics FROM scores WHERE gamemode = 'FRUITS'"))
    scores = r.fetchall()
    for score in scores:
        score_id = score[0]
        nsmall_tick_hit = score[1]
        maximum_statistics = json.loads(score[2]) if score[2] else {}
        max_small_tick_hit = maximum_statistics.get("small_tick_hit", 0)
        nsmall_tick_miss = max_small_tick_hit - nsmall_tick_hit
        if nsmall_tick_miss < 0:
            nsmall_tick_miss = 0
        conn.execute(
            sa.text("UPDATE scores SET nsmall_tick_miss = :nsmall_tick_miss WHERE id = :score_id"),
            {
                "nsmall_tick_miss": nsmall_tick_miss,
                "score_id": score_id,
            },
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("scores", "nsmall_tick_miss")
