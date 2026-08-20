"""feat(db): change user_id columns from bigint to int

Revision ID: 57a4930b6961
Revises: 2b1d99b84fbe
Create Date: 2026-08-20 19:01:21.678633

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "57a4930b6961"
down_revision: str | Sequence[str] | None = "2b1d99b84fbe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, fk_name) — user id columns referencing lazer_users.id
USER_FK_COLUMNS: list[tuple[str, str, str]] = [
    ("beatmap_playcounts", "user_id", "beatmap_playcounts_ibfk_2"),
    ("beatmap_ratings", "user_id", "beatmap_ratings_ibfk_2"),
    ("best_scores", "user_id", "best_scores_ibfk_3"),
    ("chat_messages", "sender_id", "chat_messages_ibfk_2"),
    ("chat_silence_users", "user_id", "chat_silence_users_ibfk_2"),
    ("daily_challenge_stats", "user_id", "daily_challenge_stats_ibfk_1"),
    ("email_verifications", "user_id", "email_verifications_ibfk_1"),
    ("favourite_beatmapset", "user_id", "favourite_beatmapset_ibfk_2"),
    ("item_attempts_count", "user_id", "item_attempts_count_ibfk_2"),
    ("lazer_user_achievements", "user_id", "lazer_user_achievements_ibfk_1"),
    ("lazer_user_statistics", "user_id", "lazer_user_statistics_ibfk_1"),
    ("login_sessions", "user_id", "login_sessions_ibfk_1"),
    ("login_sessions", "user_id", "login_sessions_ibfk_3"),
    ("matchmaking_user_stats", "user_id", "matchmaking_user_stats_ibfk_1"),
    ("monthly_playcounts", "user_id", "monthly_playcounts_ibfk_1"),
    ("multiplayer_events", "user_id", "multiplayer_events_ibfk_2"),
    ("oauth_clients", "owner_id", "oauth_clients_ibfk_1"),
    ("oauth_tokens", "user_id", "oauth_tokens_ibfk_1"),
    ("password_resets", "user_id", "password_resets_ibfk_1"),
    ("rank_history", "user_id", "rank_history_ibfk_1"),
    ("rank_top", "user_id", "rank_top_ibfk_1"),
    ("relationship", "target_id", "relationship_ibfk_1"),
    ("relationship", "user_id", "relationship_ibfk_2"),
    ("replays_watched_counts", "user_id", "replays_watched_counts_ibfk_1"),
    ("room_participated_users", "user_id", "room_participated_users_ibfk_2"),
    ("room_playlists", "owner_id", "room_playlists_ibfk_2"),
    ("rooms", "host_id", "rooms_ibfk_1"),
    ("score_tokens", "user_id", "score_tokens_ibfk_2"),
    ("scores", "user_id", "scores_ibfk_2"),
    ("team_members", "user_id", "team_members_ibfk_2"),
    ("team_requests", "user_id", "team_requests_ibfk_2"),
    ("teams", "leader_id", "teams_ibfk_1"),
    ("total_score_best_scores", "user_id", "total_score_best_scores_ibfk_3"),
    ("totp_keys", "user_id", "totp_keys_ibfk_1"),
    ("trusted_devices", "user_id", "trusted_devices_ibfk_1"),
    ("user_account_history", "user_id", "user_account_history_ibfk_1"),
    ("user_events", "user_id", "user_events_ibfk_1"),
    ("user_notifications", "user_id", "user_notifications_ibfk_2"),
    ("userpreference", "user_id", "userpreference_ibfk_1"),
    ("v1_api_keys", "owner_id", "v1_api_keys_ibfk_1"),
]

# Nullability per (table, column)
NULLABLE: dict[tuple[str, str], bool] = {
    ("beatmap_playcounts", "user_id"): True,
    ("beatmap_ratings", "user_id"): True,
    ("best_scores", "user_id"): True,
    ("chat_messages", "sender_id"): True,
    ("chat_silence_users", "user_id"): True,
    ("daily_challenge_stats", "user_id"): False,
    ("email_verifications", "user_id"): False,
    ("favourite_beatmapset", "user_id"): True,
    ("item_attempts_count", "user_id"): True,
    ("lazer_user_achievements", "user_id"): True,
    ("lazer_user_statistics", "user_id"): True,
    ("login_sessions", "user_id"): False,
    ("matchmaking_user_stats", "user_id"): False,
    ("monthly_playcounts", "user_id"): True,
    ("multiplayer_events", "user_id"): True,
    ("oauth_clients", "owner_id"): True,
    ("oauth_tokens", "user_id"): True,
    ("password_resets", "user_id"): False,
    ("rank_history", "user_id"): True,
    ("rank_top", "user_id"): True,
    ("relationship", "target_id"): True,
    ("relationship", "user_id"): True,
    ("replays_watched_counts", "user_id"): True,
    ("room_participated_users", "user_id"): False,
    ("room_playlists", "owner_id"): True,
    ("rooms", "host_id"): True,
    ("score_tokens", "user_id"): True,
    ("scores", "user_id"): True,
    ("team_members", "user_id"): False,
    ("team_requests", "user_id"): False,
    ("teams", "leader_id"): True,
    ("total_score_best_scores", "user_id"): True,
    ("totp_keys", "user_id"): False,
    ("trusted_devices", "user_id"): False,
    ("user_account_history", "user_id"): True,
    ("user_events", "user_id"): True,
    ("user_notifications", "user_id"): True,
    ("userpreference", "user_id"): False,
    ("v1_api_keys", "owner_id"): True,
}


def upgrade() -> None:
    """Upgrade schema: bigint -> int for all user id columns."""
    # Drop all FKs referencing lazer_users.id first so the PK can be altered.
    for table, column, fk_name in USER_FK_COLUMNS:
        op.drop_constraint(fk_name, table, type_="foreignkey")

    # Alter each child table column.
    for table, column, _ in USER_FK_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=mysql.BIGINT(),
            type_=sa.Integer(),
            existing_nullable=NULLABLE[(table, column)],
        )

    # Alter lazer_users.id itself.
    op.alter_column(
        "lazer_users",
        "id",
        existing_type=mysql.BIGINT(),
        type_=sa.Integer(),
        existing_nullable=False,
        autoincrement=True,
    )

    # Recreate FKs (one per column; login_sessions had a duplicated FK).
    seen: set[tuple[str, str]] = set()
    for table, column, _ in USER_FK_COLUMNS:
        if (table, column) in seen:
            continue
        seen.add((table, column))
        if table == "userpreference":
            op.create_foreign_key(None, table, "lazer_users", [column], ["id"], ondelete="CASCADE")
        else:
            op.create_foreign_key(None, table, "lazer_users", [column], ["id"])


def downgrade() -> None:
    """Downgrade schema: int -> bigint for all user id columns."""
    # Drop recreated FKs.
    seen: set[tuple[str, str]] = set()
    for table, column, _ in USER_FK_COLUMNS:
        if (table, column) in seen:
            continue
        seen.add((table, column))
        op.drop_constraint(None, table, type_="foreignkey")

    # Alter lazer_users.id back to bigint.
    op.alter_column(
        "lazer_users",
        "id",
        existing_type=sa.Integer(),
        type_=mysql.BIGINT(),
        existing_nullable=False,
        autoincrement=True,
    )

    # Alter each child table column back to bigint.
    for table, column, _ in USER_FK_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Integer(),
            type_=mysql.BIGINT(),
            existing_nullable=NULLABLE[(table, column)],
        )

    # Restore FKs with their original names.
    for table, column, fk_name in USER_FK_COLUMNS:
        if table == "userpreference":
            op.create_foreign_key(fk_name, table, "lazer_users", [column], ["id"], ondelete="CASCADE")
        else:
            op.create_foreign_key(fk_name, table, "lazer_users", [column], ["id"])
