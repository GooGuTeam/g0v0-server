"""fix(db): move BanchoBot to user id 3

Revision ID: 6ef25df266b2
Revises: 800ca2ea8eec
Create Date: 2026-10-26 02:34:08.476363

Select with -x banchobot_id_strategy=move|swap or BANCHOBOT_ID_STRATEGY.
The command-line argument takes precedence; the default is move (user 3 to end).
Stop writes, persist queued chat messages, and clear Redis DB 0/1 caches before
restarting after this migration. Redis DB 2 contains binary assets and is unaffected.
The mapping table makes downgrade independent of configuration and later registrations.
"""

from collections.abc import Sequence
import json
import os

from app.path import CONFIG_DIR

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.engine import Connection

revision: str = "6ef25df266b2"
down_revision: str | Sequence[str] | None = "800ca2ea8eec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAPPING_TABLE = "banchobot_id_migration"
PENDING_REPLAYS = CONFIG_DIR / "new_user_id"
# These references are written by local voting, chat, notification, screenshot,
# and login handlers. They lack FKs but refer to local lazer_users accounts.
# beatmaps.user_id, beatmapsets.user_id, and user IDs inside fetched beatmapset
# nomination/description JSON come from osu! API responses and must stay unchanged.
LOCAL_REFERENCES = {
    ("beatmap_tags", "user_id"),
    ("chat_user_channels", "user_id"),
    ("notifications", "source_user_id"),
    ("screenshots", "user_id"),
    ("user_login_log", "user_id"),
}


def _check_pending_replays() -> None:
    if PENDING_REPLAYS.exists():
        raise RuntimeError(
            "Finish the pending replay migration by starting the application before changing user IDs again."
        )


def _queue_replays(mapping: dict[int, int]) -> None:
    if not mapping:
        return
    PENDING_REPLAYS.parent.mkdir(parents=True, exist_ok=True)
    temporary = PENDING_REPLAYS.with_name("new_user_id.tmp")
    try:
        temporary.write_text(json.dumps(mapping) + "\n", encoding="utf-8")
        temporary.replace(PENDING_REPLAYS)
    finally:
        temporary.unlink(missing_ok=True)


def _connection() -> Connection:
    if context.is_offline_mode():
        raise RuntimeError("The BanchoBot ID migration requires an online MySQL connection.")
    connection = op.get_bind()
    if connection.dialect.name != "mysql":
        raise RuntimeError("The BanchoBot ID migration requires MySQL.")
    return connection


def _next_id(connection: Connection) -> int:
    max_id = connection.execute(sa.text("SELECT COALESCE(MAX(id), 3) FROM lazer_users")).scalar_one()
    auto_increment = connection.execute(
        sa.text("""
            SELECT AUTO_INCREMENT FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lazer_users'
        """)
    ).scalar_one()
    next_id = max(4, max_id + 1, auto_increment or 1)
    if next_id >= 2**31 - 1:
        raise RuntimeError("No spare signed INT user ID is available for the BanchoBot migration.")
    return next_id


def _references(connection: Connection) -> set[tuple[str, str]]:
    # Inspect the live schema so installed plugins' foreign keys are covered too.
    references = set(
        connection.execute(
            sa.text("""
                SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE
                WHERE REFERENCED_TABLE_SCHEMA = DATABASE()
                  AND TABLE_SCHEMA = DATABASE()
                  AND REFERENCED_TABLE_NAME = 'lazer_users' AND REFERENCED_COLUMN_NAME = 'id'
            """)
        ).all()
    )
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    references.update((table, column) for table, column in LOCAL_REFERENCES if table in tables)
    return references | {("lazer_users", "id")}


def _move(connection: Connection, references: set[tuple[str, str]], old: int, new: int) -> None:
    for table, column in sorted(references):
        target = sa.table(table, sa.column(column, sa.Integer()))
        connection.execute(target.update().where(target.c[column] == old).values({column: new}))
    # PM channel names encode both participants. Replace complete numeric IDs only.
    connection.execute(
        sa.text("""
            UPDATE chat_channels
            SET name = CONCAT('pm_',
                IF(SUBSTRING_INDEX(SUBSTRING_INDEX(name, '_', 2), '_', -1) = :old,
                   :new, SUBSTRING_INDEX(SUBSTRING_INDEX(name, '_', 2), '_', -1)), '_',
                IF(SUBSTRING_INDEX(name, '_', -1) = :old, :new, SUBSTRING_INDEX(name, '_', -1)))
            WHERE type = 'PM' AND name REGEXP '^pm_[0-9]+_[0-9]+$'
        """),
        {"old": old, "new": new},
    )
    connection.execute(
        sa.text("""
            UPDATE notifications SET details = JSON_SET(details, '$.user_id', :new)
            WHERE JSON_EXTRACT(details, '$.user_id') = :old
        """),
        {"old": old, "new": new},
    )
    # Activity events embed a local profile URL, separate from their user_id FK.
    connection.execute(
        sa.text("""
            UPDATE user_events SET event_payload = JSON_SET(event_payload, '$.user.url',
                CONCAT(LEFT(JSON_UNQUOTE(JSON_EXTRACT(event_payload, '$.user.url')),
                            CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(event_payload, '$.user.url'))) - :length), :new))
            WHERE JSON_UNQUOTE(JSON_EXTRACT(event_payload, '$.user.url')) REGEXP :pattern
        """),
        {"new": str(new), "length": len(str(old)), "pattern": rf"/users/{old}$"},
    )


