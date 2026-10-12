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


COMPOSITE_KEYS: dict[str, list[str]] = {
    "lazer_user_achievements": ["user_id", "achievement_id"],
    "beatmap_ratings": ["beatmapset_id", "user_id"],
    "favourite_beatmapset": ["user_id", "beatmapset_id"],
    "rank_history": ["user_id", "mode", "date"],
    "relationship": ["user_id", "target_id", "type"],
    "room_participated_users": ["room_id", "user_id"],
    "lazer_user_statistics": ["user_id", "mode"],
}


def _constraint_exists(table_name: str, name: str) -> bool:
    """Check whether a constraint exists on the table."""
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND CONSTRAINT_NAME = :name"
            ),
            {"table": table_name, "name": name},
        )
        .first()
    )
    return row is not None


def _index_exists(table_name: str, name: str) -> bool:
    """Check whether an index exists on the table."""
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND INDEX_NAME = :name"
            ),
            {"table": table_name, "name": name},
        )
        .first()
    )
    return row is not None


def _column_exists(table_name: str, name: str) -> bool:
    """Check whether a column exists on the table."""
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :name"
            ),
            {"table": table_name, "name": name},
        )
        .first()
    )
    return row is not None


def _drop_table_fks(table_name: str) -> None:
    """Drop every FK on the table, whatever its generated name is.

    MySQL auto-generated constraint names differ between fresh installs and
    databases with migration history. The FKs are recreated with canonical
    names in the per-table sections below.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
            "AND REFERENCED_TABLE_NAME IS NOT NULL"
        ),
        {"table": table_name},
    ).fetchall()
    for (constraint_name,) in rows:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def _drop_constraint_if_exists(name: str, table_name: str, type_: str) -> None:
    if _constraint_exists(table_name, name):
        op.drop_constraint(name, table_name, type_=type_)
    else:
        print(f"49dfd34240d0: skip missing {type_} constraint {name!r} on {table_name!r}")


def _drop_index_if_exists(name: str, table_name: str) -> None:
    if _index_exists(table_name, name):
        op.drop_index(name, table_name=table_name)
    else:
        print(f"49dfd34240d0: skip missing index {name!r} on {table_name!r}")


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)
    else:
        print(f"49dfd34240d0: skip missing column {column_name!r} on {table_name!r}")


def _prepare_composite_key(table_name: str, key_columns: list[str]) -> None:
    """Make the table ready for its composite primary key.

    Rows that cannot exist in a primary key (NULL key columns) are removed, and
    rows duplicating the key collapse onto their earliest id - legacy databases
    written before the surrogate id was introduced may contain both.
    """
    null_predicates = " OR ".join(f"`{column}` IS NULL" for column in key_columns)
    op.execute(f"DELETE FROM `{table_name}` WHERE {null_predicates}")  # noqa: S608

    if not _column_exists(table_name, "id"):
        return
    keys = ", ".join(f"`{column}`" for column in key_columns)
    join_predicates = " AND ".join(f"t.`{column}` = d.`{column}`" for column in key_columns)
    op.execute(
        f"DELETE t FROM `{table_name}` t "  # noqa: S608
        f"JOIN (SELECT {keys}, MIN(id) AS keep_id FROM `{table_name}` "
        f"GROUP BY {keys} HAVING COUNT(*) > 1) d "
        f"ON {join_predicates} "
        f"WHERE t.id <> d.keep_id"
    )


def _drop_surrogate_id(table_name: str, existing_type: object) -> None:
    """Drop the surrogate ``id`` column together with its PK when present."""
    if not _column_exists(table_name, "id"):
        return
    op.alter_column(table_name, "id", existing_type=existing_type, existing_nullable=False, autoincrement=False)
    _drop_constraint_if_exists("PRIMARY", table_name, "primary")
    _drop_column_if_exists(table_name, "id")


def _remove_orphans(table_name: str, column_name: str, ref_table: str, ref_column: str = "id") -> None:
    """Resolve rows whose reference no longer exists before the FK is recreated.

    Columns that allow NULL keep their rows with the reference cleared; NOT NULL
    columns cannot hold such rows, so those rows are removed.
    """
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column"
            ),
            {"table": table_name, "column": column_name},
        )
        .first()
    )
    nullable = bool(row) and row[0] == "YES"
    if nullable:
        op.execute(
            f"UPDATE `{table_name}` t LEFT JOIN `{ref_table}` r ON t.`{column_name}` = r.`{ref_column}` "  # noqa: S608
            f"SET t.`{column_name}` = NULL "
            f"WHERE t.`{column_name}` IS NOT NULL AND r.`{ref_column}` IS NULL"
        )
    else:
        op.execute(
            f"DELETE t FROM `{table_name}` t LEFT JOIN `{ref_table}` r ON t.`{column_name}` = r.`{ref_column}` "
            f"WHERE t.`{column_name}` IS NOT NULL AND r.`{ref_column}` IS NULL"
        )


def upgrade() -> None:
    """Upgrade schema: replace surrogate id PKs with composite PKs."""
    # Drop every FK on the affected tables first (MySQL requires no FK on the
    # indexes being dropped); they are recreated canonically per table below.
    for table in COMPOSITE_KEYS:
        _drop_table_fks(table)

    # Legacy data may violate the composite keys; clean it up before use.
    for table, key_columns in COMPOSITE_KEYS.items():
        _prepare_composite_key(table, key_columns)

    # Legacy rows may reference rows that no longer exist; resolve them before
    # the FKs are recreated, or the validation would fail.
    for table, column, ref_table in [
        ("lazer_user_achievements", "user_id", "lazer_users"),
        ("beatmap_ratings", "beatmapset_id", "beatmapsets"),
        ("beatmap_ratings", "user_id", "lazer_users"),
        ("favourite_beatmapset", "user_id", "lazer_users"),
        ("favourite_beatmapset", "beatmapset_id", "beatmapsets"),
        ("rank_history", "user_id", "lazer_users"),
        ("relationship", "target_id", "lazer_users"),
        ("relationship", "user_id", "lazer_users"),
        ("room_participated_users", "room_id", "rooms"),
        ("room_participated_users", "user_id", "lazer_users"),
        ("lazer_user_statistics", "user_id", "lazer_users"),
    ]:
        _remove_orphans(table, column, ref_table)

    # ---- lazer_user_achievements: PK (user_id, achievement_id) ----
    _drop_constraint_if_exists("uq_user_achievement", "lazer_user_achievements", "unique")
    _drop_index_if_exists("ix_lazer_user_achievements_achievement_id", "lazer_user_achievements")
    _drop_index_if_exists("ix_lazer_user_achievements_id", "lazer_user_achievements")
    _drop_surrogate_id("lazer_user_achievements", sa.Integer())
    op.alter_column("lazer_user_achievements", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("lazer_user_achievements", "achievement_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_lazer_user_achievements", "lazer_user_achievements", ["user_id", "achievement_id"])
    op.create_foreign_key(
        "lazer_user_achievements_ibfk_1", "lazer_user_achievements", "lazer_users", ["user_id"], ["id"]
    )

    # ---- beatmap_ratings: PK (beatmapset_id, user_id) ----
    _drop_index_if_exists("ix_beatmap_ratings_beatmapset_id", "beatmap_ratings")
    _drop_index_if_exists("ix_beatmap_ratings_user_id", "beatmap_ratings")
    _drop_surrogate_id("beatmap_ratings", mysql.BIGINT())
    op.alter_column("beatmap_ratings", "user_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_beatmap_ratings", "beatmap_ratings", ["beatmapset_id", "user_id"])
    op.create_foreign_key("beatmap_ratings_ibfk_1", "beatmap_ratings", "beatmapsets", ["beatmapset_id"], ["id"])
    op.create_foreign_key("beatmap_ratings_ibfk_2", "beatmap_ratings", "lazer_users", ["user_id"], ["id"])

    # ---- favourite_beatmapset: PK (user_id, beatmapset_id) ----
    _drop_index_if_exists("ix_favourite_beatmapset_beatmapset_id", "favourite_beatmapset")
    _drop_index_if_exists("ix_favourite_beatmapset_user_id", "favourite_beatmapset")
    _drop_surrogate_id("favourite_beatmapset", mysql.BIGINT())
    op.alter_column("favourite_beatmapset", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("favourite_beatmapset", "beatmapset_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_favourite_beatmapset", "favourite_beatmapset", ["user_id", "beatmapset_id"])
    op.create_foreign_key(
        "favourite_beatmapset_ibfk_1", "favourite_beatmapset", "beatmapsets", ["beatmapset_id"], ["id"]
    )
    op.create_foreign_key("favourite_beatmapset_ibfk_2", "favourite_beatmapset", "lazer_users", ["user_id"], ["id"])

    # ---- rank_history: PK (user_id, mode, date) ----
    _drop_index_if_exists("ix_rank_history_date", "rank_history")
    _drop_index_if_exists("ix_rank_history_user_id", "rank_history")
    _drop_surrogate_id("rank_history", mysql.BIGINT())
    op.alter_column("rank_history", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("rank_history", "date", existing_type=sa.Date(), nullable=False)
    op.create_primary_key("pk_rank_history", "rank_history", ["user_id", "mode", "date"])
    op.create_index("ix_rank_history_user_date", "rank_history", ["user_id", "date"], unique=False)
    op.create_foreign_key("rank_history_ibfk_1", "rank_history", "lazer_users", ["user_id"], ["id"])

    # ---- relationship: PK (user_id, target_id, type) ----
    _drop_index_if_exists("ix_relationship_target_id", "relationship")
    _drop_index_if_exists("ix_relationship_user_id", "relationship")
    _drop_surrogate_id("relationship", mysql.BIGINT())
    op.alter_column("relationship", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("relationship", "target_id", existing_type=sa.Integer(), nullable=False)
    op.create_primary_key("pk_relationship", "relationship", ["user_id", "target_id", "type"])
    op.create_foreign_key("relationship_ibfk_1", "relationship", "lazer_users", ["target_id"], ["id"])
    op.create_foreign_key("relationship_ibfk_2", "relationship", "lazer_users", ["user_id"], ["id"])

    # ---- room_participated_users: PK (room_id, user_id) ----
    # NOTE: MySQL drops the index it auto-created for an FK together with the
    # FK, so the FK-named supporting indexes may or may not still exist here.
    _drop_index_if_exists("room_participated_users_ibfk_1", "room_participated_users")
    _drop_index_if_exists("room_participated_users_ibfk_2", "room_participated_users")
    _drop_surrogate_id("room_participated_users", mysql.BIGINT())
    op.create_primary_key("pk_room_participated_users", "room_participated_users", ["room_id", "user_id"])
    op.create_index(
        "ix_room_participated_users_room_left_at", "room_participated_users", ["room_id", "left_at"], unique=False
    )
    op.create_foreign_key("room_participated_users_ibfk_1", "room_participated_users", "rooms", ["room_id"], ["id"])
    op.create_foreign_key(
        "room_participated_users_ibfk_2", "room_participated_users", "lazer_users", ["user_id"], ["id"]
    )

    # ---- lazer_user_statistics: PK (user_id, mode) ----
    _drop_index_if_exists("ix_lazer_user_statistics_user_id", "lazer_user_statistics")
    _drop_index_if_exists("ix_lazer_user_statistics_mode", "lazer_user_statistics")
    _drop_surrogate_id("lazer_user_statistics", sa.Integer())
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
