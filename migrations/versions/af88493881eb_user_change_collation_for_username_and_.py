"""user: change collation for username and email

Revision ID: af88493881eb
Revises: 34a563187e47
Create Date: 2025-08-26 11:31:07.183273

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "af88493881eb"
down_revision: str | Sequence[str] | None = "34a563187e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. 删除现有索引
    op.drop_index("ix_lazer_users_email", table_name="lazer_users")
    op.drop_index("ix_lazer_users_username", table_name="lazer_users")

    # 2. 修改字段 collation 为 utf8mb4_general_ci
    op.execute("""
        ALTER TABLE lazer_users
        MODIFY username VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
        MODIFY email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL;
    """)

    # 3. 重新创建唯一索引（大小写不敏感）
    op.create_index(op.f("ix_lazer_users_email"), "lazer_users", ["email"], unique=True)
    op.create_index(op.f("ix_lazer_users_username"), "lazer_users", ["username"], unique=True)


def downgrade() -> None:
    # 1. 删除索引
    op.drop_index("ix_lazer_users_email", table_name="lazer_users")
    op.drop_index("ix_lazer_users_username", table_name="lazer_users")

    # 2. 恢复原 collation（假设原来是 utf8mb4_bin）
    op.execute("""
        ALTER TABLE lazer_users
        MODIFY username VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        MODIFY email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL;
    """)

    # 3. 恢复原索引
    op.create_index(op.f("ix_lazer_users_email"), "lazer_users", ["email"], unique=True)
    op.create_index(op.f("ix_lazer_users_username"), "lazer_users", ["username"], unique=True)
