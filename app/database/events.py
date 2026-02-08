from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models.model import UTCBaseModel
from app.utils import utcnow

from pydantic import model_serializer
from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
)

if TYPE_CHECKING:
    from .user import User


class EventType(StrEnum):
    ACHIEVEMENT = "achievement"
    BEATMAP_PLAYCOUNT = "beatmap_playcount"
    BEATMAPSET_APPROVE = "beatmapset_approve"
    BEATMAPSET_DELETE = "beatmapset_delete"
    BEATMAPSET_REVIVE = "beatmapset_revive"
    BEATMAPSET_UPDATE = "beatmapset_update"
    BEATMAPSET_UPLOAD = "beatmapset_upload"
    RANK = "rank"
    RANK_LOST = "rank_lost"
    # 鉴于本服务器没有 supporter 这一说，这三个字段没有必要
    # USER_SUPPORT_AGAIN="user_support_again"
    # USER_SUPPORT_FIRST="user_support"
    # USER_SUPPORT_GIFT="user_support_gift"
    USERNAME_CHANGE = "username_change"


class Event(UTCBaseModel, SQLModel, table=True):
    __tablename__: str = "user_events"
    id: int = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True)))
    type: EventType
    event_payload: dict = Field(exclude=True, default_factory=dict, sa_column=Column(JSON))
    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True),
    )
    user: "User" = Relationship(back_populates="events")

    @model_serializer
    def serialize(self) -> dict:
        d = {
            "id": self.id,
            "createdAt": self.created_at.replace(tzinfo=UTC).isoformat(),
            "type": self.type.value,
        }

        # 临时修复：统一成就事件格式 (TODO: 可在数据迁移完成后移除)
        if self.type == EventType.ACHIEVEMENT and "achievement" in self.event_payload:
            achievement_data = self.event_payload["achievement"]
            if "achievement_id" in achievement_data and (
                "name" not in achievement_data or "slug" not in achievement_data
            ):
                from app.models.achievement import MEDALS

                achievement_id = achievement_data["achievement_id"]
                for medal in MEDALS:
                    if medal.id == achievement_id:
                        fixed_payload = dict(self.event_payload)
                        fixed_payload["achievement"] = {"name": medal.name, "slug": medal.assets_id}
                        for k, v in fixed_payload.items():
                            d[k] = v
                        return d

            for k, v in self.event_payload.items():
                d[k] = v
        else:
            for k, v in self.event_payload.items():
                d[k] = v

        return d
