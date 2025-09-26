"""feat(db): add device tracking fields to oauth_tokens

Revision ID: device_tracking_oauth_tokens
Revises:
Create Date: 2025-09-26 10:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "device_tracking_oauth_tokens"
down_revision = "9419272e4c85"  # Point to the latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Add device tracking fields to oauth_tokens table
    op.add_column("oauth_tokens", sa.Column("device_fingerprint", sa.String(length=32), nullable=True))
    op.add_column("oauth_tokens", sa.Column("device_type", sa.String(length=20), nullable=True))
    op.add_column("oauth_tokens", sa.Column("user_agent", sa.String(length=500), nullable=True))
    op.add_column("oauth_tokens", sa.Column("ip_address", sa.String(length=45), nullable=True))
    op.add_column("oauth_tokens", sa.Column("last_used_at", sa.DateTime(), nullable=True))

    # Add indexes for better performance
    op.create_index("ix_oauth_tokens_device_fingerprint", "oauth_tokens", ["device_fingerprint"])
    op.create_index("ix_oauth_tokens_device_type", "oauth_tokens", ["device_type"])


def downgrade():
    # Remove indexes
    op.drop_index("ix_oauth_tokens_device_type", "oauth_tokens")
    op.drop_index("ix_oauth_tokens_device_fingerprint", "oauth_tokens")

    # Remove columns
    op.drop_column("oauth_tokens", "last_used_at")
    op.drop_column("oauth_tokens", "ip_address")
    op.drop_column("oauth_tokens", "user_agent")
    op.drop_column("oauth_tokens", "device_type")
    op.drop_column("oauth_tokens", "device_fingerprint")
