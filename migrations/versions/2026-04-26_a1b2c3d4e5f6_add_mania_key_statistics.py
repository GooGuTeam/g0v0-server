"""add mania key statistics table and key_count to rank history

Revision ID: a1b2c3d4e5f6
Revises: 5e5f052d0fe2
Create Date: 2026-04-26 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "5e5f052d0fe2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create mania key statistics table and add key_count to rank_history/rank_top."""
    # Create the lazer_user_mania_key_statistics table
    op.create_table(
        "lazer_user_mania_key_statistics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("lazer_users.id"), nullable=False),
        sa.Column("key_count", sa.Integer(), nullable=False),
        sa.Column("count_100", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("count_300", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("count_50", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("count_miss", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("pp", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ranked_score", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("hit_accuracy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_score", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_hits", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("maximum_combo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("play_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("play_time", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_ranked", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("grade_ss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grade_ssh", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grade_s", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grade_sh", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grade_a", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level_current", sa.Float(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lazer_user_mania_key_statistics_user_id"), "lazer_user_mania_key_statistics", ["user_id"], unique=False)
    op.create_index(op.f("ix_lazer_user_mania_key_statistics_key_count"), "lazer_user_mania_key_statistics", ["key_count"], unique=False)
    op.create_index(op.f("ix_lazer_user_mania_key_statistics_pp"), "lazer_user_mania_key_statistics", ["pp"], unique=False)
    op.create_index(
        "ix_mania_key_stats_user_key",
        "lazer_user_mania_key_statistics",
        ["user_id", "key_count"],
        unique=True,
    )

    # Add key_count column to rank_history
    op.add_column(
        "rank_history",
        sa.Column("key_count", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_rank_history_key_count"),
        "rank_history",
        ["key_count"],
    )

    # Add key_count column to rank_top
    op.add_column(
        "rank_top",
        sa.Column("key_count", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_rank_top_key_count"),
        "rank_top",
        ["key_count"],
    )

    # Migrate existing mania key mode data from "mania_X" format
    # to proper GameMode.MANIA + key_count columns
    # e.g. mode="mania_4" -> mode="mania", key_count=4
    op.execute(
        """
        UPDATE rank_history
        SET mode = 'mania', key_count = CAST(SUBSTRING(mode, 7) AS UNSIGNED)
        WHERE mode LIKE 'mania_%' AND mode != 'mania'
        """
    )
    op.execute(
        """
        UPDATE rank_top
        SET mode = 'mania', key_count = CAST(SUBSTRING(mode, 7) AS UNSIGNED)
        WHERE mode LIKE 'mania_%' AND mode != 'mania'
        """
    )


def downgrade() -> None:
    """Remove mania key statistics table and key_count columns."""
    # Revert mania key data back to "mania_X" format
    op.execute(
        """
        UPDATE rank_history
        SET mode = CONCAT('mania_', key_count)
        WHERE mode = 'mania' AND key_count IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE rank_top
        SET mode = CONCAT('mania_', key_count)
        WHERE mode = 'mania' AND key_count IS NOT NULL
        """
    )

    # Remove key_count from rank_top
    op.drop_index(op.f("ix_rank_top_key_count"), table_name="rank_top")
    op.drop_column("rank_top", "key_count")

    # Remove key_count from rank_history
    op.drop_index(op.f("ix_rank_history_key_count"), table_name="rank_history")
    op.drop_column("rank_history", "key_count")

    # Drop the mania key statistics table
    op.drop_index("ix_mania_key_stats_user_key", table_name="lazer_user_mania_key_statistics")
    op.drop_index(op.f("ix_lazer_user_mania_key_statistics_pp"), table_name="lazer_user_mania_key_statistics")
    op.drop_index(op.f("ix_lazer_user_mania_key_statistics_key_count"), table_name="lazer_user_mania_key_statistics")
    op.drop_index(op.f("ix_lazer_user_mania_key_statistics_user_id"), table_name="lazer_user_mania_key_statistics")
    op.drop_table("lazer_user_mania_key_statistics")