def _remap(connection: Connection, mapping: dict[int, int]) -> None:
    if not mapping:
        return
    references = _references(connection)
    # Stage every source at an unused ID to avoid collisions in composite PKs,
    # unique indexes, and reciprocal relationships during swaps.
    first_spare = max(_next_id(connection), *mapping.values()) + 1
    if first_spare + len(mapping) > 2**31 - 1:
        raise RuntimeError("No spare signed INT user IDs are available for staging.")
    checks = connection.execute(sa.text("SELECT @@SESSION.FOREIGN_KEY_CHECKS")).scalar_one()
    connection.execute(sa.text("SET SESSION FOREIGN_KEY_CHECKS = 0"))
    try:
        # All data updates roll back together on failure, even with MySQL's
        # non-transactional Alembic DDL configuration.
        with connection.begin_nested():
            staged = {}
            for offset, (old, new) in enumerate(mapping.items()):
                temporary = first_spare + offset
                _move(connection, references, old, temporary)
                staged[temporary] = new
            for temporary, new in staged.items():
                _move(connection, references, temporary, new)
    finally:
        connection.execute(sa.text("SET SESSION FOREIGN_KEY_CHECKS = :checks"), {"checks": checks})


def upgrade() -> None:
    connection = _connection()
    _check_pending_replays()
    strategy = (
        context.get_x_argument(as_dictionary=True)
        .get("banchobot_id_strategy", os.environ.get("BANCHOBOT_ID_STRATEGY", "move"))
        .strip()
        .lower()
    )
    if strategy not in {"move", "swap"}:
        raise ValueError("banchobot_id_strategy must be 'move' (default) or 'swap'.")
    users = {
        row.id: row
        for row in connection.execute(sa.text("SELECT id, username, is_bot FROM lazer_users WHERE id IN (2, 3)"))
    }
    mapping = {}
    if 3 not in users or users[3].username != "BanchoBot" or not users[3].is_bot:
        if 2 in users and (users[2].username != "BanchoBot" or not users[2].is_bot):
            raise RuntimeError("User 2 is not BanchoBot; refusing to reassign an unrelated account.")
        if 3 in users:
            mapping[3] = _next_id(connection) if strategy == "move" else 2
        if 2 in users:
            mapping[2] = 3
    op.create_table(
        MAPPING_TABLE,
        sa.Column("old_id", sa.Integer(), primary_key=True),
        sa.Column("new_id", sa.Integer(), nullable=False, unique=True),
    )
    # The DDL above commits on MySQL. Persist the mapping and the user data in
    # the same transaction so a failed data migration can be safely retried.
    try:
        with connection.begin_nested():
            for old, new in mapping.items():
                connection.execute(
                    sa.text("INSERT INTO banchobot_id_migration (old_id, new_id) VALUES (:old, :new)"),
                    {"old": old, "new": new},
                )
            _remap(connection, mapping)
            _queue_replays(mapping)
    except Exception:
        op.drop_table(MAPPING_TABLE)
        raise
    for old, new in sorted(mapping.items()):
        context.config.print_stdout("User ID mapping: %s -> %s", old, new)
    if 3 in mapping:
        context.config.print_stdout("Original user ID 3 now has user ID %s.", mapping[3])
    if mapping:
        context.config.print_stdout("Replay migration queued in %s; start the application to finish.", PENDING_REPLAYS)


def downgrade() -> None:
    connection = _connection()
    _check_pending_replays()
    mapping = dict(connection.execute(sa.text("SELECT new_id, old_id FROM banchobot_id_migration")).all())
    # A fresh installation has no bot to move during upgrade. Startup may create
    # it at ID 3 later, so restore the ID expected by the old application too.
    if (
        3 not in mapping
        and connection.execute(
            sa.text("SELECT id FROM lazer_users WHERE id = 3 AND username = 'BanchoBot' AND is_bot = 1")
        ).first()
    ):
        mapping[3] = 2
    for target in mapping.values():
        if (
            target not in mapping
            and connection.execute(sa.text("SELECT id FROM lazer_users WHERE id = :id"), {"id": target}).first()
        ):
            raise RuntimeError(f"Cannot restore user {target}: that ID is now occupied.")
    with connection.begin_nested():
        _remap(connection, mapping)
        _queue_replays(mapping)
    op.drop_table(MAPPING_TABLE)
