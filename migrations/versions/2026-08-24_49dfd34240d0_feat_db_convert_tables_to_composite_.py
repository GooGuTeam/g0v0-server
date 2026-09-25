"""feat(db): convert tables to composite primary keys and drop surrogate id columns

Revision ID: 49dfd34240d0
Revises: 57a4930b6961
Create Date: 2026-08-24 23:16:57.279991

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "49dfd34240d0"
down_revision: str | Sequence[str] | None = "57a4930b6961"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: replace surrogate id PKs with composite PKs."""
    # Drop FKs first (MySQL requires no FK on indexes being dropped)
    op.drop_constraint("lazer_user_achievements_ibfk_1", "lazer_user_achievements", type_="foreignkey")
    op.drop_constraint("beatmap_ratings_ibfk_1", "beatmap_ratings", type_="foreignkey")
    op.drop_constraint("beatmap_ratings_ibfk_2", "beatmap_ratings", type_="foreignkey")
    op.drop_constraint("favourite_beatmapset_ibfk_1", "favourite_beatmapset", type_="foreignkey")
    op.drop_constraint("favourite_beatmapset_ibfk_2", "favourite_beatmapset", type_="foreignkey")
    op.drop_constraint("rank_history_ibfk_1", "rank_history", type_="foreignkey")
    op.drop_constraint("relationship_ibfk_1", "relationship", type_="foreignkey")
    op.drop_constraint("relationship_ibfk_2", "relationship", type_="foreignkey")
    op.drop_constraint("room_participated_users_ibfk_1", "room_participated_users", type_="foreignkey")
    op.drop_constraint("room_participated_users_ibfk_2", "room_participated_users", type_="foreignkey")
    op.drop_constraint("lazer_user_statistics_ibfk_1", "lazer_user_statistics", type_="foreignkey")

    # ---- lazer_user_achievements: PK (user_id, achievement_id) ----
    op.drop_constraint("uq_user_achievement", "lazer_user_achievements", type_="unique")
    op.drop_index("ix_lazer_user_achievements_achievement_id", table_name="lazer_user_achievements")
    op.drop_index("ix_lazer_user_achievements_id", table_name="lazer_user_achievements")
    op.alter_column(
        "lazer_user_achievements", "id", existing_type=sa.Integer(), existing_nullable=False, autoincrement=False
    )
    op.drop_constraint("PRIMARY", "lazer_user_achievements", type_="primary")
    op.drop_column("lazer_user_achievements", "id")
    op.alter_column("lazer_user_achievements", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("lazer_user_achievements", "achievement_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_lazer_user_achievements", "lazer_user_achievements", ["user_id", "achievement_id"])
    op.create_foreign_key(
        "lazer_user_achievements_ibfk_1", "lazer_user_achievements", "lazer_users", ["user_id"], ["id"]
    )

    # ---- beatmap_ratings: PK (beatmapset_id, user_id) ----
    op.drop_index("ix_beatmap_ratings_beatmapset_id", table_name="beatmap_ratings")
    op.drop_index("ix_beatmap_ratings_user_id", table_name="beatmap_ratings")
    op.alter_column("beatmap_ratings", "id", existing_type=mysql.BIGINT(), existing_nullable=False, autoincrement=False)
    op.drop_constraint("PRIMARY", "beatmap_ratings", type_="primary")
    op.drop_column("beatmap_ratings", "id")
    op.alter_column("beatmap_ratings", "user_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_beatmap_ratings", "beatmap_ratings", ["beatmapset_id", "user_id"])
    op.create_foreign_key("beatmap_ratings_ibfk_1", "beatmap_ratings", "beatmapsets", ["beatmapset_id"], ["id"])
    op.create_foreign_key("beatmap_ratings_ibfk_2", "beatmap_ratings", "lazer_users", ["user_id"], ["id"])

    # ---- favourite_beatmapset: PK (user_id, beatmapset_id) ----
    op.drop_index("ix_favourite_beatmapset_beatmapset_id", table_name="favourite_beatmapset")
    op.drop_index("ix_favourite_beatmapset_user_id", table_name="favourite_beatmapset")
    op.alter_column(
        "favourite_beatmapset", "id", existing_type=mysql.BIGINT(), existing_nullable=False, autoincrement=False
    )
    op.drop_constraint("PRIMARY", "favourite_beatmapset", type_="primary")
    op.drop_column("favourite_beatmapset", "id")
    op.alter_column("favourite_beatmapset", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("favourite_beatmapset", "beatmapset_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_favourite_beatmapset", "favourite_beatmapset", ["user_id", "beatmapset_id"])
    op.create_foreign_key(
        "favourite_beatmapset_ibfk_1", "favourite_beatmapset", "beatmapsets", ["beatmapset_id"], ["id"]
    )
    op.create_foreign_key("favourite_beatmapset_ibfk_2", "favourite_beatmapset", "lazer_users", ["user_id"], ["id"])

    # ---- rank_history: PK (user_id, mode, date) ----
    op.drop_index("ix_rank_history_date", table_name="rank_history")
    op.drop_index("ix_rank_history_user_id", table_name="rank_history")
    op.alter_column("rank_history", "id", existing_type=mysql.BIGINT(), existing_nullable=False, autoincrement=False)
    op.drop_constraint("PRIMARY", "rank_history", type_="primary")
    op.drop_column("rank_history", "id")
    op.alter_column("rank_history", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("rank_history", "date", existing_type=sa.Date(), nullable=False)
    op.create_primary_key("pk_rank_history", "rank_history", ["user_id", "mode", "date"])
    op.create_index("ix_rank_history_user_date", "rank_history", ["user_id", "date"], unique=False)
    op.create_foreign_key("rank_history_ibfk_1", "rank_history", "lazer_users", ["user_id"], ["id"])

    # ---- relationship: PK (user_id, target_id, type) ----
    op.drop_index("ix_relationship_target_id", table_name="relationship")
    op.drop_index("ix_relationship_user_id", table_name="relationship")
    op.alter_column("relationship", "id", existing_type=mysql.BIGINT(), existing_nullable=False, autoincrement=False)
    op.drop_constraint("PRIMARY", "relationship", type_="primary")
    op.drop_column("relationship", "id")
    op.alter_column("relationship", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("relationship", "target_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_relationship", "relationship", ["user_id", "target_id", "type"])
    op.create_foreign_key("relationship_ibfk_1", "relationship", "lazer_users", ["target_id"], ["id"])
    op.create_foreign_key("relationship_ibfk_2", "relationship", "lazer_users", ["user_id"], ["id"])

    # ---- room_participated_users: PK (room_id, user_id) ----
    # NOTE: when the FKs were dropped above, MySQL auto-renamed the supporting
    # indexes to match the FK constraint names.
    op.drop_index("room_participated_users_ibfk_1", table_name="room_participated_users")
    op.drop_index("room_participated_users_ibfk_2", table_name="room_participated_users")
    op.alter_column(
        "room_participated_users", "id", existing_type=mysql.BIGINT(), existing_nullable=False, autoincrement=False
    )
    op.drop_constraint("PRIMARY", "room_participated_users", type_="primary")
    op.drop_column("room_participated_users", "id")
    op.create_primary_key("pk_room_participated_users", "room_participated_users", ["room_id", "user_id"])
    op.create_index(
        "ix_room_participated_users_room_left_at", "room_participated_users", ["room_id", "left_at"], unique=False
    )
    op.create_foreign_key("room_participated_users_ibfk_1", "room_participated_users", "rooms", ["room_id"], ["id"])
    op.create_foreign_key(
        "room_participated_users_ibfk_2", "room_participated_users", "lazer_users", ["user_id"], ["id"]
    )

    # ---- lazer_user_statistics: PK (user_id, mode) ----
    op.drop_index("ix_lazer_user_statistics_user_id", table_name="lazer_user_statistics")
    op.drop_index("ix_lazer_user_statistics_mode", table_name="lazer_user_statistics")
    op.alter_column(
        "lazer_user_statistics", "id", existing_type=sa.Integer(), existing_nullable=False, autoincrement=False
    )
    op.drop_constraint("PRIMARY", "lazer_user_statistics", type_="primary")
    op.drop_column("lazer_user_statistics", "id")
    op.alter_column("lazer_user_statistics", "user_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_lazer_user_statistics", "lazer_user_statistics", ["user_id", "mode"])
    op.create_index("ix_lazer_user_statistics_mode_pp", "lazer_user_statistics", ["mode", "pp"], unique=False)
    op.create_foreign_key("lazer_user_statistics_ibfk_1", "lazer_user_statistics", "lazer_users", ["user_id"], ["id"])


def downgrade() -> None:
    """Downgrade schema: restore surrogate id PKs."""
    # Drop new FKs
    op.drop_constraint("lazer_user_statistics_ibfk_1", "lazer_user_statistics", type_="foreignkey")
    op.drop_constraint("room_participated_users_ibfk_2", "room_participated_users", type_="foreignkey")
    op.drop_constraint("room_participated_users_ibfk_1", "room_participated_users", type_="foreignkey")
    op.drop_constraint("relationship_ibfk_2", "relationship", type_="foreignkey")
    op.drop_constraint("relationship_ibfk_1", "relationship", type_="foreignkey")
    op.drop_constraint("rank_history_ibfk_1", "rank_history", type_="foreignkey")
    op.drop_constraint("favourite_beatmapset_ibfk_2", "favourite_beatmapset", type_="foreignkey")
    op.drop_constraint("favourite_beatmapset_ibfk_1", "favourite_beatmapset", type_="foreignkey")
    op.drop_constraint("beatmap_ratings_ibfk_2", "beatmap_ratings", type_="foreignkey")
    op.drop_constraint("beatmap_ratings_ibfk_1", "beatmap_ratings", type_="foreignkey")
    op.drop_constraint("lazer_user_achievements_ibfk_1", "lazer_user_achievements", type_="foreignkey")

    # ---- lazer_user_statistics ----
    op.drop_index("ix_lazer_user_statistics_mode_pp", table_name="lazer_user_statistics")
    op.drop_constraint("pk_lazer_user_statistics", "lazer_user_statistics", type_="primary")
    op.add_column("lazer_user_statistics", sa.Column("id", mysql.INTEGER(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE lazer_user_statistics SET id = (@row_id := @row_id + 1) ORDER BY user_id, mode")
    op.create_primary_key("PRIMARY", "lazer_user_statistics", ["id"])
    op.alter_column("lazer_user_statistics", "id", existing_type=mysql.INTEGER(), autoincrement=True)
    op.create_index("ix_lazer_user_statistics_mode", "lazer_user_statistics", ["mode"], unique=False)
    op.create_index("ix_lazer_user_statistics_user_id", "lazer_user_statistics", ["user_id"], unique=False)
    op.create_foreign_key("lazer_user_statistics_ibfk_1", "lazer_user_statistics", "lazer_users", ["user_id"], ["id"])

    # ---- room_participated_users ----
    op.drop_index("ix_room_participated_users_room_left_at", table_name="room_participated_users")
    op.drop_constraint("pk_room_participated_users", "room_participated_users", type_="primary")
    op.add_column("room_participated_users", sa.Column("id", mysql.BIGINT(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE room_participated_users SET id = (@row_id := @row_id + 1) ORDER BY room_id, user_id")
    op.create_primary_key("PRIMARY", "room_participated_users", ["id"])
    op.alter_column("room_participated_users", "id", existing_type=mysql.BIGINT(), autoincrement=True)
    op.create_index("room_participated_users_ibfk_2", "room_participated_users", ["user_id"], unique=False)
    op.create_index("room_participated_users_ibfk_1", "room_participated_users", ["room_id"], unique=False)
    op.create_foreign_key("room_participated_users_ibfk_1", "room_participated_users", "rooms", ["room_id"], ["id"])
    op.create_foreign_key(
        "room_participated_users_ibfk_2", "room_participated_users", "lazer_users", ["user_id"], ["id"]
    )

    # ---- relationship ----
    op.drop_constraint("pk_relationship", "relationship", type_="primary")
    op.add_column("relationship", sa.Column("id", mysql.BIGINT(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE relationship SET id = (@row_id := @row_id + 1) ORDER BY user_id, target_id")
    op.create_primary_key("PRIMARY", "relationship", ["id"])
    op.alter_column("relationship", "id", existing_type=mysql.BIGINT(), autoincrement=True)
    op.create_index("ix_relationship_user_id", "relationship", ["user_id"], unique=False)
    op.create_index("ix_relationship_target_id", "relationship", ["target_id"], unique=False)
    op.create_foreign_key("relationship_ibfk_1", "relationship", "lazer_users", ["target_id"], ["id"])
    op.create_foreign_key("relationship_ibfk_2", "relationship", "lazer_users", ["user_id"], ["id"])

    # ---- rank_history ----
    op.drop_index("ix_rank_history_user_date", "rank_history")
    op.drop_constraint("pk_rank_history", "rank_history", type_="primary")
    op.add_column("rank_history", sa.Column("id", mysql.BIGINT(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE rank_history SET id = (@row_id := @row_id + 1) ORDER BY user_id, mode, date")
    op.create_primary_key("PRIMARY", "rank_history", ["id"])
    op.alter_column("rank_history", "id", existing_type=mysql.BIGINT(), autoincrement=True)
    op.create_index("ix_rank_history_user_id", "rank_history", ["user_id"], unique=False)
    op.create_index("ix_rank_history_date", "rank_history", ["date"], unique=False)
    op.create_foreign_key("rank_history_ibfk_1", "rank_history", "lazer_users", ["user_id"], ["id"])

    # ---- favourite_beatmapset ----
    op.drop_constraint("pk_favourite_beatmapset", "favourite_beatmapset", type_="primary")
    op.add_column("favourite_beatmapset", sa.Column("id", mysql.BIGINT(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE favourite_beatmapset SET id = (@row_id := @row_id + 1) ORDER BY user_id, beatmapset_id")
    op.create_primary_key("PRIMARY", "favourite_beatmapset", ["id"])
    op.alter_column("favourite_beatmapset", "id", existing_type=mysql.BIGINT(), autoincrement=True)
    op.create_index("ix_favourite_beatmapset_user_id", "favourite_beatmapset", ["user_id"], unique=False)
    op.create_index("ix_favourite_beatmapset_beatmapset_id", "favourite_beatmapset", ["beatmapset_id"], unique=False)
    op.create_foreign_key(
        "favourite_beatmapset_ibfk_1", "favourite_beatmapset", "beatmapsets", ["beatmapset_id"], ["id"]
    )
    op.create_foreign_key("favourite_beatmapset_ibfk_2", "favourite_beatmapset", "lazer_users", ["user_id"], ["id"])

    # ---- beatmap_ratings ----
    op.drop_constraint("pk_beatmap_ratings", "beatmap_ratings", type_="primary")
    op.add_column("beatmap_ratings", sa.Column("id", mysql.BIGINT(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE beatmap_ratings SET id = (@row_id := @row_id + 1) ORDER BY beatmapset_id, user_id")
    op.create_primary_key("PRIMARY", "beatmap_ratings", ["id"])
    op.alter_column("beatmap_ratings", "id", existing_type=mysql.BIGINT(), autoincrement=True)
    op.create_index("ix_beatmap_ratings_user_id", "beatmap_ratings", ["user_id"], unique=False)
    op.create_index("ix_beatmap_ratings_beatmapset_id", "beatmap_ratings", ["beatmapset_id"], unique=False)
    op.create_foreign_key("beatmap_ratings_ibfk_1", "beatmap_ratings", "beatmapsets", ["beatmapset_id"], ["id"])
    op.create_foreign_key("beatmap_ratings_ibfk_2", "beatmap_ratings", "lazer_users", ["user_id"], ["id"])

    # ---- lazer_user_achievements ----
    op.drop_constraint("pk_lazer_user_achievements", "lazer_user_achievements", type_="primary")
    op.add_column("lazer_user_achievements", sa.Column("id", mysql.INTEGER(), nullable=False))
    op.execute("SET @row_id := 0")
    op.execute("UPDATE lazer_user_achievements SET id = (@row_id := @row_id + 1) ORDER BY user_id, achievement_id")
    op.create_primary_key("PRIMARY", "lazer_user_achievements", ["id"])
    op.alter_column("lazer_user_achievements", "id", existing_type=mysql.INTEGER(), autoincrement=True)
    op.create_index("ix_lazer_user_achievements_id", "lazer_user_achievements", ["id"], unique=False)
    op.create_index(
        "ix_lazer_user_achievements_achievement_id", "lazer_user_achievements", ["achievement_id"], unique=False
    )
    op.create_unique_constraint("uq_user_achievement", "lazer_user_achievements", ["user_id", "achievement_id"])
    op.create_foreign_key(
        "lazer_user_achievements_ibfk_1", "lazer_user_achievements", "lazer_users", ["user_id"], ["id"]
    )
