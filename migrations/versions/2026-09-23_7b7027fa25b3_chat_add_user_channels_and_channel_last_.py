"""chat: add user channels and channel last_message_id/moderated/uuid

Revision ID: 7b7027fa25b3
Revises: 57a4930b6961
Create Date: 2026-09-23 14:38:28.100496

Adds the per-user channel state table (counterpart of upstream ``user_channels``)
and the denormalised channel columns, then backfills both from existing data.

``chat_messages.uuid`` already exists since ``dd33d89aa2c2`` (VARCHAR(255)). It is
narrowed to VARCHAR(36) here; values longer than that are reported and truncated
first, because the ALTER would otherwise fail (or truncate silently).
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "7b7027fa25b3"
down_revision: str | Sequence[str] | None = "49dfd34240d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_LENGTH = 36
PREVIEW_ROWS = 20


def _reflected_column(table: str, column: str):
    """The reflected definition of ``table.column``, or ``None`` when it does not exist."""
    inspector = sa.inspect(op.get_bind())
    return next((col for col in inspector.get_columns(table) if col["name"] == column), None)


def _normalise_message_uuid() -> None:
    """Create ``chat_messages.uuid`` as VARCHAR(36), or narrow the existing column to it."""
    column = _reflected_column("chat_messages", "uuid")
    if column is None:
        op.add_column("chat_messages", sa.Column("uuid", sa.VARCHAR(length=UUID_LENGTH), nullable=True))
        return

    if isinstance(column["type"], sa.VARCHAR) and column["type"].length == UUID_LENGTH:
        return

    connection = op.get_bind()
    oversized = connection.execute(
        sa.text(
            "SELECT message_id, CHAR_LENGTH(uuid) AS uuid_length FROM chat_messages "
            "WHERE uuid IS NOT NULL AND CHAR_LENGTH(uuid) > :uuid_length ORDER BY message_id"
        ).bindparams(uuid_length=UUID_LENGTH)
    ).all()
    if oversized:
        preview = ", ".join(f"message {row.message_id} ({row.uuid_length} chars)" for row in oversized[:PREVIEW_ROWS])
        print(
            f"chat_messages.uuid: truncating {len(oversized)} value(s) longer than {UUID_LENGTH} characters "
            f"to {UUID_LENGTH}: {preview}" + (", ..." if len(oversized) > PREVIEW_ROWS else "")
        )
        connection.execute(
            sa.text(
                "UPDATE chat_messages SET uuid = LEFT(uuid, :uuid_length) "
                "WHERE uuid IS NOT NULL AND CHAR_LENGTH(uuid) > :uuid_length"
            ).bindparams(uuid_length=UUID_LENGTH)
        )

    op.alter_column(
        "chat_messages",
        "uuid",
        existing_type=column["type"],
        type_=sa.VARCHAR(length=UUID_LENGTH),
        existing_nullable=column["nullable"],
    )


def upgrade() -> None:
    """Upgrade schema."""
    # 1) Per-user channel state (upstream ``user_channels``).
    op.create_table(
        "chat_user_channels",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_read_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "channel_id"),
    )
    op.create_index(op.f("ix_chat_user_channels_channel_id"), "chat_user_channels", ["channel_id"], unique=False)
    op.create_index(op.f("ix_chat_user_channels_hidden"), "chat_user_channels", ["hidden"], unique=False)

    # 2) Denormalised channel columns.
    op.add_column("chat_channels", sa.Column("last_message_id", sa.Integer(), nullable=True))
    op.add_column("chat_channels", sa.Column("moderated", sa.Boolean(), nullable=False, server_default=sa.text("0")))

    # 3) Message uuid (VARCHAR(36), matching upstream).
    _normalise_message_uuid()

    # 4) Backfill last_message_id so existing channels already point at their newest message.
    op.execute(
        """
        UPDATE chat_channels AS c
        JOIN (
            SELECT channel_id, MAX(message_id) AS message_id
            FROM chat_messages
            GROUP BY channel_id
        ) AS m ON m.channel_id = c.channel_id
        SET c.last_message_id = m.message_id
        """
    )

    # 5) Backfill membership rows for existing PM channels (name = pm_<user1>_<user2>),
    #    marking them as read up to the channel's newest message.
    op.execute(
        """
        INSERT IGNORE INTO chat_user_channels (user_id, channel_id, hidden, last_read_id)
        SELECT CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(c.name, '_', 2), '_', -1) AS UNSIGNED), c.channel_id, 0,
               c.last_message_id
        FROM chat_channels AS c
        WHERE c.type = 'PM' AND c.name REGEXP '^pm_[0-9]+_[0-9]+$'
        UNION
        SELECT CAST(SUBSTRING_INDEX(c.name, '_', -1) AS UNSIGNED), c.channel_id, 0, c.last_message_id
        FROM chat_channels AS c
        WHERE c.type = 'PM' AND c.name REGEXP '^pm_[0-9]+_[0-9]+$'
        """
    )


def downgrade() -> None:
    """Downgrade schema.

    ``chat_messages.uuid`` is not dropped: it predates this revision (``dd33d89aa2c2``),
    and the values truncated by ``upgrade`` cannot be restored, so only the original
    VARCHAR(255) width is given back.
    """
    op.drop_table("chat_user_channels")
    op.drop_column("chat_channels", "moderated")
    op.drop_column("chat_channels", "last_message_id")
    op.alter_column(
        "chat_messages",
        "uuid",
        existing_type=sa.VARCHAR(length=UUID_LENGTH),
        type_=sa.VARCHAR(length=255),
        existing_nullable=True,
    )
