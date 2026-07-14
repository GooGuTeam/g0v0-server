"""playlist: remove db_id, use id as primary key

Revision ID: 27eb30853d3d
Revises: bd9fa635a476
Create Date: 2026-07-14 08:50:45.663750

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27eb30853d3d"
down_revision: str | Sequence[str] | None = "bd9fa635a476"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f("ix_room_playlists_id"), table_name="room_playlists")
    op.drop_index(op.f("ix_room_playlists_db_id"), table_name="room_playlists")
    op.execute(
        """
        ALTER TABLE room_playlists
        DROP PRIMARY KEY,
        DROP COLUMN id,
        CHANGE COLUMN db_id id BIGINT NOT NULL AUTO_INCREMENT,
        ADD PRIMARY KEY (id),
        ADD INDEX ix_room_playlists_id (id)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_room_playlists_id"), table_name="room_playlists")
    op.execute(
        """
        ALTER TABLE room_playlists
        DROP PRIMARY KEY,
        CHANGE COLUMN id db_id INT NOT NULL AUTO_INCREMENT,
        ADD PRIMARY KEY (db_id),
        ADD COLUMN id INT NULL
        """
    )
    op.execute("UPDATE room_playlists SET id = db_id")
    op.execute(
        """
        ALTER TABLE room_playlists
        MODIFY COLUMN id INT NOT NULL,
        ADD INDEX ix_room_playlists_db_id (db_id),
        ADD INDEX ix_room_playlists_id (id)
        """
    )
