"""User achievement/medal database models and processing logic.

This module handles user achievements (medals) including storage, retrieval,
and the logic for processing newly unlocked achievements on score submission.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.config import settings
from app.models.achievement import MEDALS, Achievement
from app.models.model import UTCBaseModel
from app.models.notification import UserAchievementUnlock
from app.utils import utcnow

from .events import Event, EventType

from redis.asyncio import Redis
from sqlalchemy.orm import joinedload
from sqlmodel import (
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .user import User


class UserAchievementBase(SQLModel, UTCBaseModel):
    """Base fields for user achievement records."""

    achievement_id: int
    achieved_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True)))


class UserAchievement(UserAchievementBase, table=True):
    """Database table for user achievement records."""

    __tablename__: str = "lazer_user_achievements"

    id: int | None = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id")), exclude=True)
    user: "User" = Relationship(back_populates="achievement")


class UserAchievementResp(UserAchievementBase):
    """Response model for user achievements."""

    @classmethod
    def from_db(cls, db_model: UserAchievement) -> "UserAchievementResp":
        """Create response from database model."""
        return cls.model_validate(db_model)


async def process_achievements(session: AsyncSession, redis: Redis, score_id: int):
    """Process and award achievements for a score submission.

    Args:
        session: Database session.
        redis: Redis client for notifications.
        score_id: The score ID to check achievements for.
    """
    from .score import Score

    score = await session.get(Score, score_id, options=[joinedload(Score.beatmap)])
    if not score:
        return
    achieved = (
        await session.exec(select(UserAchievement.achievement_id).where(UserAchievement.user_id == score.user_id))
    ).all()
    not_achieved = {k: v for k, v in MEDALS.items() if k.id not in achieved}
    result: list[Achievement] = []
    now = utcnow()
    for k, v in not_achieved.items():
        if await v(session, score, score.beatmap):
            result.append(k)
    for r in result:
        session.add(
            UserAchievement(
                achievement_id=r.id,
                user_id=score.user_id,
                achieved_at=now,
            )
        )
        await redis.publish(
            "chat:notification",
            UserAchievementUnlock.init(r, score.user_id, score.gamemode).model_dump_json(),
        )
        event = Event(
            created_at=now,
            type=EventType.ACHIEVEMENT,
            user_id=score.user_id,
            event_payload={
                "achievement": {"slug": r.assets_id, "name": r.name},
                "user": {
                    "username": score.user.username,
                    "url": settings.web_url + "users/" + str(score.user.id),
                },
            },
        )
        session.add(event)
    await session.commit()
