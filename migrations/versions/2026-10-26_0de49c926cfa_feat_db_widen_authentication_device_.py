"""feat(db): widen authentication device hashes to text

Revision ID: 0de49c926cfa
Revises: 6ef25df266b2
Create Date: 2026-10-26 20:37:12.662989

"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0de49c926cfa"
down_revision: str | Sequence[str] | None = "6ef25df266b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Only widen the two existing device credentials; preserve NULL/default semantics."""
    for table_name in ("login_sessions", "trusted_devices"):
        op.alter_column(
            table_name,
            "device_hash",
            existing_type=mysql.VARCHAR(length=64),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Preflight both tables before MySQL's first implicitly committed ALTER."""
    if context.is_offline_mode():
        raise RuntimeError("Online data checks are required before reducing device_hash to VARCHAR(64)")
    connection = op.get_bind()
    oversized = []
    for table_name in ("login_sessions", "trusted_devices"):
        table = sa.table(table_name, sa.column("device_hash"))
        count = connection.execute(
            sa.select(sa.func.count()).select_from(table).where(sa.func.char_length(table.c.device_hash) > 64)
        ).scalar_one()
        if count:
            oversized.append(f"{table_name}: {count} value(s)")
    if oversized:
        raise RuntimeError("Cannot downgrade device_hash without truncating data: " + "; ".join(oversized))
    for table_name in ("login_sessions", "trusted_devices"):
        op.alter_column(
            table_name,
            "device_hash",
            existing_type=sa.Text(),
            type_=mysql.VARCHAR(length=64),
            existing_nullable=True,
        )
