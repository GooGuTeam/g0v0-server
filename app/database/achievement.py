"""User achievement/medal database models and processing logic.

This module handles user achievements (medals) including storage, retrieval,
and the logic for processing newly unlocked achievements on score submission.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.config import settings
from app.helpers import utcnow
from app.models.achievement import MEDALS, Achievement
from app.models.model import UTCBaseModel
from app.models.notification import UserAchievementUnlock
from app.models.score import GameMode
from app.v2_ipc import IPCClient

from .events import Event, EventType

from redis.asyncio import Redis
from sqlalchemy.orm import Mapped, joinedload
from sqlmodel import Column, DateTime, Field, ForeignKey, Integer, Relationship, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .user import User


class UserAchievementBase(SQLModel, UTCBaseModel):
    """Base fields for user achievement records."""

    achievement_id: int = Field(primary_key=True)
    achieved_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True)))


class UserAchievement(UserAchievementBase, table=True):
    """Database table for user achievement records."""

    __tablename__: str = "lazer_user_achievements"

    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("lazer_users.id"), primary_key=True),
        exclude=True,
    )
    achievement_id: int = Field(primary_key=True)
    user: Mapped["User"] = Relationship(back_populates="achievement")


class UserAchievementResp(UserAchievementBase):
    """Response model for user achievements."""

    @classmethod
    def from_db(cls, db_model: UserAchievement) -> "UserAchievementResp":
        """Create response from database model."""
        return cls.model_validate(db_model)


async def unlock_achievements(
    session: AsyncSession,
    achievements: list[Achievement],
    user_id: int,
    gamemode: GameMode | None = None,
    redis: Redis | None = None,
    v2_ipc: IPCClient | None = None,
):
    from .user import User

    username = (await session.exec(select(User.username).where(User.id == user_id))).one()
    now = utcnow()
    for r in achievements:
        session.add(
            UserAchievement(
                achievement_id=r.id,
                user_id=user_id,
                achieved_at=now,
            )
        )
        if redis is not None:
            await redis.publish(
                "chat:notification",
                UserAchievementUnlock.init(r, user_id, gamemode).model_dump_json(),
            )
        elif v2_ipc is not None:
            await v2_ipc.send_notice(
                "realtime", "new_notification", UserAchievementUnlock.init(r, user_id, gamemode).to_ipc_dump()
            )
        event = Event(
            created_at=now,
            type=EventType.ACHIEVEMENT,
            user_id=user_id,
            event_payload={
                "achievement": {"slug": r.assets_id, "name": r.name},
                "user": {
                    "username": username,
                    "url": settings.web_url + "users/" + str(user_id),
                },
            },
        )
        session.add(event)
    await session.commit()


async def process_achievements(
    session: AsyncSession,
    score_id: int,
    redis: Redis | None = None,
    v2_ipc: IPCClient | None = None,
):
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

    for k, v in not_achieved.items():
        if v is None:
            continue
        if await v(session, score, score.beatmap):
            result.append(k)
    if result:
        await unlock_achievements(session, result, score.user_id, score.gamemode, redis, v2_ipc)
