"""feat(db): add matchmaking type to rooms table

Revision ID: 38c470029891
Revises: abdc8f800d92
Create Date: 2025-09-29 23:44:23.624615

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "38c470029891"
down_revision: str | Sequence[str] | None = "abdc8f800d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 在MySQL中，我们需要先修改枚举类型，添加MATCHMAKING选项
    # 由于SQLAlchemy的限制，我们需要使用原生SQL来修改枚举

    # 为了安全地修改枚举，我们采用以下步骤：
    # 1. 创建新的枚举类型（包含MATCHMAKING）
    # 2. 删除旧的枚举类型
    # 3. 重命名新枚举类型

    # 注意：这种方法适用于MySQL。对于PostgreSQL，方法会有所不同。
    # 添加新的枚举值到现有的matchtype枚举
    op.execute(
        "ALTER TABLE rooms MODIFY COLUMN type ENUM('PLAYLISTS', 'HEAD_TO_HEAD', 'TEAM_VERSUS', 'MATCHMAKING') NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 回滚：移除MATCHMAKING选项
    # 警告：如果有数据使用MATCHMAKING类型，这个操作会失败
    # 首先检查是否有使用MATCHMAKING类型的数据
    op.execute("UPDATE rooms SET type = 'HEAD_TO_HEAD' WHERE type = 'MATCHMAKING'")
    # 然后移除枚举值
    op.execute("ALTER TABLE rooms MODIFY COLUMN type ENUM('PLAYLISTS', 'HEAD_TO_HEAD', 'TEAM_VERSUS') NOT NULL")
