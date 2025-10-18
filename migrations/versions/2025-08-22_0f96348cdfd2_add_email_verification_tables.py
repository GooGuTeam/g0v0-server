"""add email verification tables

Revision ID: 0f96348cdfd2
Revises: e96a649e18ca
Create Date: 2025-08-22 07:26:59.129564

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0f96348cdfd2"
down_revision: str | Sequence[str] | None = "e96a649e18ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 创建邮件验证表
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("verification_code", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False, default=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(255), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["lazer_users.id"]),
        sa.Index("ix_email_verifications_user_id", "user_id"),
        sa.Index("ix_email_verifications_email", "email"),
    )

    # 创建登录会话表
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("session_token", sa.String(255), nullable=False),
        sa.Column("ip_address", sa.String(255), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("country_code", sa.String(255), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_new_location", sa.Boolean(), nullable=False, default=False),
        sa.ForeignKeyConstraint(["user_id"], ["lazer_users.id"]),
        sa.Index("ix_login_sessions_user_id", "user_id"),
        sa.Index("ix_login_sessions_session_token", "session_token", unique=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("login_sessions")
    op.drop_table("email_verifications")
