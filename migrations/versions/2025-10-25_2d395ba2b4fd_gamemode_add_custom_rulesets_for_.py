"""gamemode: add custom rulesets for sentakki, tau, rush, hishigata & soyokaze

Revision ID: 2d395ba2b4fd
Revises: ceabe941b207
Create Date: 2025-10-25 12:20:06.681929

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "2d395ba2b4fd"
down_revision: str | Sequence[str] | None = "ceabe941b207"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MODES: tuple[str, ...] = (
    "OSU",
    "TAIKO",
    "FRUITS",
    "MANIA",
    "OSURX",
    "OSUAP",
    "TAIKORX",
    "FRUITSRX",
)

CUSTOM_MODES: tuple[str, ...] = (
    "SENTAKKI",
    "TAU",
    "RUSH",
    "HISHIGATA",
    "SOYOKAZE",
)

NEW_MODES: tuple[str, ...] = OLD_MODES + CUSTOM_MODES

TARGET_COLUMNS: tuple[tuple[str, str], ...] = (
    ("lazer_users", "playmode"),
    ("lazer_users", "g0v0_playmode"),
    ("beatmaps", "mode"),
    ("lazer_user_statistics", "mode"),
    ("score_tokens", "ruleset_id"),
    ("scores", "gamemode"),
    ("best_scores", "gamemode"),
    ("total_score_best_scores", "gamemode"),
    ("rank_history", "mode"),
    ("rank_top", "mode"),
    ("teams", "playmode"),
)


def _gamemode_enum(values: tuple[str, ...]) -> mysql.ENUM:
    return mysql.ENUM(*values, name="gamemode")


def upgrade() -> None:
    """Upgrade schema."""
    for table, column in TARGET_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=_gamemode_enum(OLD_MODES),
            type_=_gamemode_enum(NEW_MODES),
        )


def downgrade() -> None:
    """Downgrade schema."""
    placeholders = ", ".join(f":mode_{index}" for index in range(len(CUSTOM_MODES)))
    mode_params = {f"mode_{index}": mode for index, mode in enumerate(CUSTOM_MODES)}

    cleanup_templates = [
        "DELETE FROM playlist_best_scores WHERE score_id IN (SELECT id FROM scores WHERE gamemode IN ({placeholders}))",
        "DELETE FROM total_score_best_scores WHERE gamemode IN ({placeholders})",
        "DELETE FROM best_scores WHERE gamemode IN ({placeholders})",
        "DELETE FROM score_tokens WHERE ruleset_id IN ({placeholders})",
        "DELETE FROM score_tokens WHERE score_id IN (SELECT id FROM scores WHERE gamemode IN ({placeholders}))",
        "DELETE FROM score_tokens WHERE beatmap_id IN (SELECT id FROM beatmaps WHERE mode IN ({placeholders}))",
        "DELETE FROM scores WHERE gamemode IN ({placeholders})",
        "DELETE FROM rank_history WHERE mode IN ({placeholders})",
        "DELETE FROM rank_top WHERE mode IN ({placeholders})",
        "DELETE FROM lazer_user_statistics WHERE mode IN ({placeholders})",
        "DELETE FROM team_requests WHERE team_id IN (SELECT id FROM teams WHERE playmode IN ({placeholders}))",
        "DELETE FROM team_members WHERE team_id IN (SELECT id FROM teams WHERE playmode IN ({placeholders}))",
        "DELETE FROM teams WHERE playmode IN ({placeholders})",
        (
            "DELETE FROM matchmaking_pool_beatmaps WHERE beatmap_id IN "
            "(SELECT id FROM beatmaps WHERE mode IN ({placeholders}))"
        ),
        "DELETE FROM beatmap_playcounts WHERE beatmap_id IN (SELECT id FROM beatmaps WHERE mode IN ({placeholders}))",
        "DELETE FROM beatmap_tags WHERE beatmap_id IN (SELECT id FROM beatmaps WHERE mode IN ({placeholders}))",
        "DELETE FROM failtime WHERE beatmap_id IN (SELECT id FROM beatmaps WHERE mode IN ({placeholders}))",
        "DELETE FROM room_playlists WHERE beatmap_id IN (SELECT id FROM beatmaps WHERE mode IN ({placeholders}))",
        "DELETE FROM banned_beatmaps WHERE beatmap_id IN (SELECT id FROM beatmaps WHERE mode IN ({placeholders}))",
        "DELETE FROM beatmaps WHERE mode IN ({placeholders})",
    ]

    for template in cleanup_templates:
        statement = template.format(placeholders=placeholders)
        op.execute(sa.text(statement), parameters=dict(mode_params))

    # Reset persisted user modes to a supported option before shrinking the enum domain.
    update_templates = [
        "UPDATE lazer_users SET g0v0_playmode = 'OSU' WHERE g0v0_playmode IN ({placeholders})",
        "UPDATE lazer_users SET playmode = 'OSU' WHERE playmode IN ({placeholders})",
    ]

    for template in update_templates:
        op.execute(sa.text(template.format(placeholders=placeholders)), parameters=dict(mode_params))

    for table, column in TARGET_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=_gamemode_enum(NEW_MODES),
            type_=_gamemode_enum(OLD_MODES),
        )
