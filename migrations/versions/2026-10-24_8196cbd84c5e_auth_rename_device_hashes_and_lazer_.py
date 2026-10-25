"""auth: rename device hashes and lazer client type

Revision ID: 8196cbd84c5e
Revises: 7b7027fa25b3
Create Date: 2026-10-24 07:40:51.466037

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8196cbd84c5e"
down_revision: str | Sequence[str] | None = "7b7027fa25b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in ("login_sessions", "trusted_devices"):
        op.alter_column(
            table_name,
            "web_uuid",
            new_column_name="device_hash",
            existing_type=sa.VARCHAR(length=36),
            type_=sa.VARCHAR(length=64),
            existing_nullable=True,
        )

    op.execute(sa.text("UPDATE trusted_devices SET client_type = 'lazer' WHERE client_type = 'client'"))


def downgrade() -> None:
    """Downgrade schema."""
    # MySQL DDL commits implicitly; check both tables before narrowing either column.
    if op.get_context().as_sql:
        raise RuntimeError("Downgrade requires an online length check to preserve device_hash values.")

    connection = op.get_bind()
    for table_name in ("login_sessions", "trusted_devices"):
        table = sa.table(table_name, sa.column("device_hash", sa.VARCHAR(length=64)))
        oversized = connection.scalar(
            sa.select(sa.literal(1)).where(sa.func.char_length(table.c.device_hash) > 36).limit(1)
        )
        if oversized is not None:
            raise ValueError(f"Cannot downgrade {table_name}: device_hash values longer than 36 characters exist.")

    for table_name in ("login_sessions", "trusted_devices"):
        op.alter_column(
            table_name,
            "device_hash",
            new_column_name="web_uuid",
            existing_type=sa.VARCHAR(length=64),
            type_=sa.VARCHAR(length=36),
            existing_nullable=True,
        )

    op.execute(sa.text("UPDATE trusted_devices SET client_type = 'client' WHERE client_type = 'lazer'"))
